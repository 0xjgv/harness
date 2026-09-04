import { describe, expect, test } from 'bun:test';
import { evaluateGherkinGuard, isProductionSourcePath } from '../harness';

describe('isProductionSourcePath', () => {
  test('files under src/ are production source', () => {
    expect(isProductionSourcePath('src/index.ts')).toBe(true);
    expect(isProductionSourcePath('src/nested/thing.ts')).toBe(true);
  });

  test('tests are excluded', () => {
    expect(isProductionSourcePath('tests/index.test.ts')).toBe(false);
    expect(isProductionSourcePath('tests/features/foo.feature')).toBe(false);
  });

  test('the runner itself and other root files are excluded', () => {
    expect(isProductionSourcePath('harness.ts')).toBe(false);
    expect(isProductionSourcePath('README.md')).toBe(false);
    expect(isProductionSourcePath('package.json')).toBe(false);
  });
});

describe('evaluateGherkinGuard (pure decision logic — no git, no fs)', () => {
  test('production-source change with no .feature change triggers', () => {
    const decision = evaluateGherkinGuard({
      changedPaths: ['src/index.ts'],
      hasFeatureFiles: true,
    });
    expect(decision).toEqual({
      skip: false,
      trigger: true,
      override: false,
      changedProductionSources: ['src/index.ts'],
    });
  });

  test('production-source change alongside a .feature change passes', () => {
    const decision = evaluateGherkinGuard({
      changedPaths: ['src/index.ts', 'tests/features/new-thing.feature'],
      hasFeatureFiles: true,
    });
    expect(decision.trigger).toBe(false);
  });

  test('no .feature files anywhere in the template skips entirely, even with a production-source change', () => {
    const decision = evaluateGherkinGuard({
      changedPaths: ['src/index.ts'],
      hasFeatureFiles: false,
    });
    expect(decision).toEqual({
      skip: true,
      trigger: false,
      override: false,
      changedProductionSources: [],
    });
  });

  test('HARNESS_ALLOW_NO_FEATURE=1 overrides a trigger and passes', () => {
    const decision = evaluateGherkinGuard({
      changedPaths: ['src/index.ts'],
      hasFeatureFiles: true,
      overrideEnv: '1',
    });
    expect(decision.trigger).toBe(true);
    expect(decision.override).toBe(true);
  });

  test('an override value other than "1" does not suppress the trigger', () => {
    const decision = evaluateGherkinGuard({
      changedPaths: ['src/index.ts'],
      hasFeatureFiles: true,
      overrideEnv: 'true',
    });
    expect(decision.trigger).toBe(true);
    expect(decision.override).toBe(false);
  });

  test('no production-source changes at all does not trigger, regardless of .feature changes', () => {
    const decision = evaluateGherkinGuard({
      changedPaths: ['README.md', 'harness.ts'],
      hasFeatureFiles: true,
    });
    expect(decision.trigger).toBe(false);
    expect(decision.changedProductionSources).toEqual([]);
  });
});
