import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Reads .github/workflows/ci.yml for the tests that keep a CI step a gate
 * (lintGate.test.ts, typecheckConfig.test.ts, auditGate.test.ts).
 *
 * Read as text: this project declares no YAML parser. A missing file throws,
 * which is a failure and not a skip. What follows knows the block style the
 * workflow is written in, with plain keys. A quoted key, a flow mapping in
 * braces, or a job key followed by anything but a comment (an anchor, a tag)
 * is not understood.
 *
 * Both functions read the real workflow unless they are handed one, which is
 * how ciWorkflow.test.ts tries them on text the real file does not hold.
 */

const WORKFLOW = resolve(__dirname, '..', '..', '..', '.github', 'workflows', 'ci.yml');

const readWorkflow = () => readFileSync(WORKFLOW, 'utf8');

// Blank lines and comment lines go first. Outside a block scalar, which the
// pinned steps do not have, YAML ignores them wherever they are indented, so
// one of them must not look like the end of a step or of a job.
const meaningful = (workflow: string) => workflow
    .split(/\r?\n/)
    .filter(line => line.trim() !== '' && !line.trim().startsWith('#'));

function stepStart(lines: string[], name: string): number {
    const starts = lines.flatMap((line, i) => (line.trim() === `- name: ${name}` ? [i] : []));
    if (starts.length !== 1) {
        throw new Error(`expected one CI step named "${name}", found ${starts.length}`);
    }
    return starts[0];
}

const indent = (line: string) => line.search(/\S/);

/**
 * The step's lines: every line indented deeper than the dash that opens it,
 * so that `continue-on-error`, an `if:`, or a command other than the expected
 * one each show up in the result. The next line indented no deeper than that
 * dash ends it: the next step, a job setting, or anything further out. A list
 * nested inside the step does not.
 */
export function ciStep(name: string, workflow = readWorkflow()): string[] {
    const lines = meaningful(workflow);
    const start = stepStart(lines, name);
    const after = lines.slice(start + 1);
    const next = after.findIndex(line => indent(line) <= indent(lines[start]));
    return [lines[start], ...(next === -1 ? after : after.slice(0, next))].map(line => line.trim());
}

/**
 * The settings of the job the step belongs to. A job is a key at two spaces
 * with nothing after it but a comment, and its own settings are the keys at
 * four, before or after its steps. The job ends at the next line indented by
 * two spaces or fewer. A key may have spaces before its colon.
 */
export function ciJobSettings(stepName: string, workflow = readWorkflow()): string[] {
    const lines = meaningful(workflow);
    const start = stepStart(lines, stepName);
    const job = lines.slice(0, start).map(line => /^ {2}[\w-]+\s*:\s*(#.*)?$/.test(line)).lastIndexOf(true);
    if (job === -1) throw new Error(`CI step "${stepName}" belongs to no job`);
    const rest = lines.slice(job + 1);
    const end = rest.findIndex(line => /^ {0,2}\S/.test(line));
    return (end === -1 ? rest : rest.slice(0, end))
        .filter(line => /^ {4}[\w-]+\s*:/.test(line))
        .map(line => line.split(':')[0].trim());
}
