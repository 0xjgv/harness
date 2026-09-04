import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  baselineFloor,
  complexityGatesOrWarn,
  coverageMinDefault,
  duplicateBlockCount,
  lizardWarningCount,
  type Measurement,
  measureRatcheted,
  parseLineForSuppressions,
  parseMinArg,
  RATCHETED_METRICS,
  type RatchetedMetric,
  readBaseline,
  runCapture,
  scanSuppressionFindings,
  scanSuppressions,
  writeBaseline,
} from '../harness';

/** A real `lizard -Eduplicate` tail: two blocks, then the footer that proves it ran. */
const DUPLICATE_REPORT = [
  'Duplicates',
  '===================================',
  'Duplicate block:',
  '--------------------------',
  'src/a.ts:24 ~ 28',
  'src/b.ts:50 ~ 55',
  '^^^^^^^^^^^^^^^^^^^^^^^^^^',
  '',
  'Duplicate block:',
  '--------------------------',
  'src/a.ts:24 ~ 28',
  'src/b.ts:60 ~ 66',
  '^^^^^^^^^^^^^^^^^^^^^^^^^^',
  '',
  'Total duplicate rate: 2.97%',
  'Total unique rate: 96.73%',
  '',
].join('\n');

describe('parseLineForSuppressions', () => {
  test('plain code returns no matches', () => {
    expect(parseLineForSuppressions('const x = 1;')).toEqual([]);
  });

  test('@ts-ignore', () => {
    expect(parseLineForSuppressions('// @ts-ignore')).toEqual([{ kind: 'ts-ignore', rules: [] }]);
  });

  test('@ts-expect-error with trailing text', () => {
    expect(parseLineForSuppressions('// @ts-expect-error because reasons')).toEqual([
      { kind: 'ts-expect-error', rules: [] },
    ]);
  });

  test('@ts-nocheck', () => {
    expect(parseLineForSuppressions('// @ts-nocheck')).toEqual([{ kind: 'ts-nocheck', rules: [] }]);
  });

  test('eslint-disable-line with no rules', () => {
    expect(parseLineForSuppressions('foo(); // eslint-disable-line')).toEqual([
      { kind: 'eslint-disable', rules: [] },
    ]);
  });

  test('eslint-disable-next-line with colon-separated rules and whitespace', () => {
    expect(parseLineForSuppressions('// eslint-disable-next-line: no-unused-vars,  semi')).toEqual([
      { kind: 'eslint-disable', rules: ['no-unused-vars', 'semi'] },
    ]);
  });

  test('eslint-disable in block comment', () => {
    expect(parseLineForSuppressions('/* eslint-disable */')).toEqual([
      { kind: 'eslint-disable', rules: [] },
    ]);
  });

  test('biome-ignore with namespaced rule', () => {
    expect(
      parseLineForSuppressions('// biome-ignore lint/style/useSingleVarDeclarator: reason'),
    ).toEqual([{ kind: 'biome-ignore', rules: ['lint/style/useSingleVarDeclarator'] }]);
  });
});

describe('scanSuppressions', () => {
  let tmp: string;

  beforeAll(() => {
    tmp = mkdtempSync(join(tmpdir(), 'bun-suppr-'));
    mkdirSync(join(tmp, 'src'));
    mkdirSync(join(tmp, 'tests'));
    writeFileSync(
      join(tmp, 'src', 'a.ts'),
      '// @ts-ignore\nconst x = 1;\nfoo(); // eslint-disable-line: no-unused-vars\n',
    );
    writeFileSync(
      join(tmp, 'tests', 'b.ts'),
      '// biome-ignore lint/style/useSingleVarDeclarator: reason\nconst y = 2;\n',
    );
    writeFileSync(join(tmp, 'src', 'skip.md'), '// @ts-ignore (should be ignored — not .ts)\n');
  });

  afterAll(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  test('counts suppressions across src and tests and ignores non-ts files', async () => {
    const results = await scanSuppressions([join(tmp, 'src'), join(tmp, 'tests')]);
    expect(results['ts-ignore']).toHaveLength(1);
    expect(results['eslint-disable']).toEqual([['no-unused-vars']]);
    expect(results['biome-ignore']).toEqual([['lint/style/useSingleVarDeclarator']]);
  });
});

describe('scanSuppressionFindings locations (FIX 5)', () => {
  test('locations are always relative to ROOT, for both scan branches', async () => {
    const tmp = mkdtempSync(join(tmpdir(), 'bun-suppr-loc-'));
    try {
      // A root that is itself a .ts file exercises the single-file branch; a root
      // that is a directory exercises the directory-walk branch. Before the fix,
      // the single-file branch echoed the raw (here: absolute) input path while
      // the directory branch always built an absolute path — a mix of formats.
      // Assembled at runtime, not written as a literal directive comment, so this
      // fixture doesn't trip the real suppression ratchet when this file is itself
      // scanned as part of tests/.
      const ignoreDirective = ['//', '@ts-ignore'].join(' ');
      const topFile = join(tmp, 'top.ts');
      writeFileSync(topFile, `${ignoreDirective}\nconst x = 1;\n`);
      mkdirSync(join(tmp, 'nested'));
      writeFileSync(join(tmp, 'nested', 'child.ts'), `${ignoreDirective}\nconst y = 2;\n`);

      const findings = await scanSuppressionFindings([topFile, join(tmp, 'nested')]);
      expect(findings).toHaveLength(2);
      for (const finding of findings) {
        expect(finding.location.startsWith('/')).toBe(false);
        expect(finding.location).toMatch(/:\d+$/);
      }
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});

describe('baseline helpers', () => {
  const baselineRoots: string[] = [];

  afterAll(() => {
    for (const root of baselineRoots) rmSync(root, { recursive: true, force: true });
  });

  test('readBaseline parses key value lines', async () => {
    const root = mkdtempSync(join(tmpdir(), 'bun-baseline-'));
    baselineRoots.push(root);
    writeFileSync(join(root, '.harness-baseline'), 'suppressions.ts-ignore 2\ncoverage.min 70\n');

    expect(await readBaseline(root)).toEqual({
      'suppressions.ts-ignore': 2,
      'coverage.min': 70,
    });
  });

  test('coverageMinDefault uses flag before baseline', async () => {
    const root = mkdtempSync(join(tmpdir(), 'bun-baseline-'));
    baselineRoots.push(root);
    writeFileSync(join(root, '.harness-baseline'), 'coverage.min 60\n');
    const originalArgv = process.argv;
    try {
      process.argv = ['bun', 'harness.ts', 'coverage'];
      expect(await coverageMinDefault(root)).toBe(60);
      process.argv = ['bun', 'harness.ts', 'coverage', '--min=10'];
      expect(await coverageMinDefault(root)).toBe(10);
    } finally {
      process.argv = originalArgv;
    }
  });
});

describe('writeBaseline merge semantics', () => {
  const roots: string[] = [];

  afterAll(() => {
    for (const root of roots) rmSync(root, { recursive: true, force: true });
  });

  // Every test must pass an explicit base: the default is the template root, and
  // one forgotten argument would rewrite the shipped `.harness-baseline`.
  function project(existing?: string): string {
    const root = mkdtempSync(join(tmpdir(), 'bun-write-baseline-'));
    roots.push(root);
    if (existing !== undefined) writeFileSync(join(root, '.harness-baseline'), existing);
    return root;
  }

  function read(root: string): string {
    return readFileSync(join(root, '.harness-baseline'), 'utf8');
  }

  test('measured floors are written and unknown keys are preserved', async () => {
    const root = project('mutation.min 63\ncoverage.min 40\n');
    const written = await writeBaseline(
      { 'ts-ignore': [[]] },
      { 'coverage.min': { value: 72 }, 'complexity.max_violations': { value: 3 } },
      root,
    );

    expect(written.ok).toBe(true);
    expect(await readBaseline(root)).toMatchObject({
      'mutation.min': 63,
      'coverage.min': 72,
      'complexity.max_violations': 3,
      'suppressions.ts-ignore': 1,
    });
  });

  test('a kind that vanished from the tree ratchets to 0, not to its old count', async () => {
    const root = project('suppressions.ts-ignore 5\nsuppressions.biome-ignore 2\n');
    await writeBaseline({ 'ts-ignore': [[]] }, {}, root);

    expect(await readBaseline(root)).toMatchObject({
      'suppressions.ts-ignore': 1,
      'suppressions.biome-ignore': 0,
    });
  });

  test('an unavailable metric drops its key instead of carrying the shipped number', async () => {
    const root = project('coverage.min 100\n');
    const written = await writeBaseline({}, { 'coverage.min': { unavailable: 'no tests' } }, root);

    expect(written.ok).toBe(true);
    expect(read(root)).not.toContain('coverage.min');
  });

  test('an errored metric writes nothing at all', async () => {
    const root = project('coverage.min 40\n');
    const written = await writeBaseline(
      { 'ts-ignore': [[]] },
      { 'coverage.min': { error: 'the test run under coverage failed (exit 1)' } },
      root,
    );

    expect(written).toEqual({
      ok: false,
      broken: [['coverage.min', 'the test run under coverage failed (exit 1)']],
    });
    expect(read(root)).toBe('coverage.min 40\n');
  });

  test('lines are sorted so the file is diff-stable', async () => {
    const root = project();
    await writeBaseline(
      {},
      { 'crap.max_violations': { value: 2 }, 'coverage.min': { value: 1 } },
      root,
    );

    const keys = read(root)
      .trim()
      .split('\n')
      .map((line) => line.split(' ')[0]);
    expect(keys).toEqual([...keys].sort());
  });
});

describe('measureRatcheted', () => {
  const metric = (key: string, measurement: Measurement): RatchetedMetric => ({
    key,
    measure: () => Promise.resolve(measurement),
  });

  test('stops at the first error so the later noise is not reported', async () => {
    const measured = await measureRatcheted([
      metric('coverage.min', { value: 70 }),
      metric('complexity.max_violations', { error: 'lizard failed to run (exit 2)' }),
      metric('crap.max_violations', { value: 0 }),
    ]);

    expect(Object.keys(measured)).toEqual(['coverage.min', 'complexity.max_violations']);
  });

  test('an unavailable metric does not stop the ones after it', async () => {
    const measured = await measureRatcheted([
      metric('coverage.min', { unavailable: 'no tests' }),
      metric('crap.max_violations', { value: 4 }),
    ]);

    expect(measured['crap.max_violations']).toEqual({ value: 4 });
  });

  test('the shipped table covers exactly the keys this template ratchets', () => {
    expect(RATCHETED_METRICS.map((m) => m.key)).toEqual([
      'coverage.min',
      'complexity.max_violations',
      'duplication.max_blocks',
      'crap.max_violations',
    ]);
  });
});

describe('lizard floors', () => {
  const roots: string[] = [];

  afterAll(() => {
    for (const root of roots) rmSync(root, { recursive: true, force: true });
  });

  function project(existing?: string): string {
    const root = mkdtempSync(join(tmpdir(), 'bun-complexity-'));
    roots.push(root);
    mkdirSync(join(root, 'src'));
    mkdirSync(join(root, 'tests'));
    writeFileSync(join(root, 'src', 'index.ts'), 'export const x = 1;\n');
    writeFileSync(join(root, 'tests', 'x.test.ts'), 'export const y = 1;\n');
    if (existing !== undefined) writeFileSync(join(root, '.harness-baseline'), existing);
    return root;
  }

  test('no floor recorded means report-only, not -i 0', async () => {
    const root = project();
    const [gate] = await complexityGatesOrWarn(root);

    expect(gate?.description).toContain('report-only: no .harness-baseline floor');
    expect(gate?.cmd.at(-1)).toBe('1000000');
    expect(gate?.extract?.('')).toContain('suppressions --update-baseline');
    expect(await baselineFloor('complexity.max_violations', root)).toBeUndefined();
  });

  test('a recorded floor is handed to lizard as -i N', async () => {
    const root = project('complexity.max_violations 4\n');
    const [gate] = await complexityGatesOrWarn(root);

    expect(gate?.description).toBe('Complexity (lizard, baseline 4)');
    expect(gate?.cmd.slice(-2)).toEqual(['-i', '4']);
    expect(await baselineFloor('complexity.max_violations', root)).toBe(4);
  });

  test('a floor of 0 is a real floor, not a missing one', async () => {
    const root = project('complexity.max_violations 0\n');
    const [gate] = await complexityGatesOrWarn(root);

    expect(gate?.description).toBe('Complexity (lizard)');
    expect(gate?.cmd.slice(-2)).toEqual(['-i', '0']);
  });

  // The floor only reproduces against the same target set the complexity gate
  // uses, so compare the two lists instead of hardcoding one of them.
  test('the duplicate-block gate scans the same targets as the complexity gate', async () => {
    const root = project();
    const [complexity, duplication] = await complexityGatesOrWarn(root);
    const targets = (complexity?.cmd ?? []).slice(2, complexity?.cmd.indexOf('-C'));

    expect(targets).toEqual(['src', 'tests']);
    expect(duplication?.cmd.slice(2, 2 + targets.length)).toEqual(targets);
    expect(duplication?.cmd.slice(2 + targets.length)).toEqual([
      '-Eduplicate',
      '-w',
      '-i',
      '1000000',
    ]);
  });

  test('no duplication floor recorded means report-only, never a failing verdict', async () => {
    const root = project();
    const [, gate] = await complexityGatesOrWarn(root);

    expect(gate?.description).toContain('report-only: no .harness-baseline floor');
    const verdict = gate?.verdict?.(DUPLICATE_REPORT);
    expect(verdict?.ok).toBe(true);
    expect(verdict?.detail).toContain('suppressions --update-baseline');
  });

  test('a recorded floor tolerates exactly that many blocks', async () => {
    const root = project('duplication.max_blocks 2\n');
    const [, gate] = await complexityGatesOrWarn(root);

    expect(gate?.description).toBe('Duplicate blocks (lizard, baseline 2)');
    expect(gate?.verdict?.(DUPLICATE_REPORT)).toEqual({ ok: true, detail: '2 block(s)' });
  });

  test('one block over the floor fails', async () => {
    const root = project('duplication.max_blocks 1\n');
    const [, gate] = await complexityGatesOrWarn(root);

    expect(gate?.verdict?.(DUPLICATE_REPORT)?.ok).toBe(false);
  });

  test('a missing report fails the verdict instead of passing at zero', async () => {
    const root = project('duplication.max_blocks 5\n');
    const [, gate] = await complexityGatesOrWarn(root);

    expect(gate?.verdict?.('uvx: command not found')?.ok).toBe(false);
  });

  // Report-only means report-only: a garbled run cannot turn day one red either.
  test('a missing report is still report-only when no floor is recorded', async () => {
    const root = project();
    const [, gate] = await complexityGatesOrWarn(root);

    expect(gate?.verdict?.('uvx: command not found')?.ok).toBe(true);
  });

  // The guard that makes `verdict` safe: a crashed tool prints no findings, and
  // scoring that as "zero findings" is the false-pass the whole design avoids.
  test('a verdict is not consulted when the command exited non-zero', async () => {
    const result = await runCapture({
      description: 'crashes',
      cmd: ['sh', '-c', 'exit 3'],
      verdict: () => ({ ok: true, detail: 'looks clean to me' }),
    });

    expect(result.ok).toBe(false);
    expect(result.exitCode).toBe(3);
  });

  test('a failing verdict on a clean exit still exits non-zero', async () => {
    const result = await runCapture({
      description: 'passes, reports trouble',
      cmd: ['sh', '-c', 'exit 0'],
      verdict: () => ({ ok: false, detail: '3 block(s)' }),
    });

    expect(result.ok).toBe(false);
    expect(result.exitCode).toBe(1);
    expect(result.detail).toBe('3 block(s)');
  });
});

describe('lizardWarningCount', () => {
  const summary = [
    '='.repeat(10),
    'Total nloc   Avg.NLOC  AvgCCN  Avg.token   Fun Cnt  Warning cnt   Fun Rt   nloc Rt',
    '--------------------------------------------------------------------------------',
    '      1200       12.0     3.2      100.0        99            7     0.07      0.12',
  ].join('\n');

  test('reads the Warning cnt column out of the summary row', () => {
    expect(lizardWarningCount(summary)).toBe(7);
  });

  test('no summary row is null, never a silent 0', () => {
    expect(lizardWarningCount('lizard: command not found')).toBeNull();
  });
});

describe('duplicateBlockCount', () => {
  test('counts the block headers, not the file lines under them', () => {
    expect(duplicateBlockCount(DUPLICATE_REPORT)).toBe(2);
  });

  test('a report with no blocks is 0', () => {
    expect(duplicateBlockCount('Duplicates\n===\nTotal duplicate rate: 0.00%\n')).toBe(0);
  });

  test('no report at all is null, never a silent 0', () => {
    expect(duplicateBlockCount('uvx: command not found')).toBeNull();
  });
});

describe('parseMinArg (FIX 4)', () => {
  test('flag absent', () => {
    expect(parseMinArg(['bun', 'harness.ts', 'coverage'])).toEqual({ present: false });
  });

  test('valid integer value', () => {
    expect(parseMinArg(['bun', 'harness.ts', 'coverage', '--min=42'])).toEqual({
      present: true,
      ok: true,
      value: 42,
    });
  });

  test('non-numeric value is reported instead of silently becoming NaN', () => {
    expect(parseMinArg(['bun', 'harness.ts', 'coverage', '--min=abc'])).toEqual({
      present: true,
      ok: false,
      raw: 'abc',
    });
  });

  test('non-integer numeric value is rejected', () => {
    expect(parseMinArg(['bun', 'harness.ts', 'coverage', '--min=50.5'])).toEqual({
      present: true,
      ok: false,
      raw: '50.5',
    });
  });
});
