/**
 * Stop hook and PostToolUse hook.
 *
 * Pure helpers get unit tests. The exit contract gets a few end-to-end runs of the
 * real CLI against throwaway git repos whose project sits in a subdirectory.
 */
import { afterAll, describe, expect, setDefaultTimeout, test } from 'bun:test';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import {
  capFindings,
  hookTarget,
  LOOP_GUARD_NOTICE,
  parseDiffRanges,
  stopHookExit,
  stopHookPayload,
  touchedOverLimit,
} from '../harness';

// End-to-end cases spawn biome, lizard, and knip.
setDefaultTimeout(60_000);

const TEMPLATE = join(import.meta.dir, '..');
const roots: string[] = [];

afterAll(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
});

function tempDir(): string {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'stop-hook-')));
  roots.push(root);
  return root;
}

function write(dir: string, relative: string, text: string): void {
  mkdirSync(dirname(join(dir, relative)), { recursive: true });
  writeFileSync(join(dir, relative), text);
}

const spans = (...ranges: [number, number][]) => ranges;

// ── Pure helpers ────────────────────────────────────────────────────

test('parseDiffRanges', () => {
  const diff = [
    'diff --git a/src/app.ts b/src/app.ts',
    '--- a/src/app.ts',
    '+++ b/src/app.ts',
    '@@ -1,0 +2 @@ x', // no count: one line
    '+y',
    '@@ -10,2 +11,3 @@ function f() {',
    '+++ looks like a header but is an added line',
    '@@ -20,4 +22,0 @@', // pure deletion: no range
    'diff --git a/old.ts b/old.ts',
    '--- a/old.ts',
    '+++ /dev/null', // deleted file: not listed
    '@@ -1,2 +0,0 @@',
    'diff --git a/sp ace.ts b/sp ace.ts',
    '--- /dev/null',
    '+++ b/sp ace.ts\t',
    '@@ -0,0 +1,4 @@',
    'diff --git a/only_deleted.ts b/only_deleted.ts',
    '--- a/only_deleted.ts',
    '+++ b/only_deleted.ts',
    '@@ -3 +2,0 @@',
    '',
  ].join('\n');
  expect(parseDiffRanges(diff)).toEqual(
    new Map([
      ['src/app.ts', spans([2, 2], [11, 13])],
      ['sp ace.ts', spans([1, 4])],
      ['only_deleted.ts', spans()],
    ]),
  );
});

test('touchedOverLimit reports each limit a touched function exceeds', () => {
  // `end` is lizard's: the line of the token after the function's closing brace.
  const row = (name: string, start: number, end: number, ccn: number, args = 1) =>
    `3,${ccn},9,${args},${end - start + 1},"${name}@${start}-${end}@src/a.ts",` +
    `"src/a.ts","${name}","${name} ( a , b )",${start},${end}`;
  const csv = [
    row('legacy', 1, 32, 20), // closes on 30; lines 31-32 were appended after it
    row('edited', 40, 62, 16, 9), // closes on 60, touched there
    row('small', 70, 80, 3), // touched, under every limit
    row('long', 90, 200, 1), // touched on its first line
    'garbage',
  ].join('\n');
  const closing = [30, 60, 80, 200];
  const source = Array.from({ length: 200 }, (_, i) => (closing.includes(i + 1) ? '  }' : 'x'));
  const sources = new Map([['src/a.ts', source]]);
  const scope = new Map([['src/a.ts', spans([31, 32], [60, 60], [75, 75], [90, 90])]]);
  expect(touchedOverLimit(csv, scope, sources)).toEqual([
    'src/a.ts:40: edited CCN 16 (limit 15)',
    'src/a.ts:40: edited args 9 (limit 8)',
    'src/a.ts:90: long length 111 (limit 100)',
  ]);
  expect(touchedOverLimit(csv, new Map(), sources)).toEqual([]);
});

test('payload names failed gates and caps findings', () => {
  const lines = stopHookPayload([
    { gate: 'Lint', findings: Array.from({ length: 23 }, (_, n) => `a.ts:${n + 1}: E1 x`) },
    { gate: 'Complexity', findings: [], problem: 'boom' },
    { gate: 'Dead code', findings: ['b.ts:1: unused'] },
  ]).split('\n');
  expect(lines).toHaveLength(22);
  expect(lines[0]).toBe('stop-hook failed: Lint, Dead code');
  expect(lines[20]).toBe('a.ts:20: E1 x');
  expect(lines[21]).toBe('… +4 more — run `bun harness.ts stop-hook --verbose`');

  const twenty = Array.from({ length: 20 }, (_, n) => `a.ts:${n}: x`);
  expect(capFindings(twenty, false)).toEqual(twenty);
  expect(capFindings([...twenty, 'a.ts:21: x'], true)).toHaveLength(21);
  expect(stopHookPayload([{ gate: 'Lint', findings: [], problem: 'boom' }])).toBe('');
});

test('stopHookExit', () => {
  const active = { stop_hook_active: true };
  const cases: [string, number, Record<string, unknown>, number][] = [
    ['', 0, {}, 0],
    ['', 1, {}, 1],
    ['', 0, active, 0],
    ['P', 0, {}, 2],
    ['P', 1, {}, 2], // a crashed gate never hides another gate's findings
    ['P', 0, active, 1], // already blocked once on this stop
    ['P', 1, active, 1],
    ['P', 0, { stop_hook_active: 'true' }, 2],
  ];
  for (const [payload, failed, event, expected] of cases) {
    const got = stopHookExit(payload, failed, event);
    expect([payload, failed, event, got]).toEqual([payload, failed, event, expected]);
  }
});

test('hookTarget resolves project sources only', async () => {
  const root = tempDir();
  for (const path of ['src/app.ts', 'src/data.json', 'docs/tool.ts']) {
    write(root, path, '');
  }
  const cases: [unknown, string | null][] = [
    [join(root, 'src', 'app.ts'), 'src/app.ts'],
    ['src/app.ts', 'src/app.ts'],
    ['src/missing.ts', null],
    ['src/data.json', null],
    ['docs/tool.ts', null],
    ['src', null],
    ['../elsewhere/src/app.ts', null],
    [join(TEMPLATE, 'harness.ts'), null],
    ['', null],
    [3, null],
  ];
  for (const [filePath, expected] of cases) {
    const got = await hookTarget({ tool_input: { file_path: filePath } }, root);
    expect([filePath, got]).toEqual([filePath, expected]);
  }
  expect(await hookTarget({ tool_input: 'src/app.ts' }, root)).toBeNull();
  expect(await hookTarget({}, root)).toBeNull();
});

// ── End to end: the real CLI on throwaway repos ────────────────────

interface Ran {
  code: number;
  stdout: string;
  stderr: string;
}

async function spawn(cmd: string[], cwd: string, stdin = ''): Promise<Ran> {
  // No git hook variables, and no base-ref or guard overrides from the ambient shell.
  const ambient = /^(GIT_|HARNESS_|GITHUB_BASE_REF$)/;
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !ambient.test(k)));
  const proc = Bun.spawn(cmd, {
    cwd,
    env,
    stdin: new Blob([stdin]),
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const [stdout, stderr, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  return { code, stdout, stderr };
}

async function git(root: string, ...args: string[]): Promise<string> {
  const identity = ['-c', 'user.email=h@example.com', '-c', 'user.name=H'];
  const result = await spawn(['git', ...identity, '-c', 'commit.gpgsign=false', ...args], root);
  if (result.code !== 0) throw new Error(`git ${args.join(' ')} failed: ${result.stderr}`);
  return result.stdout;
}

async function commit(root: string): Promise<void> {
  await git(root, 'add', '-A');
  await git(root, 'commit', '-q', '--no-verify', '-m', 'change');
}

/** A committed repo whose project, in `proj/`, runs a copy of this harness. */
async function projectRepo(): Promise<{ root: string; project: string }> {
  const root = tempDir();
  const project = join(root, 'proj');
  mkdirSync(project);
  for (const file of ['harness.ts', 'biome.json', 'tsconfig.json', 'CLAUDE.md']) {
    copyFileSync(join(TEMPLATE, file), join(project, file));
  }
  copyFileSync(join(TEMPLATE, 'CLAUDE.md'), join(project, 'AGENTS.md'));
  symlinkSync(join(TEMPLATE, 'node_modules'), join(project, 'node_modules'));
  write(project, '.gitignore', 'node_modules\n');
  write(project, 'package.json', '{\n  "name": "scratch",\n  "private": true\n}\n');
  write(project, 'knip.json', '{ "entry": ["src/index.ts"], "project": ["src/**/*.ts"] }\n');
  await git(root, 'init', '-q', '-b', 'main');
  await commit(root);
  return { root, project };
}

function harness(project: string, args: string[], stdin = ''): Promise<Ran> {
  return spawn(['bun', 'harness.ts', ...args], project, stdin);
}

/** A function whose CCN is `branches + 1`. */
function branchy(name: string, branches: number): string {
  const body = Array.from(
    { length: branches },
    (_, i) => `  if (value === ${i}) {\n    return ${i};\n  }\n`,
  ).join('');
  return `export function ${name}(value: number): number {\n${body}  return -1;\n}\n`;
}

// Only one case changes a src/ file, so only one runs knip: concurrent `bunx knip`
// runs race while linking their shared install.
describe.concurrent('end to end', () => {
  test('a clean tree is silent', async () => {
    const { project } = await projectRepo();
    expect(await harness(project, ['stop-hook'])).toEqual({ code: 0, stdout: '', stderr: '' });
  });

  test('only what the branch touches blocks, once per stop', async () => {
    const { root, project } = await projectRepo();
    write(
      project,
      'src/index.ts',
      "import { legacy } from './app';\n\nexport const n = legacy(1);\n",
    );
    write(project, 'src/app.ts', branchy('legacy', 20));
    await commit(root);
    await git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD');
    await git(root, 'checkout', '-q', '-b', 'feature');
    // Appended right after legacy, whose lizard span runs into fresh's first line. The
    // trailing const keeps git from sliding the hunk up into legacy's closing lines.
    const tail = 'export const VERSION = 2;\n';
    write(project, 'src/app.ts', `${branchy('legacy', 20)}\n${branchy('fresh', 16)}\n${tail}`);
    await commit(root);
    const first = await harness(project, ['stop-hook']);
    const second = await harness(project, ['stop-hook'], '{"stop_hook_active": true}');

    const payload = [
      'stop-hook failed: Complexity, Dead code',
      'src/app.ts:65: fresh CCN 17 (limit 15)',
      'src/app.ts:65: Unused export: fresh',
      'src/app.ts:117: Unused export: VERSION',
      '',
    ].join('\n');
    expect(first).toEqual({ code: 2, stdout: '', stderr: payload });
    expect(second).toEqual({ code: 1, stdout: '', stderr: `${payload}${LOOP_GUARD_NOTICE}\n` });
  });

  test('post-edit --hook asks for a re-read only after reformatting', async () => {
    const { project } = await projectRepo();
    write(project, 'src/messy.ts', 'export const x=1\n');
    write(project, 'src/clean.ts', 'export const x = 1;\n');
    const hook = (path: string) =>
      harness(
        project,
        ['post-edit', '--hook'],
        JSON.stringify({ tool_input: { file_path: path } }),
      );
    const messy = await hook(join(project, 'src', 'messy.ts'));
    const clean = await hook('src/clean.ts');

    const context = 'harness: reformatted src/messy.ts; re-read it before editing it again';
    const line = {
      hookSpecificOutput: { hookEventName: 'PostToolUse', additionalContext: context },
    };
    expect(messy).toEqual({ code: 0, stdout: `${JSON.stringify(line)}\n`, stderr: '' });
    expect(readFileSync(join(project, 'src', 'messy.ts'), 'utf8')).toBe('export const x = 1;\n');
    expect(clean).toEqual({ code: 0, stdout: '', stderr: '' });
  });

  test('pre-commit stages the AGENTS.md mirror of a staged CLAUDE.md', async () => {
    const { root, project } = await projectRepo();
    write(project, 'CLAUDE.md', 'v2\n');
    await git(root, 'add', 'proj/CLAUDE.md');
    const result = await harness(project, ['pre-commit']);

    expect(result.code).toBe(0);
    expect(readFileSync(join(project, 'AGENTS.md'), 'utf8')).toBe('v2\n');
    expect(await git(root, 'diff', '--cached', '--name-only')).toBe(
      'proj/AGENTS.md\nproj/CLAUDE.md\n',
    );
  });
});
