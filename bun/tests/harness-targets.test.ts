import { afterEach, describe, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  appTargets,
  existingTargets,
  hasTests,
  isProjectTsFile,
  isQualityTsFile,
  isTestFile,
  porcelainPath,
  qualityTargets,
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
