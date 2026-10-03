import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

/**
 * API-H2: the generated client stays deleted.
 *
 * src/client/ was openapi-typescript-codegen output built from a specification
 * nobody regenerated, and its models contradicted the real API in eight ways.
 * Its only importer was main.tsx setting a base URL nothing read. The production
 * bundle never carried it -- Rollup drops a property write nobody reads -- but
 * the type-checker compiled all of it, and the dev server loaded it on every
 * boot, including a form-encoding library that resolved only as an undeclared
 * transitive dependency (FE-H2).
 *
 * The tree, the `generate-client` script and the generator itself all went in
 * the same change. This pins the output rather than the path: a regenerated
 * client is caught by the banner every generated file starts with, anywhere
 * under src/.
 */
describe('no generated API client in the source tree', () => {
    const SRC = __dirname;
    const BANNER = 'generated using openapi-typescript-codegen';
    // This file has to name the banner to look for it.
    const SELF = 'noGeneratedClient.test.ts';

    function sourceFiles(dir: string): string[] {
        return readdirSync(dir).flatMap(entry => {
            const full = join(dir, entry);
            if (statSync(full).isDirectory()) {
                return sourceFiles(full);
            }
            return /\.tsx?$/.test(entry) ? [full] : [];
        });
    }

    it('finds no file carrying the generator banner', () => {
        const files = sourceFiles(SRC);
        // A walk that found nothing would pass this test while checking nothing.
        expect(files.map(file => relative(SRC, file))).toContain('main.tsx');

        const generated = files
            .filter(file => relative(SRC, file) !== SELF)
            .filter(file => readFileSync(file, 'utf8').includes(BANNER))
            .map(file => relative(SRC, file));

        expect(generated).toEqual([]);
    });
});
