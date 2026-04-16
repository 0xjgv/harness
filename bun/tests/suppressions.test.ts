import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseLineForSuppressions, scanSuppressions } from '../harness';

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
