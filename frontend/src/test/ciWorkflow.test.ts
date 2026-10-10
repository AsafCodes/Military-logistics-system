import { describe, it, expect } from 'vitest';
import { ciJobSettings, ciStep } from './ciWorkflow';

/**
 * The reader behind the CI pins in lintGate.test.ts, typecheckConfig.test.ts
 * and auditGate.test.ts, tried on workflows the real file does not hold.
 * Those pins are only as good as what this reader can see.
 */

const workflow = (...lines: string[]) => lines.join('\n') + '\n';

const BASE = [
    'on:',
    '  push:',
    '    branches: [ "main" ]',
    'jobs:',
    '  backend:',
    '    runs-on: ubuntu-latest',
    '    steps:',
    '    - name: Run tests',
    '      run: pytest',
    '',
    '  frontend:',
    '    runs-on: ubuntu-latest',
    '',
    '    steps:',
    '    # A comment that belongs to the step below it.',
    '    - name: Typecheck',
    '      run: npm run typecheck',
    '',
    '    # A comment that belongs to the step below it.',
    '    - name: Build',
    '      run: npm run build',
];

const STEP = ['- name: Typecheck', 'run: npm run typecheck'];

// The base with `added` put straight after the line that reads `after`.
function withAfter(after: string, ...added: string[]) {
    const at = BASE.indexOf(after);
    if (at === -1) throw new Error(`no line "${after}" in the base workflow`);
    return workflow(...BASE.slice(0, at + 1), ...added, ...BASE.slice(at + 1));
}

describe('ciStep', () => {
    it('returns the step without the blank lines and comments around it', () => {
        expect(ciStep('Typecheck', workflow(...BASE))).toEqual(STEP);
        expect(ciStep('Build', workflow(...BASE))).toEqual(['- name: Build', 'run: npm run build']);
    });

    it('reads a file with Windows line endings the same way', () => {
        expect(ciStep('Typecheck', BASE.join('\r\n') + '\r\n')).toEqual(STEP);
    });

    it.each([
        ['continue-on-error', '      continue-on-error: true'],
        ['if', '      if: false'],
        ['a working directory', '      working-directory: /tmp'],
    ])('shows %s added to the step', (_what, line) => {
        expect(ciStep('Typecheck', withAfter('      run: npm run typecheck', line))).toEqual([...STEP, line.trim()]);
    });

    it.each([
        ['at the left margin', '# for now'],
        ['at the step indent', '      # for now'],
        ['at the list indent', '    # for now'],
    ])('does not stop at a comment %s', (_where, comment) => {
        // YAML ignores a comment line wherever it is indented, so the lines
        // after it still belong to the step.
        expect(ciStep('Typecheck', withAfter('      run: npm run typecheck', comment, '      continue-on-error: true')))
            .toEqual([...STEP, 'continue-on-error: true']);
    });

    it('does not stop at a blank line', () => {
        expect(ciStep('Typecheck', withAfter('      run: npm run typecheck', '', '      continue-on-error: true')))
            .toEqual([...STEP, 'continue-on-error: true']);
    });

    it('does not stop at a list nested in the step', () => {
        const nested = withAfter(
            '      run: npm run typecheck',
            '      with:',
            '        paths:',
            '        - src',
            '      continue-on-error: true',
        );
        expect(ciStep('Typecheck', nested))
            .toEqual([...STEP, 'with:', 'paths:', '- src', 'continue-on-error: true']);
    });

    it('ends the last step at the next top-level key', () => {
        expect(ciStep('Build', workflow(...BASE, 'env:', '  A: b'))).toEqual(['- name: Build', 'run: npm run build']);
    });

    it('refuses a name that no step has', () => {
        expect(() => ciStep('Lint', workflow(...BASE))).toThrow('found 0');
    });

    it('refuses a name that two steps have, even in different jobs', () => {
        const twice = withAfter('      run: pytest', '    - name: Typecheck', '      run: true');
        expect(() => ciStep('Typecheck', twice)).toThrow('found 2');
    });

    it('does not take a longer name for the one asked for', () => {
        expect(() => ciStep('Type', workflow(...BASE))).toThrow('found 0');
    });
});

describe('ciJobSettings', () => {
    it('returns the settings of the job that holds the step', () => {
        expect(ciJobSettings('Typecheck', workflow(...BASE))).toEqual(['runs-on', 'steps']);
    });

    it('shows a setting written before the steps', () => {
        expect(ciJobSettings('Typecheck', withAfter('  frontend:', '    continue-on-error: true')))
            .toEqual(['continue-on-error', 'runs-on', 'steps']);
    });

    it('shows a setting written after the steps', () => {
        expect(ciJobSettings('Typecheck', workflow(...BASE, '    if: false'))).toEqual(['runs-on', 'steps', 'if']);
    });

    it('does not stop at a comment at the left margin', () => {
        expect(ciJobSettings('Typecheck', workflow(...BASE, '# for now', '    continue-on-error: true')))
            .toEqual(['runs-on', 'steps', 'continue-on-error']);
    });

    it('finds a job whose key carries a comment', () => {
        // Missed, the step would be put in the job before, and that job's
        // settings returned for it.
        const commented = withAfter('  frontend:', '    continue-on-error: true')
            .replace('  frontend:', '  frontend: # the UI');
        expect(ciJobSettings('Typecheck', commented)).toEqual(['continue-on-error', 'runs-on', 'steps']);
    });

    it('shows a setting written with a space before its colon', () => {
        expect(ciJobSettings('Typecheck', withAfter('  frontend:', '    continue-on-error : true')))
            .toEqual(['continue-on-error', 'runs-on', 'steps']);
    });

    it('finds a job whose key has a space before its colon', () => {
        const spaced = withAfter('  frontend:', '    continue-on-error: true')
            .replace('  frontend:', '  frontend :');
        expect(ciJobSettings('Typecheck', spaced)).toEqual(['continue-on-error', 'runs-on', 'steps']);
    });

    it('leaves out the settings of the job before', () => {
        const waivedBackend = withAfter('  backend:', '    continue-on-error: true');
        expect(ciJobSettings('Typecheck', waivedBackend)).toEqual(['runs-on', 'steps']);
        expect(ciJobSettings('Run tests', waivedBackend)).toEqual(['continue-on-error', 'runs-on', 'steps']);
    });

    it('leaves out the settings of the job after', () => {
        const later = workflow(...BASE, '', '  deploy:', '    if: false', '    steps:', '    - name: Ship', '      run: true');
        expect(ciJobSettings('Typecheck', later)).toEqual(['runs-on', 'steps']);
    });

    it('leaves out a key nested inside a step', () => {
        const nested = withAfter('      run: npm run typecheck', '      with:', '        if: x');
        expect(ciJobSettings('Typecheck', nested)).toEqual(['runs-on', 'steps']);
    });
});
