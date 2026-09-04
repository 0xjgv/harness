import { describe, expect, test } from 'bun:test';
import { isMutableSourceFile, mutationScore, tallyMutants } from '../harness';

function report(...statuses: string[]): unknown {
  return { files: { 'src/a.ts': { mutants: statuses.map((status) => ({ status })) } } };
}

describe('tallyMutants', () => {
  test('counts Killed and Timeout as killed, Survived as survived', () => {
    expect(tallyMutants(report('Killed', 'Timeout', 'Survived'))).toEqual({
      killed: 2,
      survived: 1,
    });
  });

  test('excludes mutants that never ran from both sides', () => {
    // A NoCoverage/Ignored/CompileError mutant says nothing about the tests, so
    // counting it as survived would punish a repo for Stryker's own limitations.
    const tally = tallyMutants(report('NoCoverage', 'Ignored', 'CompileError', 'RuntimeError'));
    expect(tally).toEqual({ killed: 0, survived: 0 });
  });

  test('sums across files and tolerates a file with no mutants', () => {
    const payload = {
      files: {
        'src/a.ts': { mutants: [{ status: 'Killed' }] },
        'src/b.ts': { mutants: [{ status: 'Survived' }, { status: 'Killed' }] },
        'src/c.ts': {},
      },
    };
    expect(tallyMutants(payload)).toEqual({ killed: 2, survived: 1 });
  });

  test('returns null when the payload is not a report', () => {
    expect(tallyMutants(null)).toBeNull();
    expect(tallyMutants({})).toBeNull();
    expect(tallyMutants({ files: null })).toBeNull();
  });
});

describe('mutationScore', () => {
  test('is the rounded percentage of graded mutants killed', () => {
    expect(mutationScore({ killed: 1, survived: 1 })).toBe(50);
    expect(mutationScore({ killed: 2, survived: 1 })).toBe(67);
    expect(mutationScore({ killed: 1, survived: 2 })).toBe(33);
    expect(mutationScore({ killed: 3, survived: 0 })).toBe(100);
    expect(mutationScore({ killed: 0, survived: 3 })).toBe(0);
  });
});

describe('isMutableSourceFile', () => {
  test('accepts application sources only', () => {
    expect(isMutableSourceFile('src/index.ts')).toBe(true);
    expect(isMutableSourceFile('src/nested/thing.ts')).toBe(true);
  });

  test('rejects tests, the runner, and non-TypeScript files', () => {
    expect(isMutableSourceFile('src/index.test.ts')).toBe(false);
    expect(isMutableSourceFile('src/index.spec.ts')).toBe(false);
    expect(isMutableSourceFile('tests/smoke.test.ts')).toBe(false);
    expect(isMutableSourceFile('harness.ts')).toBe(false);
    expect(isMutableSourceFile('src/index.js')).toBe(false);
  });
});
