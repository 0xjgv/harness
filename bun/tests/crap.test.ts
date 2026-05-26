import { describe, expect, test } from 'bun:test';
import { crapScore, parseLcov } from '../harness';

describe('crapScore', () => {
  test('full coverage reduces to ccn', () => {
    expect(crapScore(10, 1.0)).toBe(10);
  });

  test('zero coverage at ccn=10 is 110', () => {
    expect(crapScore(10, 0.0)).toBe(110);
  });

  test('half coverage at ccn=10 is 22.5', () => {
    expect(crapScore(10, 0.5)).toBe(22.5);
  });

  test('minimal ccn at zero coverage is 2', () => {
    expect(crapScore(1, 0.0)).toBe(2);
  });
});

describe('parseLcov', () => {
  test('parses multi-file LCOV into file -> line -> hits map', () => {
    const input = [
      'TN:',
      'SF:src/foo.ts',
      'DA:1,3',
      'DA:2,0',
      'DA:5,1',
      'end_of_record',
      'SF:src/bar.ts',
      'DA:10,7',
      'end_of_record',
    ].join('\n');
    expect(parseLcov(input)).toEqual({
      'src/foo.ts': { 1: 3, 2: 0, 5: 1 },
      'src/bar.ts': { 10: 7 },
    });
  });
});
