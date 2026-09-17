/**
 * Stop hook, PostToolUse hook, and the hook-adjacent pre-commit/pre-push rules.
 *
 * Pure helpers get unit tests. The exit contract gets a few end-to-end runs of the
 * real CLI against throwaway git repos: silent 0, exit 2 with a stderr payload, exit 1
 * for a tool failure or a repeated block.
 */
import { afterAll, describe, expect, setDefaultTimeout, test } from 'bun:test';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import fc from 'fast-check';
import {
  biomeFindings,
  CLAUDE_POST_EDIT_HOOK,
  CLAUDE_STOP_HOOK,
  capFindings,
  checkStopHooksPresent,
  complexityDelta,
  type FunctionMetrics,
  HOOK_WIRINGS,
  hookTarget,
  installHook,
  knipFindings,
  LOOP_GUARD_NOTICE,
  loopGuardKey,
  parseDiffRanges,
  parseHookEvent,
  parseLizardCsv,
  payloadDigest,
  runTool,
  splitCsvRow,
  stopHookExit,
  stopHookPayload,
  ToolError,
  WHOLE_FILE,
} from '../harness';

// End-to-end cases spawn biome, lizard, and knip.
setDefaultTimeout(60_000);

const TEMPLATE = join(import.meta.dir, '..');

/** No git hook variables and no base or guard overrides from the ambient shell. */
function childEnv(extra: Record<string, string> = {}): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    const ambient = key.startsWith('GIT_') || key.startsWith('HARNESS_');
    if (value !== undefined && !ambient && key !== 'GITHUB_BASE_REF') env[key] = value;
  }
  return { ...env, ...extra };
}

interface Ran {
  code: number;
  stdout: string;
  stderr: string;
}

async function spawn(
  cmd: string[],
  cwd: string,
  opts: { stdin?: string; env?: Record<string, string> } = {},
): Promise<Ran> {
  const proc = Bun.spawn(cmd, {
    cwd,
    stdin: new Blob([opts.stdin ?? '']),
    stdout: 'pipe',
    stderr: 'pipe',
    env: childEnv(opts.env),
  });
  const [stdout, stderr, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  return { code, stdout, stderr };
}

async function git(root: string, ...args: string[]): Promise<string> {
  const identity = ['-c', 'user.email=harness@example.com', '-c', 'user.name=Harness'];
  const cmd = ['git', '-C', root, ...identity, '-c', 'commit.gpgsign=false', ...args];
  const result = await spawn(cmd, root);
  if (result.code !== 0) throw new Error(`git ${args.join(' ')} failed: ${result.stderr}`);
  return result.stdout;
}

function harness(project: string, args: string[], stdin = '', env = {}): Promise<Ran> {
  return spawn(['bun', 'harness.ts', ...args], project, { stdin, env });
}

function write(dir: string, relative: string, text: string): void {
  const path = join(dir, relative);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, text);
}

function read(dir: string, relative: string): string {
  return readFileSync(join(dir, relative), 'utf8');
}

/** A function whose CCN is `branches + 1`. */
function branchy(name: string, branches: number): string {
  const body = Array.from(
    { length: branches },
    (_, i) => `  if (value === ${i}) {\n    return ${i};\n  }\n`,
  ).join('');
  return `export function ${name}(value: number): number {\n${body}  return -1;\n}\n`;
}

/** A biome-formatted module of exported blocks. */
function module(...blocks: string[]): string {
  return blocks.join('\n');
}

const HELPER = 'export function helper(): number {\n  return 1;\n}\n';

// Stop-hook fixtures live under tests/, a lint and complexity target that does not
// start knip: concurrent `bunx knip` runs race while linking their shared install.
// One case exercises knip on its own.
const APP = 'tests/app.ts';

// End-to-end groups run concurrently, so temp repos are removed once, at the end.
const roots: string[] = [];

afterAll(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
});

/** A git repo whose project (optionally in `subdir`) runs a copy of this harness. */
async function projectRepo(subdir = ''): Promise<{ root: string; project: string }> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'stop-hook-')));
  roots.push(root);
  await git(root, 'init', '-q', '-b', 'main');
  const project = join(root, subdir);
  mkdirSync(join(project, 'src'), { recursive: true });
  copyFileSync(join(TEMPLATE, 'harness.ts'), join(project, 'harness.ts'));
  for (const config of ['biome.json', 'tsconfig.json', 'CLAUDE.md']) {
    copyFileSync(join(TEMPLATE, config), join(project, config));
  }
  copyFileSync(join(TEMPLATE, 'CLAUDE.md'), join(project, 'AGENTS.md'));
  symlinkSync(join(TEMPLATE, 'node_modules'), join(project, 'node_modules'));
  write(project, '.gitignore', 'node_modules\n');
  write(project, 'package.json', '{\n  "name": "scratch",\n  "private": true\n}\n');
  write(project, 'knip.json', '{ "entry": ["src/index.ts"], "project": ["src/**/*.ts"] }\n');
  return { root, project };
}

async function commit(root: string, message = 'change'): Promise<void> {
  await git(root, 'add', '-A');
  await git(root, 'commit', '-q', '--no-verify', '-m', message);
}

/** Commit `baseText` as APP on main, mark it origin/main, branch off. */
async function featureBranch(root: string, project: string, baseText: string): Promise<void> {
  write(project, APP, baseText);
  await commit(root, 'base');
  await git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD');
  await git(root, 'checkout', '-q', '-b', 'feature');
}

// ── Pure helpers ────────────────────────────────────────────────────

describe('parseDiffRanges', () => {
  const DIFF = [
    'diff --git a/src/app.ts b/src/app.ts',
    'index 1..2 100644',
    '--- a/src/app.ts',
    '+++ b/src/app.ts',
    '@@ -1,0 +2 @@ x',
    '+y',
    '@@ -10,2 +11,3 @@ function f() {',
    '+++ looks like a header but is an added line',
    '+b',
    '+c',
    '@@ -20,4 +22,0 @@',
    '-gone',
    'diff --git a/old.ts b/old.ts',
    'deleted file mode 100644',
    '--- a/old.ts',
    '+++ /dev/null',
    '@@ -1,2 +0,0 @@',
    '-x',
    'diff --git a/sp ace.ts b/sp ace.ts',
    'new file mode 100644',
    '--- /dev/null',
    '+++ b/sp ace.ts\t',
    '@@ -0,0 +1,4 @@',
    '+a',
    'diff --git a/only_deleted.ts b/only_deleted.ts',
    '--- a/only_deleted.ts',
    '+++ b/only_deleted.ts',
    '@@ -3 +2,0 @@',
    '-x',
    '',
  ].join('\n');

  test('parses ranges per file', () => {
    expect(parseDiffRanges(DIFF)).toEqual(
      new Map([
        [
          'src/app.ts',
          [
            [2, 2],
            [11, 13],
          ],
        ],
        ['sp ace.ts', [[1, 4]]],
        ['only_deleted.ts', []],
      ]),
    );
  });

  test('hunk headers round trip', () => {
    const hunk = fc.tuple(fc.integer({ min: 1, max: 10_000 }), fc.integer({ min: 0, max: 50 }));
    fc.assert(
      fc.property(fc.array(hunk, { maxLength: 20 }), (hunks) => {
        const body = hunks.map(([start, count]) => `@@ -1 +${start},${count} @@\n`).join('');
        const diff = `diff --git a/f.ts b/f.ts\n--- a/f.ts\n+++ b/f.ts\n${body}`;
        const expected = hunks
          .filter(([, count]) => count > 0)
          .map(([start, count]): [number, number] => [start, start + count - 1]);
        expect(parseDiffRanges(diff)).toEqual(new Map([['f.ts', expected]]));
      }),
    );
  });
});

describe('lizard CSV', () => {
  const CSV = [
    'NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end',
    '3,1,66,3,4,"run@97-100@src/a.ts","src/a.ts","run","run ( a : Map < string , number > , b )",97,100',
    '3,1,12,1,3,"ok@4-6@src/a.ts","src/a.ts","ok","ok ( a )",4,6',
    '3,2,12,1,3,"ok@9-11@src/a.ts","src/a.ts","ok","ok ( a )",9,11',
    'garbage',
  ].join('\n');

  test('keys by long_name and numbers repeats', () => {
    const parsed = parseLizardCsv(CSV).get('src/a.ts');
    const long = 'run ( a : Map < string , number > , b )';
    expect([...(parsed?.keys() ?? [])]).toEqual([long, 'ok ( a )', 'ok ( a )#2']);
    expect(parsed?.get(long)).toEqual({ name: 'run', line: 97, ccn: 1, args: 3, length: 4 });
    expect(parsed?.get('ok ( a )#2')?.ccn).toBe(2);
  });

  test('splits quoted cells', () => {
    expect(splitCsvRow('1,"a, b","say ""hi""",')).toEqual(['1', 'a, b', 'say "hi"', '']);
  });
});

function fn(overrides: Partial<FunctionMetrics> = {}): FunctionMetrics {
  return { name: 'busy', line: 1, ccn: 1, args: 1, length: 5, ...overrides };
}

describe('complexityDelta', () => {
  function delta(now: FunctionMetrics, was?: FunctionMetrics, nowKey = 'busy ( v )'): string[] {
    const base = was ? new Map([['a.ts', new Map([['busy ( v )', was]])]]) : new Map();
    return complexityDelta(new Map([['a.ts', new Map([[nowKey, now]])]]), base);
  }

  test('new function over the limit', () => {
    expect(delta(fn({ ccn: 17, line: 3 }))).toEqual(['a.ts:3: busy CCN new→17 (limit 15)']);
  });

  test('worse than base', () => {
    expect(delta(fn({ ccn: 17 }), fn({ ccn: 14 }))).toEqual(['a.ts:1: busy CCN 14→17 (limit 15)']);
    expect(delta(fn({ ccn: 21 }), fn({ ccn: 20 }))).toEqual(['a.ts:1: busy CCN 20→21 (limit 15)']);
  });

  test('unchanged, better, or under the limit pass', () => {
    expect(delta(fn({ ccn: 20 }), fn({ ccn: 20 }))).toEqual([]);
    expect(delta(fn({ ccn: 18 }), fn({ ccn: 20 }))).toEqual([]);
    expect(delta(fn({ ccn: 15 }))).toEqual([]);
  });

  test('args and length limits', () => {
    expect(delta(fn({ args: 9, length: 101 }))).toEqual([
      'a.ts:1: busy args new→9 (limit 8)',
      'a.ts:1: busy length new→101 (limit 100)',
    ]);
  });

  test('a signature edit falls back to the unique name', () => {
    expect(delta(fn({ ccn: 20 }), fn({ ccn: 20 }), 'busy ( v : number )')).toEqual([]);
  });

  test('an ambiguous name counts as new', () => {
    const current = new Map([
      [
        'a.ts',
        new Map([
          ['busy ( v : number )', fn({ ccn: 20 })],
          ['busy ( )', fn({ ccn: 2 })],
        ]),
      ],
    ]);
    const base = new Map([['a.ts', new Map([['busy ( v )', fn({ ccn: 20 })]])]]);
    expect(complexityDelta(current, base)).toEqual(['a.ts:1: busy CCN new→20 (limit 15)']);
  });
});

const SCOPE = new Map([
  ['src/a.ts', [[2, 3]] as [number, number][]],
  ['src/new.ts', [...WHOLE_FILE]],
]);

describe('tool output filters', () => {
  function diagnostic(path: string, line: number, severity = 'error', category = 'lint/x') {
    return { severity, category, message: `m${line}`, location: { path, start: { line } } };
  }

  test('biome findings keep blocking diagnostics on changed lines', () => {
    const report = JSON.stringify({
      diagnostics: [
        diagnostic('src/a.ts', 3),
        diagnostic('src/a.ts', 9),
        diagnostic('src/a.ts', 2, 'warning'),
        diagnostic('./src/new.ts', 1, 'fatal', 'parse'),
        diagnostic('src/new.ts', 0, 'error', 'format'),
        diagnostic('src/other.ts', 1),
      ],
    });
    expect(biomeFindings(report, SCOPE)).toEqual([
      'src/a.ts:3: lint/x m3',
      'src/new.ts:1: parse m1',
    ]);
  });

  test('unreadable biome output is a tool error', () => {
    for (const report of ['', 'not json', '{}', '{"diagnostics":[{"severity":"error"}]}']) {
      expect(() => biomeFindings(report, SCOPE)).toThrow(ToolError);
    }
  });

  test('knip findings keep symbol issues on changed lines', () => {
    const report = JSON.stringify({
      files: ['src/a.ts'],
      issues: [
        {
          file: 'src/a.ts',
          dependencies: [{ name: 'dep', line: 2 }],
          exports: [
            { name: 'kept', line: 2 },
            { name: 'old', line: 7 },
          ],
          types: [{ name: 'Shape', line: 3 }],
          enumMembers: { Color: [{ name: 'Red', line: 3 }] },
          classMembers: { Box: [{ name: 'size', line: 9 }] },
          duplicates: [[{ name: 'a', line: 2 }, { name: 'b' }]],
        },
        { file: 'package.json', devDependencies: [{ name: 'x', line: 2 }] },
        { file: 'src/new.ts', binaries: [{ name: 'tool' }], nsTypes: [{ name: 'T', line: 4 }] },
      ],
    });
    expect(knipFindings(report, SCOPE)).toEqual([
      'src/a.ts:2: unused export kept',
      'src/a.ts:3: unused exported type Shape',
      'src/a.ts:3: unused enum member Color.Red',
      'src/a.ts:2: duplicate export a, b',
      'src/new.ts:4: unused exported type in namespace T',
    ]);
  });

  test('unreadable knip output is a tool error', () => {
    for (const report of ['not json', '{}', '{"issues":[{"exports":[]}]}']) {
      expect(() => knipFindings(report, SCOPE)).toThrow(ToolError);
    }
  });
});

describe('runTool', () => {
  test('a missing tool is a tool error', async () => {
    await expect(runTool('nope', ['/nonexistent/harness-tool'])).rejects.toThrow(
      /^nope not runnable/,
    );
  });

  test('an unexpected exit names the last stderr line', async () => {
    const script = "process.stderr.write('a\\nboom\\n'); process.exit(4)";
    await expect(runTool('js', ['bun', '-e', script])).rejects.toThrow(/^js exited 4: boom$/);
  });

  test('a findings exit needs a report', async () => {
    const silent = ['bun', '-e', 'process.exit(4)'];
    const report = ['bun', '-e', "console.log('found'); process.exit(4)"];
    await expect(runTool('js', silent, { ok: [4] })).rejects.toThrow(ToolError);
    expect(await runTool('js', report, { ok: [4] })).toBe('found\n');
  });
});

describe('payload and exit', () => {
  test('payload names failed gates and caps findings', () => {
    const lines = stopHookPayload([
      { gate: 'Lint', findings: Array.from({ length: 23 }, (_, n) => `a.ts:${n + 1}: E1 x`) },
      { gate: 'Complexity', findings: [] },
      { gate: 'Dead code', findings: ['b.ts:1: unused'], problem: '' },
    ]).split('\n');
    expect(lines[0]).toBe('stop-hook failed: Lint, Dead code');
    expect(lines).toHaveLength(22);
    expect(lines[20]).toBe('a.ts:20: E1 x');
    expect(lines[21]).toBe('… +4 more — run `bun harness.ts stop-hook --verbose`');
  });

  test('verbose lifts the cap', () => {
    const findings = Array.from({ length: 30 }, (_, n) => `a.ts:${n}: x`);
    expect(capFindings(findings, true)).toEqual(findings);
  });

  test('twenty findings need no more line', () => {
    const findings = Array.from({ length: 20 }, (_, n) => `a.ts:${n}: x`);
    expect(capFindings(findings, false)).toEqual(findings);
  });

  test('clean results have no payload', () => {
    const results = [
      { gate: 'Lint', findings: [] },
      { gate: 'Complexity', findings: [], problem: 'boom' },
    ];
    expect(stopHookPayload(results)).toBe('');
  });

  test('exit codes', () => {
    const digest = payloadDigest('P');
    const active = { stop_hook_active: true };
    const cases: [string, number, Record<string, unknown>, string, number][] = [
      ['', 0, {}, '', 0],
      ['', 1, {}, '', 1],
      ['P', 0, {}, '', 2],
      ['P', 1, {}, '', 2], // a crashed gate never hides another gate's findings
      ['P', 0, active, '', 2],
      ['P', 0, {}, digest, 2], // a new turn blocks again on the same findings
      ['P', 0, active, digest, 1],
      ['Q', 0, active, digest, 2], // the findings changed: block again
      ['P', 0, { stop_hook_active: 'true' }, digest, 2],
    ];
    for (const [payload, failed, event, stored, expected] of cases) {
      expect([
        payload,
        failed,
        event,
        stored,
        stopHookExit(payload, failed, event, stored),
      ]).toEqual([payload, failed, event, stored, expected]);
    }
  });

  test('loop guard key', () => {
    expect(loopGuardKey('')).toBe('root');
    expect(loopGuardKey('bun')).toBe('bun');
    expect(loopGuardKey('bun/')).toBe('bun');
    expect(loopGuardKey('apps/my app')).toBe('apps-my-app');
  });
});

describe('hook input', () => {
  test('hook target resolves inside the project only', async () => {
    const root = realpathSync(mkdtempSync(join(tmpdir(), 'hook-target-')));
    roots.push(root);
    for (const path of ['src/app.ts', 'src/view.tsx', 'src/data.json', 'docs/tool.ts']) {
      write(root, path, '');
    }
    const cases: [Record<string, unknown>, string | null][] = [
      [{ tool_input: { file_path: join(root, 'src', 'app.ts') } }, 'src/app.ts'],
      [{ tool_input: { file_path: 'src/app.ts' } }, 'src/app.ts'],
      [{ tool_input: { file_path: 'src/view.tsx' } }, 'src/view.tsx'],
      [{ tool_input: { file_path: 'src/missing.ts' } }, null],
      [{ tool_input: { file_path: 'src/data.json' } }, null],
      [{ tool_input: { file_path: 'docs/tool.ts' } }, null],
      [{ tool_input: { file_path: '../elsewhere/src/app.ts' } }, null],
      [{ tool_input: { file_path: join(TEMPLATE, 'harness.ts') } }, null],
      [{ tool_input: { file_path: '' } }, null],
      [{ tool_input: { file_path: 3 } }, null],
      [{ tool_input: 'src/app.ts' }, null],
      [{}, null],
    ];
    for (const [event, expected] of cases) {
      expect([event, await hookTarget(event, root)]).toEqual([event, expected]);
    }
  });

  test('hook event tolerates bad input', () => {
    expect(parseHookEvent('{"stop_hook_active": true}')).toEqual({ stop_hook_active: true });
    for (const text of ['', 'not json', '[1, 2]', 'null']) {
      expect(parseHookEvent(text)).toEqual({});
    }
  });
});

describe('hook wiring', () => {
  test('installing into the committed settings is a no-op', async () => {
    const root = mkdtempSync(join(tmpdir(), 'hook-wiring-'));
    roots.push(root);
    for (const wiring of HOOK_WIRINGS) write(root, wiring.path, read(TEMPLATE, wiring.path));
    for (const wiring of HOOK_WIRINGS) await installHook(wiring, root);
    for (const wiring of HOOK_WIRINGS) {
      expect(read(root, wiring.path)).toBe(read(TEMPLATE, wiring.path));
    }
  });

  test('install replaces a legacy handler and keeps others', async () => {
    const root = mkdtempSync(join(tmpdir(), 'hook-wiring-'));
    roots.push(root);
    const other = { matcher: 'Edit', hooks: [{ type: 'command', command: 'echo hi' }] };
    const settings = {
      hooks: {
        Stop: [{ hooks: [{ type: 'command', command: 'bun harness.ts stop-hook' }] }],
        PostToolUse: [other],
      },
    };
    write(root, '.claude/settings.json', JSON.stringify(settings));
    for (let round = 0; round < 2; round++) {
      for (const wiring of HOOK_WIRINGS.slice(0, 2)) await installHook(wiring, root);
    }
    const data = JSON.parse(read(root, '.claude/settings.json'));
    expect(data.hooks.Stop).toEqual([{ hooks: [CLAUDE_STOP_HOOK] }]);
    expect(data.hooks.PostToolUse).toEqual([
      other,
      { matcher: 'Edit|Write', hooks: [CLAUDE_POST_EDIT_HOOK] },
    ]);
  });

  test('the presence check flags a missing PostToolUse', async () => {
    const root = mkdtempSync(join(tmpdir(), 'hook-wiring-'));
    roots.push(root);
    const stopOnly = { hooks: { Stop: [{ hooks: [CLAUDE_STOP_HOOK] }] } };
    write(root, '.claude/settings.json', JSON.stringify(stopOnly));
    write(root, '.codex/hooks.json', 'not json');
    const lines: string[] = [];
    const original = console.log;
    console.log = (...args: unknown[]) => lines.push(args.map(String).join(' '));
    try {
      await checkStopHooksPresent(root);
    } finally {
      console.log = original;
    }
    const text = lines.join('\n');
    expect(text).toContain('✓\x1b[0m Stop hook wiring (.claude/settings.json)');
    expect(text).toContain('Missing PostToolUse hook wiring: .claude/settings.json');
    expect(text).toContain('Missing Stop hook wiring: .codex/hooks.json');
  });
});

// ── End to end: the real CLI on throwaway repos ────────────────────

describe.concurrent('stop-hook end to end: scope and complexity', () => {
  test('a clean tree is silent', async () => {
    const { root, project } = await projectRepo();
    write(project, APP, module(HELPER));
    await commit(root);
    const result = await harness(project, ['stop-hook']);
    expect(result).toEqual({ code: 0, stdout: '', stderr: '' });
  });

  test('fixable lint is fixed first, silently', async () => {
    // A tracked, modified file is the first porcelain line (` M <path>`), which an
    // output-wide trim used to cut to `M <path>` and misread.
    const { root, project } = await projectRepo();
    write(project, APP, 'export const value = 0;\n');
    await commit(root);
    write(project, APP, 'let value = 1;\nexport { value };\n');
    const result = await harness(project, ['stop-hook']);
    expect(result).toEqual({ code: 0, stdout: '', stderr: '' });
    expect(read(project, APP)).toBe('const value = 1;\n\nexport { value };\n');
  });

  test('an untouched legacy function does not block', async () => {
    // The project sits in a subdirectory and the change is committed on a branch.
    const { root, project } = await projectRepo('proj');
    await featureBranch(root, project, module(branchy('busy', 20), HELPER));
    write(project, APP, module(branchy('busy', 20), HELPER, 'export const VALUE = 2;\n'));
    await commit(root);
    const result = await harness(project, ['stop-hook']);
    expect(result).toEqual({ code: 0, stdout: '', stderr: '' });
  });

  test('a new complex function blocks, then the loop guard releases', async () => {
    const { root, project } = await projectRepo('proj');
    await featureBranch(root, project, module(HELPER));
    write(project, APP, module(HELPER, branchy('busy', 20)));
    await commit(root);
    const active = JSON.stringify({ stop_hook_active: true });
    const first = await harness(project, ['stop-hook'], active);
    const second = await harness(project, ['stop-hook'], active);
    const state = join((await git(root, 'rev-parse', '--absolute-git-dir')).trim(), 'harness');
    const stateFiles = readdirSync(state);

    write(project, APP, module(HELPER)); // fixed: a clean stop forgets it
    const third = await harness(project, ['stop-hook'], active);

    expect([first.code, first.stdout]).toEqual([2, '']);
    expect(first.stderr.split('\n')).toEqual([
      'stop-hook failed: Complexity',
      `${APP}:5: busy CCN new→21 (limit 15)`,
      '',
    ]);
    expect([second.code, second.stdout]).toEqual([1, '']);
    expect(second.stderr).toBe(`${first.stderr}${LOOP_GUARD_NOTICE}\n`);
    expect(stateFiles).toEqual(['stop-hook-proj']);
    expect([third.code, third.stderr]).toEqual([0, '']);
    expect(readdirSync(state)).toEqual([]);
  });

  test('worse blocks and better passes', async () => {
    const { root, project } = await projectRepo();
    await featureBranch(root, project, module(branchy('busy', 16)));
    write(project, APP, module(branchy('busy', 18)));
    const worse = await harness(project, ['stop-hook']);
    write(project, APP, module(branchy('busy', 15)));
    const better = await harness(project, ['stop-hook']);

    expect(worse.code).toBe(2);
    expect(worse.stderr).toContain(`${APP}:1: busy CCN 17→19 (limit 15)`);
    expect([better.code, better.stderr]).toEqual([0, '']);
  });
});

describe.concurrent('stop-hook end to end: lint, dead code, tool failure', () => {
  test('lint blocks on changed lines only', async () => {
    const lint = 'export function lint(value: number): boolean {\n  return value == 1;\n}\n';
    const { root, project } = await projectRepo();
    write(project, APP, module(HELPER));
    await commit(root);
    write(project, APP, module(HELPER, lint));
    const changed = await harness(project, ['stop-hook']);
    await commit(root);
    write(project, APP, module(HELPER, lint, 'export const VALUE = 2;\n'));
    const unchanged = await harness(project, ['stop-hook']);

    expect(changed.code).toBe(2);
    expect(changed.stderr.split('\n')).toEqual([
      'stop-hook failed: Lint',
      `${APP}:6: lint/suspicious/noDoubleEquals ` +
        'Using == may be unsafe if you are relying on type coercion.',
      '',
    ]);
    expect([unchanged.code, unchanged.stderr]).toEqual([0, '']);
  });

  test('a tool failure exits 1, not 2', async () => {
    const { root, project } = await projectRepo();
    write(project, APP, module(HELPER));
    await commit(root);
    write(project, 'biome.json', '{ "linter": { "rules": { "nope": 5 } } }\n');
    write(project, APP, module(HELPER, 'export const VALUE = 2;\n'));
    const result = await harness(project, ['stop-hook']);

    expect([result.code, result.stdout]).toEqual([1, '']);
    expect(result.stderr).toMatch(/^stop-hook: Lint could not run: biome exited 1: /);
    expect(result.stderr).not.toContain('stop-hook failed');
  });
  test('dead code blocks on changed lines only', async () => {
    const { root, project } = await projectRepo();
    write(project, 'src/index.ts', "import { used } from './lib';\n\nexport const total = used;\n");
    write(project, 'src/lib.ts', 'export const used = 1;\nexport const legacy = 2;\n');
    await commit(root);
    write(
      project,
      'src/lib.ts',
      'export const used = 1;\nexport const legacy = 2;\nexport const fresh = 3;\n',
    );
    const result = await harness(project, ['stop-hook']);

    expect(result).toEqual({
      code: 2,
      stdout: '',
      stderr: 'stop-hook failed: Dead code\nsrc/lib.ts:3: unused export fresh\n',
    });
  });
});

describe.concurrent('post-edit --hook end to end', () => {
  test('a reformatted file asks for a re-read; anything else is silent', async () => {
    const { project } = await projectRepo();
    write(project, 'src/app.ts', 'export const x=1\n');
    write(project, 'src/clean.ts', 'export const x = 1;\n');
    const hook = (filePath: string) =>
      harness(
        project,
        ['post-edit', '--hook'],
        JSON.stringify({ tool_input: { file_path: filePath } }),
      );
    const messy = await hook(join(project, 'src', 'app.ts'));
    const clean = await hook('src/clean.ts');
    const outside = await hook(join(TEMPLATE, 'harness.ts'));
    const garbage = await harness(project, ['post-edit', '--hook'], '{not json');

    expect(messy).toEqual({
      code: 0,
      stdout:
        '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":' +
        '"harness: reformatted src/app.ts; re-read it before editing it again"}}\n',
      stderr: '',
    });
    expect(read(project, 'src/app.ts')).toBe('export const x = 1;\n');
    for (const result of [clean, outside, garbage]) {
      expect(result).toEqual({ code: 0, stdout: '', stderr: '' });
    }
  });
});

describe.concurrent('pre-commit and pre-push end to end', () => {
  const ENV_TEST = [
    "import { expect, test } from 'bun:test';",
    '',
    "test('runs outside git hook variables', () => {",
    '  expect(process.env.GIT_DIR).toBeUndefined();',
    '  expect(process.env.HARNESS_FAIL).toBeUndefined();',
    '});',
    '',
  ].join('\n');

  test('pre-commit no longer runs tests', async () => {
    const { root, project } = await projectRepo();
    write(project, 'tests/env.test.ts', ENV_TEST);
    await commit(root);
    write(project, 'src/app.ts', module(HELPER));
    await git(root, 'add', 'src/app.ts');
    const result = await harness(project, ['pre-commit'], '', { HARNESS_FAIL: '1' });

    expect(result.code).toBe(0);
    expect(result.stdout).toContain('Typecheck');
    expect(result.stdout).not.toContain('Tests');
  });

  test('pre-push runs tests without git hook variables and fails on them', async () => {
    const { root, project } = await projectRepo();
    write(project, 'tests/env.test.ts', ENV_TEST);
    await commit(root);
    await git(root, 'checkout', '-q', '-b', 'feature');
    const tip = (await git(root, 'rev-parse', 'HEAD')).trim();
    const refs = `refs/heads/feature ${tip} refs/heads/feature ${'0'.repeat(40)}\n`;
    const env = { HARNESS_PRE_PUSH_REFS: refs, GIT_DIR: join(root, '.git') };
    const passing = await harness(project, ['pre-push'], '', env);
    const failing = await harness(project, ['pre-push'], '', { ...env, HARNESS_FAIL: '1' });

    expect([passing.code, passing.stdout]).toEqual([0, expect.stringContaining('✓\x1b[0m Tests')]);
    expect([failing.code, failing.stdout]).toEqual([1, expect.stringContaining('✗\x1b[0m Tests')]);
  });
});

describe.concurrent('AGENTS.md auto-sync end to end', () => {
  async function docsRepo(): Promise<{ root: string; project: string }> {
    const repo = await projectRepo();
    write(repo.project, 'CLAUDE.md', 'v1\n');
    write(repo.project, 'AGENTS.md', 'v1\n');
    await commit(repo.root, 'docs');
    return repo;
  }

  test('stop-hook mirrors an uncommitted CLAUDE.md', async () => {
    const { root, project } = await docsRepo();
    write(project, 'AGENTS.md', 'v1 hand edit\n');
    write(project, 'CLAUDE.md', 'v2\n');
    const first = await harness(project, ['stop-hook']);
    const mirrored = read(project, 'AGENTS.md');

    // After the first sync AGENTS.md is itself modified; later edits still sync.
    write(project, 'CLAUDE.md', 'v3\n');
    await git(root, 'add', 'CLAUDE.md');
    const second = await harness(project, ['stop-hook']);

    expect([first, mirrored]).toEqual([{ code: 0, stdout: '', stderr: '' }, 'v2\n']);
    expect([second, read(project, 'AGENTS.md')]).toEqual([
      { code: 0, stdout: '', stderr: '' },
      'v3\n',
    ]);
  });

  test('stop-hook leaves an AGENTS.md-only edit', async () => {
    const { project } = await docsRepo();
    write(project, 'AGENTS.md', 'hand edit\n');
    await harness(project, ['stop-hook']);
    expect(read(project, 'AGENTS.md')).toBe('hand edit\n');
  });

  test('pre-commit stages the mirror of a staged CLAUDE.md', async () => {
    const { root, project } = await docsRepo();
    write(project, 'CLAUDE.md', 'v2\n');
    await git(root, 'add', 'CLAUDE.md');
    const result = await harness(project, ['pre-commit']);
    const staged = (await git(root, 'diff', '--cached', '--name-only')).split('\n');

    expect(result.code).toBe(0);
    expect(result.stdout).toContain('AGENTS.md ← CLAUDE.md (staged)');
    expect(staged).toEqual(['AGENTS.md', 'CLAUDE.md', '']);
  });

  test('pre-commit fails on a mirror edited alone or beside source', async () => {
    const { root, project } = await docsRepo();
    write(project, 'AGENTS.md', 'hand edit\n');
    await git(root, 'add', 'AGENTS.md');
    const alone = await harness(project, ['pre-commit']);
    write(project, 'src/app.ts', module(HELPER));
    await git(root, 'add', 'src/app.ts');
    const beside = await harness(project, ['pre-commit']);

    for (const result of [alone, beside]) {
      expect(result.code).toBe(1);
      expect(result.stdout).toContain('AGENTS.md differs from CLAUDE.md');
      expect(result.stdout).not.toContain('Typecheck');
    }
    expect(read(project, 'AGENTS.md')).toBe('hand edit\n');
  });
});
