import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { readLocal, writeLocal, removeLocal } from './safeStorage';

describe('safeStorage', () => {
    it('reads and writes when storage works', () => {
        writeLocal('k', 'v');
        expect(readLocal('k')).toBe('v');
        removeLocal('k');
        expect(readLocal('k')).toBeNull();
    });

    it('returns null instead of throwing when the accessor is denied', () => {
        const real = Object.getOwnPropertyDescriptor(window, 'localStorage');
        Object.defineProperty(window, 'localStorage', {
            configurable: true,
            get() {
                throw new DOMException('The operation is insecure.', 'SecurityError');
            },
        });
        try {
            // All three must be survivable: a caller that has to wrap these in
            // its own try/catch has gained nothing over the bare API.
            expect(readLocal('k')).toBeNull();
            expect(() => writeLocal('k', 'v')).not.toThrow();
            expect(() => removeLocal('k')).not.toThrow();
        } finally {
            if (real) Object.defineProperty(window, 'localStorage', real);
        }
    });
});

/**
 * The convention, enforced.
 *
 * A comment saying "always use safeStorage" is exactly the kind of documented,
 * unenforced rule this codebase's audit keeps finding (SEC-H4's shape: declared,
 * displayed, checked by nothing). A bare `localStorage` in a mount effect takes
 * the application down, so this is a real failure mode rather than a style
 * preference -- it gets a gate, not a guideline.
 */
describe('nothing bypasses safeStorage', () => {
    const SRC = join(__dirname, '..');

    const EXEMPT = new Set([
        // This module IS the wrapper.
        join('lib', 'safeStorage.ts'),
        join('lib', 'safeStorage.test.ts'),
        // Test scaffolding legitimately manipulates storage directly, including
        // making the accessor throw on purpose.
        join('test', 'setup.ts'),
        join('App.test.tsx'),
        join('main.test.tsx'),
        join('lib', 'axios.test.ts'),
        join('services', 'auth.service.test.ts'),
        // DEAD CODE, deliberately exempted rather than fixed. LegacyLogin is an
        // orphaned second login form -- nothing imports it -- that still does
        // `localStorage.setItem('token', ...)`, reinstating the exact defect
        // SEC-H9 removes if anyone ever routes to it. Deleting it belongs to
        // FE-M1, so it is named here instead: an exception someone chose, which
        // disappears the moment that entry lands.
        join('features', 'auth', 'components', 'LegacyLogin.tsx'),
    ]);

    function sourceFiles(dir: string): string[] {
        return readdirSync(dir).flatMap(entry => {
            const full = join(dir, entry);
            if (statSync(full).isDirectory()) {
                // The generated OpenAPI client is not hand-written and is slated
                // for deletion (FE-H2).
                return entry === 'client' ? [] : sourceFiles(full);
            }
            return /\.tsx?$/.test(entry) ? [full] : [];
        });
    }

    /**
     * Blank out comment bodies, preserving line numbering.
     *
     * Necessary, not fussy: the files this rule governs are precisely the ones
     * whose comments EXPLAIN the rule, so a naive text search reports every
     * explanation as a violation. The `[^:]` guard keeps `https://` in a string
     * from being read as a line comment.
     */
    function stripComments(source: string): string {
        return source
            .replace(/\/\*[\s\S]*?\*\//g, block => block.replace(/[^\n]/g, ' '))
            .replace(/(^|[^:])\/\/.*$/gm, '$1');
    }

    it('has no bare localStorage or sessionStorage outside the wrapper', () => {
        const offenders = sourceFiles(SRC)
            .filter(file => !EXEMPT.has(relative(SRC, file)))
            .flatMap(file => {
                const rel = relative(SRC, file).split(sep).join('/');
                return stripComments(readFileSync(file, 'utf8'))
                    .split('\n')
                    .map((line, i) => ({ line, n: i + 1 }))
                    .filter(({ line }) => /\b(localStorage|sessionStorage)\b/.test(line))
                    .map(({ n }) => `${rel}:${n}`);
            });

        expect(offenders,
            'Use readLocal/writeLocal/removeLocal from @/lib/safeStorage. The ' +
            'localStorage accessor itself throws where a browser denies site ' +
            'data, and in a mount effect that unmounts the whole application.',
        ).toEqual([]);
    });
});
