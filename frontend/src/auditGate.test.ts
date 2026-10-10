import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import ts from 'typescript';
import { ciJobSettings, ciStep } from './test/ciWorkflow';

/**
 * FE-H8: CI audits the runtime dependencies.
 *
 * `npm run audit:prod` is `npm audit --omit=dev --audit-level=high`: it reads
 * the advisories for `dependencies` and everything they pull in, and fails on
 * a high or a critical one. CI runs it as a named step.
 *
 * It does not cover `devDependencies`, and it lets moderate and low advisories
 * through. What the build tools themselves put in the bundle, the stylesheet
 * Tailwind writes and the loader code Vite adds, is therefore not covered.
 *
 * So the gate is only as good as the split between the two lists. The tests
 * after the first two hold that split from both sides.
 *
 * Nothing here calls the registry. These tests pin the configuration; the CI
 * step does the audit.
 */

const ROOT = resolve(__dirname, '..');
const SRC = join(ROOT, 'src');

const manifest = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));
const runtime = Object.keys(manifest.dependencies);

// Test files, and the helpers only they import, are not in the bundle.
const isTestCode = (path: string) =>
    /\.(test|spec|typecheck)\.tsx?$/.test(path) || path.startsWith('test/');

function modules(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
        const path = join(dir, entry.name);
        if (entry.isDirectory()) return modules(path);
        return /\.tsx?$/.test(entry.name) ? [path] : [];
    });
}

// 'three/src/math/MathUtils' -> 'three'; '@radix-ui/react-slot' stays whole.
const packageOf = (specifier: string) =>
    specifier.split('/').slice(0, specifier.startsWith('@') ? 2 : 1).join('/');

// Relative paths and the '@/' alias point back into src.
const isPackage = (specifier: string) => !/^(\.|@\/|node:)/.test(specifier);

describe('production audit gate (FE-H8)', () => {
    it('audits the runtime dependencies and fails on a high advisory', () => {
        // The whole script. Without `--omit=dev` it would also fail on build
        // tools, which this gate was not asked to cover; `--audit-level=critical`
        // would let a high through; and `|| true` after it would undo it.
        expect(manifest.scripts['audit:prod']).toBe('npm audit --omit=dev --audit-level=high');
    });

    it('runs in CI as a step nothing waives', () => {
        const name = 'Audit production dependencies';
        // The whole step, so that `continue-on-error`, an `if:`, or a command
        // other than the script each fail here.
        expect(ciStep(name)).toEqual([`- name: ${name}`, 'run: npm run audit:prod']);

        const settings = ciJobSettings(name);
        expect(settings).toContain('runs-on');
        expect(settings).not.toContain('continue-on-error');
        expect(settings).not.toContain('if');
    });

    it.each([
        'tailwindcss',
        'tailwindcss-animate',
        'postcss',
        'autoprefixer',
        'vite',
        'vitest',
    ])('keeps the build tool %s out of the runtime list', tool => {
        // A build tool listed under `dependencies` brings its whole tree into
        // the audit. When this was written that failed the step for three of
        // the six: tailwindcss and vitest, where the fix npm offers is a new
        // major version, and tailwindcss-animate, where it offers none. The
        // other three passed, and could fail it some later day for code that
        // is not in the bundle.
        expect(runtime).not.toContain(tool);
        expect(Object.keys(manifest.devDependencies)).toContain(tool);
    });

    it('lists the packages the application imports under dependencies', () => {
        // The other side: a package the bundle imports, moved to
        // `devDependencies`, would leave the audit without a word.
        //
        // Read from the .ts and .tsx files under src: `import` and `export`
        // statements, `require`, and `import()` of a string. Not seen: an
        // `import()` of anything else, a package named only in a URL, a
        // stylesheet or index.html. A type-only import counts like any other,
        // so one from a dev package fails here although it adds no code.
        const imports = modules(SRC)
            .map(file => relative(SRC, file).replace(/\\/g, '/'))
            .filter(file => !isTestCode(file))
            .flatMap(file => ts
                .preProcessFile(readFileSync(join(SRC, file), 'utf8'), true, true)
                .importedFiles
                .map(imported => imported.fileName)
                .filter(isPackage)
                .map(specifier => ({ file, name: packageOf(specifier) })));

        // A scan that read nothing would report no stray package either.
        expect(imports).toContainEqual({ file: 'main.tsx', name: 'react-dom' });
        expect(imports).toContainEqual({ file: 'lib/axios.ts', name: 'axios' });

        const strays = imports
            .filter(({ name }) => !runtime.includes(name))
            .map(({ file, name }) => `${file} imports ${name}`);
        expect(strays).toEqual([]);
    });
});
