import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { ESLint } from 'eslint';
import tseslint from 'typescript-eslint';
import { ciJobSettings, ciStep } from './test/ciWorkflow';

/**
 * FE-H6 / INF-L4: lint is a gate, and this keeps it one.
 *
 * Most react-hooks rules run on the React Compiler's analysis. When the
 * compiler gives up on a component or hook, those rules report nothing for
 * it. The recommended rule set leaves most of the compiler's give-up
 * diagnostics off, so one `try ... finally` used to switch those rules off
 * for a whole function unseen: seventeen such clauses hid ten errors.
 * eslint.config.js now turns four of those diagnostics into errors.
 *
 * The plants below are linted as text through that config, so nothing here
 * is a fixture on disk. Each must be reported by the rule named beside it.
 * The rest pins other ways to switch the gate off quietly: a file the rules
 * do not reach, the script's zero-warning flag, the CI step, and a
 * suppression comment.
 *
 * What none of this can do: `set-state-in-effect` looks only at the calls
 * written directly in an effect. A state call one function down is not
 * reported, with or without this gate.
 */

const ROOT = resolve(__dirname, '..');

// Both spellings are built from two halves so that this file holds no
// suppression comment of its own, in a plant or anywhere else.
const DISABLE = 'eslint-' + 'disable';
const CONFIGURE = 'es' + 'lint';

const GATE_RULES = [
    'react-hooks/todo',
    'react-hooks/invariant',
    'react-hooks/syntax',
    'react-hooks/rule-suppression',
];

const eslint = new ESLint({ cwd: ROOT });

async function lint(lines: string[]) {
    const [result] = await eslint.lintText(lines.join('\n') + '\n', {
        filePath: join(ROOT, 'src', 'planted', 'Planted.tsx'),
    });
    return result.messages;
}

// Errors only: a plant is about the rule that fails it, and one of them
// carries a warning as well.
const errorRules = (messages: { severity: number; ruleId: string | null }[]) =>
    [...new Set(messages.filter(m => m.severity === 2).map(m => m.ruleId ?? '(no rule)'))];

const component = (...body: string[]) => [
    "import { useEffect, useState } from 'react';",
    'export function Planted({ seed }: { seed?: { n?: number } }) {',
    '    const [n, setN] = useState(0);',
    '    useEffect(() => { document.title = String(n); }, [n]);',
    ...body,
    '}',
];

const PLANTS: [name: string, rule: string, lines: string[]][] = [
    ['a `finally` in a handler', 'react-hooks/todo', component(
        "    const go = async () => { try { await fetch('/x'); } finally { setN(seed?.n ?? 1); } };",
        '    return <button onClick={go}>{n}</button>;',
    )],
    ['a `finally` in a custom hook', 'react-hooks/todo', [
        "import { useState } from 'react';",
        'export function usePlanted() {',
        '    const [n, setN] = useState(0);',
        "    const go = async () => { try { await fetch('/x'); } finally { setN(1); } };",
        '    return { n, go };',
        '}',
    ]],
    ['a `??=`', 'react-hooks/todo', component(
        '    const go = () => { let next = seed?.n; next ??= 1; setN(next); };',
        '    return <button onClick={go}>{n}</button>;',
    )],
    ['a ternary inside a `try`', 'react-hooks/todo', component(
        "    const go = async () => { try { const next = seed ? 1 : 2; await fetch('/x'); setN(next); } catch { setN(3); } };",
        '    return <button onClick={go}>{n}</button>;',
    )],
    ['an optional chain inside a `try` in the component body', 'react-hooks/invariant', component(
        '    let shown = 0;',
        '    try { shown = seed?.n ?? 2; } catch { shown = 3; }',
        '    return <button onClick={() => setN(shown)}>{n}</button>;',
    )],
    ['a reassigned `const`', 'react-hooks/syntax', component(
        '    const limit = seed?.n ?? 1;',
        '    const go = () => { limit = 2; setN(limit); };',
        '    return <button onClick={go}>{n}</button>;',
    )],
    // The compiler also skips a function that holds a suppression comment for
    // exhaustive-deps or rules-of-hooks. One for another react-hooks rule
    // does not stop it; the search further down is what catches those.
    ['a suppressed react-hooks rule', 'react-hooks/rule-suppression', component(
        `    // ${DISABLE}-next-line react-hooks/exhaustive-deps`,
        '    useEffect(() => { document.title = String(seed?.n); }, []);',
        '    return <button onClick={() => setN(1)}>{n}</button>;',
    )],
    ['a state update made synchronously in an effect', 'react-hooks/set-state-in-effect', component(
        '    useEffect(() => { setN(seed?.n ?? 1); }, [seed]);',
        '    return <p>{n}</p>;',
    )],
    // The case the gate exists for. The effect is the plant above, an error on
    // its own; the `finally` beside it stops the compiler, so that error is
    // not reported and the give-up is all there is to see.
    ['a `finally` beside a state update in an effect', 'react-hooks/todo', component(
        '    useEffect(() => { setN(seed?.n ?? 1); }, [seed]);',
        "    const go = async () => { try { await fetch('/x'); } finally { setN(2); } };",
        '    return <button onClick={go}>{n}</button>;',
    )],
];

/**
 * A suppression comment silences a rule and nothing reports it. One that
 * names a react-hooks rule, or names no rule and so covers them all, would
 * undo the gate for the lines it covers. So would a block comment that
 * reconfigures a react-hooks rule in place.
 *
 * `rule-suppression` reports only a comment that names exhaustive-deps or
 * rules-of-hooks. This search is wider: it reads every comment in the file,
 * taken from the parser lint itself uses, so a `//` or `/*` inside a string
 * is not mistaken for one.
 */
type Comment = { type: 'Block' | 'Line'; value: string };
// typescript-eslint types its parser only as far as ESLint needs.
const parseForESLint = tseslint.parser.parseForESLint as unknown as (
    text: string,
    options: { filePath: string },
) => { ast: { comments?: Comment[] } };

function suppressions(text: string, filePath = 'snippet.ts'): string[] {
    const { ast } = parseForESLint(text, { filePath });
    return (ast.comments ?? []).flatMap(comment => {
        const body = comment.value.trim();
        const directive = body.match(new RegExp(`^${DISABLE}(-next-line|-line)?(?=\\s|$)`));
        if (directive) {
            // `rule-a, "rule-b" -- why`: the rules end where a description
            // starts, which ESLint takes to be two or more hyphens between
            // whitespace.
            const rules = body.slice(directive[0].length).split(/\s-{2,}\s/)[0]
                .split(',').map(rule => rule.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
            const covers = rules.length === 0 || rules.some(rule => rule.startsWith('react-hooks/'));
            return covers ? [body] : [];
        }
        const configures = comment.type === 'Block'
            && new RegExp(`^${CONFIGURE}\\s`).test(body)
            && body.includes('react-hooks/');
        return configures ? [body] : [];
    });
}

// What ESLint leaves alone here: `node_modules` at any depth and `.git` at
// the root by its own defaults, `dist` at the root by eslint.config.js. Any
// other directory is linted, one whose name starts with a dot included.
const SKIPPED_AT_ROOT = new Set(['dist', '.git']);

function linted(dir: string): string[] {
    return readdirSync(dir).flatMap(entry => {
        if (entry === 'node_modules' || (dir === ROOT && SKIPPED_AT_ROOT.has(entry))) return [];
        const path = join(dir, entry);
        if (statSync(path).isDirectory()) return linted(path);
        return /\.tsx?$/.test(entry) ? [path] : [];
    });
}

const FILES = linted(ROOT);

describe('lint gate (FE-H6, INF-L4)', () => {
    it('reports nothing for a component with none of the constructs', async () => {
        // The scaffold every plant shares, with a `try` that the compiler can
        // follow. If this reported anything, a plant's own rule would not be
        // the only thing it could fail on. No warning either.
        expect(await lint(component(
            "    const go = async () => { try { await fetch('/x'); setN(1); } catch { setN(3); } };",
            '    return <button onClick={go}>{seed?.n ?? n}</button>;',
        ))).toEqual([]);
    });

    it.each(PLANTS)('fails on %s', async (_name, rule, lines) => {
        // This rule and no other error, so a plant cannot pass on an
        // unrelated one.
        expect(errorRules(await lint(lines))).toEqual([rule]);
    });

    it('walks the files lint reads', () => {
        // A walk that found nothing would pass every per-file check below.
        const names = FILES.map(f => relative(ROOT, f).replace(/\\/g, '/'));
        expect(names).toContain('src/App.tsx');
        expect(names).toContain('src/lintGate.test.ts');
        expect(names).toContain('vite.config.globals.d.ts');
    });

    it('applies the gate rules to every one of those files', async () => {
        // The plants are linted at one path. An ignore pattern, or an
        // override that turns a rule off for some folder, would leave them
        // passing while the files it covers went unchecked.
        const rules = [...GATE_RULES, 'react-hooks/set-state-in-effect'];
        const exempt: string[] = [];
        for (const file of FILES) {
            const config = await eslint.isPathIgnored(file) ? undefined : await eslint.calculateConfigForFile(file);
            const off = rules.filter(rule => config?.rules?.[rule]?.[0] !== 2);
            if (off.length > 0) exempt.push(`${relative(ROOT, file)}: ${off.join(', ')}`);
        }
        expect(exempt).toEqual([]);
    });

    it('runs the lint script with a zero-warning limit', () => {
        // A missing effect dependency and an unused suppression comment are
        // warnings, and ESLint exits 0 on warnings unless told otherwise.
        const { scripts } = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));
        // The whole script: `|| true` after the flag would undo it.
        expect(scripts.lint).toBe('eslint . --max-warnings 0');
    });

    it('has no waiver on the CI lint step or on its job', () => {
        // The whole step, so that `continue-on-error`, an `if:`, or a command
        // that bypasses the script's flag each fail this.
        expect(ciStep('Lint with ESLint')).toEqual(['- name: Lint with ESLint', 'run: npm run lint']);

        const settings = ciJobSettings('Lint with ESLint');
        expect(settings).toContain('steps');
        expect(settings).not.toContain('continue-on-error');
        expect(settings).not.toContain('if');
    });
});

describe('lint suppressions (FE-H6, INF-L4)', () => {
    it.each([
        [`// ${DISABLE}-next-line react-hooks/set-state-in-effect`],
        [`foo(); // ${DISABLE}-line react-hooks/todo -- just this once`],
        [`/* ${DISABLE} @typescript-eslint/no-explicit-any, react-hooks/refs */`],
        [`/* ${DISABLE} @typescript-eslint/no-explicit-any,\n   react-hooks/refs */`],
        [`// ${DISABLE}-next-line "react-hooks/set-state-in-effect"`],
        [`/* ${DISABLE} */`],
        [`// ${DISABLE}-next-line`],
        [`// ${DISABLE}-next-line -- no rule named`],
        [`// ${DISABLE}-next-line --- no rule named`],
        [`// ${DISABLE}-next-line\t--\tno rule named`],
        [`const path = '/*'; /* ${DISABLE} react-hooks/todo */`],
        [`const url = 'http://x'; /* ${DISABLE} react-hooks/todo */`],
        [`/* ${CONFIGURE} react-hooks/todo: "off" */`],
        [`/* ${CONFIGURE} no-restricted-syntax: ["error", "a > *"], react-hooks/todo: "off" */`],
    ])('counts %s', text => {
        expect(suppressions(text)).toHaveLength(1);
    });

    it.each([
        [`// ${DISABLE}-next-line @typescript-eslint/no-empty-object-type -- deliberate`],
        [`/* ${DISABLE} no-console, @typescript-eslint/no-explicit-any */`],
        [`// ${DISABLE}-next-line no-console -- not react-hooks/todo`],
        [`/* ${CONFIGURE} no-console: "off" */`],
        ["const rule = 'react-hooks/todo';"],
        [`const text = '// ${DISABLE}-next-line react-hooks/todo';`],
    ])('allows %s', text => {
        expect(suppressions(text)).toEqual([]);
    });

    it('finds none in the files lint reads', () => {
        const offenders = FILES.flatMap(file =>
            suppressions(readFileSync(file, 'utf8'), file).map(s => `${relative(ROOT, file)}: ${s}`),
        );
        expect(offenders).toEqual([]);
    });
});
