import { afterEach, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  appTargets,
  existingTargets,
  type Gate,
  hasTests,
  isProjectTsFile,
  isQualityTsFile,
  isTestFile,
  porcelainPath,
  qualityTargets,
  runGatesParallel,
} from '../harness';

function tempProject(withTests = false): string {
  const root = mkdtempSync(join(tmpdir(), 'bun-targets-'));
  mkdirSync(join(root, 'src'));
  writeFileSync(join(root, 'src', 'index.ts'), 'export const value = 1;\n');
  writeFileSync(join(root, 'harness.ts'), '// harness\n');
  if (withTests) {
    mkdirSync(join(root, 'tests'));
    writeFileSync(join(root, 'tests', 'index.test.ts'), "import { test } from 'bun:test';\n");
  }
  return root;
}

let roots: string[] = [];

afterEach(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
  roots = [];
});

describe('target helpers', () => {
  test('filter existing quality and app targets', async () => {
    const root = tempProject(true);
    roots.push(root);

    expect(await existingTargets(['src', 'missing', 'harness.ts'], root)).toEqual([
      'src',
      'harness.ts',
    ]);
    expect(await qualityTargets({ base: root })).toEqual(['src', 'harness.ts', 'tests']);
    expect(await qualityTargets({ base: root, includeTests: false })).toEqual([
      'src',
      'harness.ts',
    ]);
    expect(await appTargets({ base: root })).toEqual(['src']);
    expect(await appTargets({ base: root, includeTests: true })).toEqual(['src', 'tests']);
  });

  test('detects Bun test file names', async () => {
    const root = tempProject();
    roots.push(root);

    expect(isTestFile('index.test.ts')).toBe(true);
    expect(isTestFile('index.spec.ts')).toBe(true);
    expect(isTestFile('index.ts')).toBe(false);
    expect(await hasTests(root)).toBe(false);

    mkdirSync(join(root, 'tests'));
    writeFileSync(join(root, 'tests', 'helper.ts'), 'export const helper = true;\n');
    expect(await hasTests(root)).toBe(false);

    writeFileSync(join(root, 'tests', 'feature.spec.ts'), "import { test } from 'bun:test';\n");
    expect(await hasTests(root)).toBe(true);
  });

  test('project file predicates include harness and tests', () => {
    expect(isProjectTsFile('src/index.ts')).toBe(true);
    expect(isProjectTsFile('tests/index.test.ts')).toBe(true);
    expect(isProjectTsFile('harness.ts')).toBe(true);
    expect(isProjectTsFile('docs/example.ts')).toBe(false);
    expect(isProjectTsFile('src/data.json')).toBe(false);
    expect(isQualityTsFile('src/index.ts')).toBe(true);
    expect(isQualityTsFile('harness.ts')).toBe(true);
    expect(isQualityTsFile('tests/index.test.ts')).toBe(false);
  });

  test('porcelain path keeps rename target', () => {
    expect(porcelainPath(' M src/index.ts')).toBe('src/index.ts');
    expect(porcelainPath('R  old.ts -> harness.ts')).toBe('harness.ts');
  });
});

describe('parallel gate runner', () => {
  function captureLog(): { lines: string[]; restore: () => void } {
    const lines: string[] = [];
    const original = console.log;
    console.log = (...args: unknown[]) => {
      lines.push(args.map(String).join(' '));
    };
    return { lines, restore: () => (console.log = original) };
  }

  test('all gates run to completion on a seeded failure', async () => {
    // A seeded failure in the middle must not short-circuit: every gate still
    // reports, results print in submission order, and the overall result is false.
    const gates: Gate[] = [
      { description: 'first ok', cmd: ['true'] },
      { description: 'seeded fail', cmd: ['false'] },
      { description: 'last ok', cmd: ['true'] },
    ];
    const { lines, restore } = captureLog();
    let allOk: boolean;
    try {
      allOk = await runGatesParallel(gates);
    } finally {
      restore();
    }
    const text = lines.join('\n');

    expect(allOk).toBe(false);
    expect(text).toContain('first ok');
    expect(text).toContain('seeded fail');
    expect(text).toContain('last ok');
    expect(text.indexOf('first ok')).toBeLessThan(text.indexOf('last ok'));
  });

  test('empty batch passes', async () => {
    expect(await runGatesParallel([])).toBe(true);
  });
});
