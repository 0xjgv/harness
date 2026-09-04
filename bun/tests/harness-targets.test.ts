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
  mapChangedToTests,
  normalizeChangedPath,
  porcelainPath,
  qualityTargets,
  resolveFallbackArchBase,
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

  test('FIX 1: repo-root-relative path normalizes to project-relative and is recognized', () => {
    // git status --porcelain always returns repo-root-relative paths (e.g.
    // "bun/src/foo.ts" when the git root is one level up from this template).
    // normalizeChangedPath() strips the gitPrefix() ("bun") so isProjectTsFile
    // sees the same "src/..." shape it expects when run from the template root.
    const normalized = normalizeChangedPath('bun/src/foo.ts', 'bun');
    expect(normalized).toBe('src/foo.ts');
    expect(isProjectTsFile(normalized)).toBe(true);
  });

  test('FIX 1: a sibling template path is rejected after normalization', () => {
    // A change in a sibling template (e.g. python/x.ts) does not share this
    // template's prefix, so it is left untouched and correctly rejected.
    const normalized = normalizeChangedPath('python/x.ts', 'bun');
    expect(normalized).toBe('python/x.ts');
    expect(isProjectTsFile(normalized)).toBe(false);
  });
});

describe('resolveFallbackArchBase (FIX 6)', () => {
  test('picks the first resolvable ref in priority order', async () => {
    const calls: string[] = [];
    const verify = async (ref: string) => {
      calls.push(ref);
      return ref === 'origin/main';
    };
    expect(await resolveFallbackArchBase(verify)).toBe('origin/main');
    // origin/HEAD was tried and failed before origin/main resolved; main was
    // never reached because the search stops at the first hit.
    expect(calls).toEqual(['origin/HEAD', 'origin/main']);
  });

  test('returns undefined when no fallback ref resolves', async () => {
    expect(await resolveFallbackArchBase(async () => false)).toBeUndefined();
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

describe('change-set mapping', () => {
  // src/index.ts re-exports src/core.ts, so only a transitive walk connects a
  // src/core.ts edit to the test that imports src/index.ts.
  function importProject(): string {
    const root = mkdtempSync(join(tmpdir(), 'bun-scope-'));
    mkdirSync(join(root, 'src'));
    mkdirSync(join(root, 'tests'));
    writeFileSync(join(root, 'harness.ts'), 'export const version = 1;\n');
    writeFileSync(join(root, 'src', 'core.ts'), 'export const core = 1;\n');
    writeFileSync(join(root, 'src', 'index.ts'), "export * from './core';\n");
    writeFileSync(join(root, 'src', 'orphan.ts'), 'export const orphan = 1;\n');
    writeFileSync(join(root, 'tests', 'smoke.test.ts'), "import '../src/index';\n");
    writeFileSync(join(root, 'tests', 'harness.test.ts'), "import '../harness';\n");
    writeFileSync(join(root, 'tests', 'steps.ts'), 'export const step = 1;\n');
    roots.push(root);
    return root;
  }

  test('a changed source maps to the tests that reach it transitively', async () => {
    const root = importProject();
    expect(await mapChangedToTests(['src/core.ts'], root)).toEqual({
      tests: ['tests/smoke.test.ts'],
      unmapped: [],
    });
  });

  test('a changed source no test imports is reported, never failed', async () => {
    const root = importProject();
    // tests/steps.ts is test code no test imports: nothing to run, nothing to report.
    const changed = ['src/orphan.ts', 'harness.ts', 'tests/steps.ts'];
    expect(await mapChangedToTests(changed, root)).toEqual({
      tests: ['tests/harness.test.ts'],
      unmapped: ['src/orphan.ts'],
    });
  });

  test('a changed test file maps to itself', async () => {
    const root = importProject();
    expect(await mapChangedToTests(['tests/smoke.test.ts'], root)).toEqual({
      tests: ['tests/smoke.test.ts'],
      unmapped: [],
    });
  });
});
