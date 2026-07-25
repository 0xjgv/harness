import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  coverageMinDefault,
  parseLineForSuppressions,
  parseMinArg,
  readBaseline,
  scanSuppressionFindings,
  scanSuppressions,
} from '../harness';

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
