import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import ts from 'typescript';

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
