import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import ts from 'typescript';
import { ciJobSettings, ciStep } from './test/ciWorkflow';

/**
 * INF-H9: the typecheck reads declaration files too.
 *
 * `skipLibCheck: true` skips every .d.ts, not just node_modules, so a broken
 * src/r3f.d.ts (FE-H3) and seventeen errors in hls.js's declarations all
 * passed `tsc -b` unseen. Both configs now set it to false; this keeps them
 * there.
 *
 * The configs are read with the compiler's own parser, which handles their
 * comments and any `extends` chain -- a JSON.parse would throw on the first,
 * and a raw-text check would miss the flag arriving through the second. The
 * projects are taken from tsconfig.json's references, so a third one added
 * later is held to the same rule.
 *
 * The second block, further down, is FE-H7 and carries its own note.
 */

const ROOT = resolve(__dirname, '..');

function parse(configPath: string): ts.ParsedCommandLine {
    const parsed = ts.getParsedCommandLineOfConfigFile(configPath, {}, {
        ...ts.sys,
        onUnRecoverableConfigFileDiagnostic: d => {
            throw new Error(ts.flattenDiagnosticMessageText(d.messageText, '\n'));
        },
    });
    if (!parsed) throw new Error(`could not read ${configPath}`);
    return parsed;
}

const projects = (parse(join(ROOT, 'tsconfig.json')).projectReferences ?? []).map(ref =>
    ts.resolveProjectReferencePath(ref),
);

// Dependencies and build output are not this project's sources.
const NOT_SOURCES = new Set(['node_modules', 'dist']);

function sources(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
        if (dir === ROOT && NOT_SOURCES.has(entry.name)) return [];
        const path = join(dir, entry.name);
        if (entry.isDirectory()) return sources(path);
        return /\.[cm]?tsx?$/.test(entry.name) ? [path] : [];
    });
}

const FILES = sources(ROOT);
const name = (file: string) => relative(ROOT, file).replace(/\\/g, '/');

describe('typecheck configuration (INF-H9)', () => {
    it('finds the projects tsc -b builds', () => {
        // Without this the it.each below could pass by running zero times.
        expect(projects.map(p => relative(ROOT, p)).sort()).toEqual(
            ['tsconfig.app.json', 'tsconfig.node.json'],
        );
    });

    it.each(projects)('%s sets skipLibCheck to false explicitly', project => {
        // Explicit, not merely absent: absent means false today, but the
        // explicit value is what a reader of the file sees and what this pins.
        expect(parse(project).options.skipLibCheck).toBe(false);
    });

    it('declares Worker in the node config as a type with no members and no value', () => {
        // vite's declarations need the name; vite.config.ts runs in Node and
        // must not see a `Worker` it could construct (see the file's header).
        const shim = join(ROOT, 'vite.config.globals.d.ts');
        const nodeFiles = parse(join(ROOT, 'tsconfig.node.json')).fileNames.map(f => relative(ROOT, f));
        expect(nodeFiles).toContain('vite.config.globals.d.ts');

        const source = ts.createSourceFile(shim, readFileSync(shim, 'utf8'), ts.ScriptTarget.Latest);
        // Members can also arrive through `extends`, so heritage counts as well.
        const declared = source.statements.map(s =>
            ts.isInterfaceDeclaration(s)
                ? `interface ${s.name.text} (${s.members.length} members, ${s.heritageClauses?.length ?? 0} extends)`
                : ts.SyntaxKind[s.kind],
        );
        expect(declared).toEqual(['interface Worker (0 members, 0 extends)']);

        // Triple-slash directives are not statements, and any of them could
        // bring the DOM back in: `/// <reference lib="dom" />` does it in one line.
        expect({
            lib: source.libReferenceDirectives.map(d => d.fileName),
            types: source.typeReferenceDirectives.map(d => d.fileName),
            path: source.referencedFiles.map(d => d.fileName),
        }).toEqual({ lib: [], types: [], path: [] });
    });
});

/**
 * FE-H7: the typecheck is a script of its own and a CI step of its own.
 *
 * It used to exist only as the first half of `build`. `npm run typecheck` is
 * now `tsc -b`, `build` calls that script, and CI runs it as a named step
 * before the build.
 */
describe('typecheck gate (FE-H7)', () => {
    const { scripts } = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));

    it('runs the typecheck script as a project build', () => {
        // The whole script. tsconfig.json lists no files of its own, only the
        // two references, so `tsc` without `-b` has nothing to check and
        // passes; and `|| true` after the command would undo it.
        expect(scripts.typecheck).toBe('tsc -b');
    });

    it('typechecks before it bundles', () => {
        // Through the script, so the two cannot drift apart.
        expect(scripts.build).toBe('npm run typecheck && vite build');
    });

    it.each(projects)('%s sets noEmit', project => {
        // No JavaScript, declarations or source maps come out of the check.
        // tsc still writes its .tsbuildinfo, under node_modules/.tmp.
        expect(parse(project).options.noEmit).toBe(true);
    });

    it.each(projects)('%s checks types', project => {
        // `noCheck` leaves `tsc -b` running and reporting no type errors.
        expect(parse(project).options.noCheck).not.toBe(true);
    });

    it('checks every TypeScript file in the frontend', () => {
        // An `exclude`, a narrower `include`, or a file that no project
        // lists would take files out of the check and leave it green.
        const checked = new Set(projects.flatMap(project => parse(project).fileNames.map(name)));
        const names = FILES.map(name);
        // A walk that found nothing would pass the check below, and the one
        // in the next test.
        expect(names).toContain('src/App.tsx');
        expect(names).toContain('src/typecheckConfig.test.ts');
        expect(names).toContain('vite.config.ts');
        expect(names.filter(file => !checked.has(file))).toEqual([]);
    });

    it('has no file that opts out of the check', () => {
        // The directive switches the checker off for the file it opens.
        // Looked for as text anywhere in a file, so a mention in a string or
        // further down counts as well. Built from two halves so that this
        // file does not hold it.
        const directive = '@ts-' + 'nocheck';
        expect(FILES.filter(file => readFileSync(file, 'utf8').includes(directive)).map(name)).toEqual([]);
    });

    it.each([
        ['Typecheck', 'npm run typecheck'],
        ['Build', 'npm run build'],
    ])('has no waiver on the CI %s step or on its job', (name, command) => {
        // The whole step, so that `continue-on-error`, an `if:`, or a command
        // that goes around the script each fail this.
        expect(ciStep(name)).toEqual([`- name: ${name}`, `run: ${command}`]);

        const settings = ciJobSettings(name);
        expect(settings).toContain('steps');
        expect(settings).not.toContain('continue-on-error');
        expect(settings).not.toContain('if');
    });
});
