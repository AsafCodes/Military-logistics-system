import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import ts from 'typescript';

/**
 * FE-H1: the application has exactly one HTTP client, lib/axios.ts.
 *
 * There used to be three ways out to the API: api.ts (no timeout, reloaded the
 * page on any 401 and ignored skipAuthRedirect), lib/axios.ts (10 s timeout,
 * navigates to /login, honours the opt-out), and a bare axios.get on the login
 * page aimed at a hardcoded address. Each new page picked one by habit, so the
 * application had two session-expiry behaviours and two base addresses.
 *
 * This walks src/ and parses every file with the TypeScript compiler rather
 * than a regex, so a multi-line import, a commented-out one, or `refetch()`
 * can't fool it. Outside tests and src/test/:
 *   A. the only file that binds an axios VALUE is lib/axios.ts. Type-only
 *      imports, and the error helpers below, bind nothing that can send.
 *   B. there is exactly one create() call -- on axios, or on a client. An
 *      axios INSTANCE has create() too, and the client it returns inherits
 *      the base address and timeout but none of the interceptors, so
 *      `api.create()` in a page would be a second client with no 401 policy
 *      that never imports axios at all.
 *   C. nothing calls fetch, opens an XMLHttpRequest or EventSource, or sends a
 *      beacon.
 * Everywhere, tests included:
 *   D. nothing imports or vi.mock()s the deleted client or the deleted
 *      ConnectionTest. tsc catches a stale import, but not a stale vi.mock(),
 *      whose string it never resolves.
 *
 * Not caught, by design -- these take deliberate effort, not habit: element
 * access (`window['fetch']`), a transport or create() copied into a variable
 * first, an HTTP library other than axios, and create() on a client that
 * arrives some other way than a direct import of lib/axios -- through a
 * re-export such as a services/ barrel, or as a function parameter.
 */

// Named imports from 'axios' that can't send a request: error classification
// and constants. `Axios` is deliberately absent -- `new Axios(...)` is a client.
const INERT_NAMED_IMPORTS = new Set([
    'isAxiosError', 'AxiosError', 'isCancel', 'CanceledError', 'HttpStatusCode',
]);

const SRC = __dirname;
const DELETED_MODULES = [join(SRC, 'api'), join(SRC, 'components', 'shared', 'ConnectionTest')];
const SHARED_CLIENT = join(SRC, 'lib', 'axios');

interface Scan {
    /** Ways this file binds an axios value, as human-readable descriptions. */
    axiosValues: string[];
    /** `.create(...)` calls on axios or on a client. */
    createCalls: number;
    /** Raw transports: fetch, XMLHttpRequest, EventSource, sendBeacon. */
    transports: string[];
    /** Module specifiers that resolve to a deleted module. */
    staleRefs: string[];
}

function isAxios(node: ts.Node | undefined): boolean {
    return !!node && ts.isStringLiteralLike(node) && node.text === 'axios';
}

/** Where a module specifier points, as an extensionless absolute path. */
function resolveSpecifier(fileName: string, specifier: string): string | null {
    if (specifier.startsWith('@/')) return join(SRC, specifier.slice(2));
    if (specifier.startsWith('.')) return resolve(dirname(fileName), specifier);
    return null;
}

/** The same, with any source extension dropped. */
function targetOf(fileName: string, specifier: string): string | undefined {
    return resolveSpecifier(fileName, specifier)?.replace(/\.(tsx?|jsx?)$/, '');
}

function isStale(fileName: string, specifier: string): boolean {
    const target = targetOf(fileName, specifier);
    return !!target && DELETED_MODULES.includes(target);
}

/** A call's first argument, if it is a string literal. */
function firstStringArg(call: ts.CallExpression): ts.StringLiteralLike | undefined {
    const [arg] = call.arguments;
    return arg && ts.isStringLiteralLike(arg) ? arg : undefined;
}

function scan(fileName: string, text: string): Scan {
    const kind = fileName.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const source = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, kind);
    const result: Scan = { axiosValues: [], createCalls: 0, transports: [], staleRefs: [] };
    // Local names whose .create() makes a client: axios's default or namespace
    // export, the shared client's default export, and any variable holding a
    // client this file created. `axios` itself is always included so a global
    // or re-declared axios is still counted.
    const creators = new Set(['axios']);

    const noteSpecifier = (node: ts.Node | undefined) => {
        if (node && ts.isStringLiteralLike(node) && isStale(fileName, node.text)) {
            result.staleRefs.push(node.text);
        }
    };

    const visit = (node: ts.Node): void => {
        if (ts.isImportDeclaration(node)) {
            noteSpecifier(node.moduleSpecifier);
            const clause = node.importClause;
            if (isAxios(node.moduleSpecifier) && clause && !clause.isTypeOnly) {
                if (clause.name) {
                    result.axiosValues.push(`default import ${clause.name.text}`);
                    creators.add(clause.name.text);
                }
                const bindings = clause.namedBindings;
                if (bindings && ts.isNamespaceImport(bindings)) {
                    result.axiosValues.push(`namespace import ${bindings.name.text}`);
                    creators.add(bindings.name.text);
                } else if (bindings) {
                    for (const element of bindings.elements) {
                        const imported = (element.propertyName ?? element.name).text;
                        if (!element.isTypeOnly && !INERT_NAMED_IMPORTS.has(imported)) {
                            result.axiosValues.push(`named import ${imported}`);
                        }
                    }
                }
            }
            const fromSharedClient = ts.isStringLiteralLike(node.moduleSpecifier)
                && targetOf(fileName, node.moduleSpecifier.text) === SHARED_CLIENT;
            if (fromSharedClient && clause?.name && !clause.isTypeOnly) {
                creators.add(clause.name.text);
            }
        } else if (ts.isVariableDeclaration(node)) {
            // `const client = axios.create()` -- client.create() is a client too.
            const init = node.initializer;
            if (init && ts.isCallExpression(init) && ts.isPropertyAccessExpression(init.expression)
                && init.expression.name.text === 'create' && ts.isIdentifier(init.expression.expression)
                && creators.has(init.expression.expression.text) && ts.isIdentifier(node.name)) {
                creators.add(node.name.text);
            }
        } else if (ts.isExportDeclaration(node)) {
            noteSpecifier(node.moduleSpecifier);
            if (isAxios(node.moduleSpecifier) && !node.isTypeOnly) {
                result.axiosValues.push('re-export from axios');
            }
        } else if (ts.isImportEqualsDeclaration(node)) {
            const ref = node.moduleReference;
            if (ts.isExternalModuleReference(ref)) {
                noteSpecifier(ref.expression);
                if (isAxios(ref.expression)) result.axiosValues.push('import = require(axios)');
            }
        } else if (ts.isCallExpression(node)) {
            const callee = node.expression;
            const arg = firstStringArg(node);
            if (callee.kind === ts.SyntaxKind.ImportKeyword) {
                noteSpecifier(arg);
                if (isAxios(arg)) result.axiosValues.push('dynamic import(axios)');
            } else if (ts.isIdentifier(callee) && callee.text === 'require') {
                noteSpecifier(arg);
                if (isAxios(arg)) result.axiosValues.push('require(axios)');
            } else if (ts.isIdentifier(callee) && callee.text === 'fetch') {
                result.transports.push('fetch()');
            } else if (ts.isPropertyAccessExpression(callee)) {
                const method = callee.name.text;
                const target = callee.expression;
                // vi.mock / vi.doMock / vi.importActual / vi.importMock all take
                // the module path first.
                if (ts.isIdentifier(target) && target.text === 'vi') noteSpecifier(arg);
                if (method === 'create' && ts.isIdentifier(target) && creators.has(target.text)) {
                    result.createCalls += 1;
                }
                if (method === 'fetch' && ts.isIdentifier(target)
                    && ['window', 'globalThis', 'self'].includes(target.text)) {
                    result.transports.push(`${target.text}.fetch()`);
                }
                if (method === 'sendBeacon') result.transports.push('sendBeacon()');
            }
        } else if (ts.isNewExpression(node)) {
            const callee = node.expression;
            const name = ts.isIdentifier(callee) ? callee.text
                : ts.isPropertyAccessExpression(callee) ? callee.name.text : '';
            if (name === 'XMLHttpRequest' || name === 'EventSource') {
                result.transports.push(`new ${name}()`);
            }
        }
        ts.forEachChild(node, visit);
    };
    visit(source);
    return result;
}

function sourceFiles(dir: string): string[] {
    return readdirSync(dir).flatMap(entry => {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) return sourceFiles(full);
        return /\.tsx?$/.test(entry) ? [full] : [];
    });
}

/** Forward-slashed path relative to src/, so expectations read the same on Windows. */
const rel = (file: string) => relative(SRC, file).split(sep).join('/');

// Matches what vite.config.ts's `test.include` runs: *.test.* and *.spec.*.
const isTestCode = (path: string) => /\.(test|spec)\.tsx?$/.test(path) || path.startsWith('test/');

describe('one HTTP client', () => {
    const all = sourceFiles(SRC).map(file => ({ file: rel(file), ...scan(file, readFileSync(file, 'utf8')) }));
    const app = all.filter(entry => !isTestCode(entry.file));

    it('walks the tree it claims to', () => {
        // A walk that found nothing would pass every assertion below.
        const appFiles = app.map(entry => entry.file);
        expect(appFiles).toContain('lib/axios.ts');
        expect(appFiles).toContain('App.tsx');
        expect(appFiles).not.toContain('App.test.tsx');
        expect(all.map(entry => entry.file)).toContain('App.test.tsx');
        // Vitest runs *.spec.* too (vite.config.ts), so those are tests as well.
        expect(isTestCode('features/x/Page.spec.tsx')).toBe(true);
        expect(isTestCode('features/x/Page.tsx')).toBe(false);
    });

    it('A: binds an axios value in lib/axios.ts and nowhere else', () => {
        // Equality, not emptiness: if lib/axios.ts stopped registering, the
        // detector would be broken and this would say so.
        const binders = app.filter(entry => entry.axiosValues.length > 0);
        expect(Object.fromEntries(binders.map(entry => [entry.file, entry.axiosValues])))
            .toEqual({ 'lib/axios.ts': ['default import axios'] });
    });

    it('B: creates exactly one axios instance', () => {
        const creators = app.filter(entry => entry.createCalls > 0);
        expect(creators.map(entry => [entry.file, entry.createCalls])).toEqual([['lib/axios.ts', 1]]);
    });

    it('C: opens no other transport', () => {
        const others = app.filter(entry => entry.transports.length > 0);
        expect(Object.fromEntries(others.map(entry => [entry.file, entry.transports]))).toEqual({});
    });

    it('D: nothing, tests included, imports or mocks a deleted module', () => {
        const stale = all.filter(entry => entry.staleRefs.length > 0);
        expect(Object.fromEntries(stale.map(entry => [entry.file, entry.staleRefs]))).toEqual({});
    });
});

describe('the detector itself', () => {
    // A page somewhere in the tree, so relative specifiers resolve as they would.
    const PAGE = join(SRC, 'features', 'catalog', 'components', 'Page.tsx');
    const values = (code: string) => scan(PAGE, code).axiosValues;

    it.each([
        ['a default import', "import axios from 'axios';"],
        ['a namespace import', "import * as ax from 'axios';"],
        ['a mixed import', "import axios, { isAxiosError } from 'axios';"],
        ['a multi-line named import of Axios', "import {\n    isAxiosError,\n    Axios,\n} from 'axios';"],
        ['a renamed named import of Axios', "import { Axios as Client } from 'axios';"],
        ['a dynamic import', "const m = await import('axios');"],
        ['a require', "const m = require('axios');"],
        ['a re-export', "export { default } from 'axios';"],
        ['a star re-export', "export * from 'axios';"],
    ])('flags %s', (_label, code) => {
        expect(values(code)).not.toEqual([]);
    });

    it.each([
        ['an inert named import', "import { isAxiosError } from 'axios';"],
        ['every inert named import', "import { isAxiosError, AxiosError, isCancel, CanceledError, HttpStatusCode } from 'axios';"],
        ['a type-only import', "import type { AxiosInstance } from 'axios';"],
        ['a type-only default import', "import type axios from 'axios';"],
        ['an inline type specifier', "import { type AxiosInstance } from 'axios';"],
        ['a type-only re-export', "export type { AxiosInstance } from 'axios';"],
        ['a commented-out import', "// import axios from 'axios';\n/* import axios from 'axios'; */"],
        ['an import inside a string', "const s = \"import axios from 'axios'\";"],
        ['another library', "import axios from 'axios-mock';"],
    ])('passes %s', (_label, code) => {
        expect(values(code)).toEqual([]);
    });

    it('counts create() on the shared client, which drops its interceptors', () => {
        expect(scan(PAGE, "import api from '@/lib/axios';\napi.create();").createCalls).toBe(1);
        expect(scan(PAGE, "import http from '../../../lib/axios';\nhttp.create({});").createCalls).toBe(1);
        expect(scan(PAGE, "import axios from 'axios';\nconst c = axios.create();\nc.create();").createCalls).toBe(2);
        // Using the client is not creating one.
        expect(scan(PAGE, "import api from '@/lib/axios';\napi.get('/x');").createCalls).toBe(0);
        // Nor is a module that merely shares the name.
        expect(scan(PAGE, "import api from './axios';\napi.create();").createCalls).toBe(0);
    });

    it('counts create() on whatever name axios was imported as', () => {
        expect(scan(PAGE, "import axios from 'axios';\naxios.create();\naxios.create({});").createCalls).toBe(2);
        expect(scan(PAGE, "import http from 'axios';\nhttp.create();").createCalls).toBe(1);
        expect(scan(PAGE, "import * as ax from 'axios';\nax.create();").createCalls).toBe(1);
        expect(scan(PAGE, 'Object.create(null);\nstore.create();').createCalls).toBe(0);
    });

    it.each([
        ['fetch()', "fetch('/x');"],
        ['window.fetch()', "window.fetch('/x');"],
        ['globalThis.fetch()', "globalThis.fetch('/x');"],
        ['new XMLHttpRequest()', 'const x = new XMLHttpRequest();'],
        ['new window.XMLHttpRequest()', 'const x = new window.XMLHttpRequest();'],
        ['new EventSource()', "const s = new EventSource('/x');"],
        ['navigator.sendBeacon()', "navigator.sendBeacon('/x');"],
    ])('flags the transport %s', (_label, code) => {
        expect(scan(PAGE, code).transports).not.toEqual([]);
    });

    it.each([
        ['refetch()', 'refetch();'],
        ['this.fetchData()', 'this.fetchData();'],
        ['queryClient.fetchQuery()', 'queryClient.fetchQuery({});'],
        ['fetch in a comment', "// fetch('/x');"],
        ['fetch in a string', "const s = \"fetch('/x')\";"],
    ])('passes %s', (_label, code) => {
        expect(scan(PAGE, code).transports).toEqual([]);
    });

    it.each([
        ['an import of @/api', "import api from '@/api';"],
        ['a relative import of the deleted client', "import api from '../../../api';"],
        ['a vi.mock of @/api', "vi.mock('@/api', () => ({}));"],
        ['a vi.doMock of @/api', "vi.doMock('@/api');"],
        ['a vi.mock of ConnectionTest', "vi.mock('@/components/shared/ConnectionTest', () => ({}));"],
        ['a dynamic import of @/api', "const m = await import('@/api');"],
        ['a re-export of @/api', "export { default } from '@/api';"],
    ])('flags %s', (_label, code) => {
        expect(scan(PAGE, code).staleRefs).not.toEqual([]);
    });

    it.each([
        ['the surviving client', "import api from '@/lib/axios';"],
        ['a sibling named api', "import api from './api';"],
        ['a deeper module named api', "import x from '@/features/api';"],
        ['a vi.mock of the surviving client', "vi.mock('@/lib/axios', () => ({}));"],
    ])('passes %s', (_label, code) => {
        expect(scan(PAGE, code).staleRefs).toEqual([]);
    });
});
