/**
 * Property-based tests for the pure helpers in harness.ts.
 *
 * Worked example for the template's PBT convention: law-like behavior
 * (formulas, parsers, round-trips) gets a property, not just examples.
 * Examples pin known cases; properties pin the law.
 */
import { describe, expect, test } from 'bun:test';
import fc from 'fast-check';
import { crapScore, parseLcov, parseLineForSuppressions } from '../harness';

const ccnArb = fc.integer({ min: 1, max: 100 });
const covArb = fc.double({ min: 0, max: 1, noNaN: true });

describe('crapScore properties', () => {
  test('full coverage collapses to ccn', () => {
    fc.assert(fc.property(ccnArb, (ccn) => crapScore(ccn, 1.0) === ccn));
  });

  test('bounded below by ccn, above by the zero-coverage score', () => {
    fc.assert(
      fc.property(ccnArb, covArb, (ccn, cov) => {
        const score = crapScore(ccn, cov);
        return score >= ccn && score <= ccn * ccn + ccn;
      }),
    );
  });

  test('more coverage never raises the score', () => {
    fc.assert(
      fc.property(ccnArb, covArb, covArb, (ccn, a, b) => {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        return crapScore(ccn, lo) >= crapScore(ccn, hi);
      }),
    );
  });

  test('more complexity never lowers the score', () => {
    fc.assert(
      fc.property(ccnArb, ccnArb, covArb, (a, b, cov) => {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        return crapScore(lo, cov) <= crapScore(hi, cov);
      }),
    );
  });
});

describe('parseLineForSuppressions properties', () => {
  const KINDS = new Set([
    'ts-ignore',
    'ts-expect-error',
    'ts-nocheck',
    'eslint-disable',
    'biome-ignore',
  ]);

  test('total on arbitrary text: never throws, every match has a known kind', () => {
    fc.assert(
      fc.property(fc.string(), (line) => {
        for (const m of parseLineForSuppressions(line)) {
          if (!KINDS.has(m.kind) || !Array.isArray(m.rules)) return false;
        }
        return true;
      }),
    );
  });

  test('no comment marker means no match', () => {
    fc.assert(
      fc.property(
        fc.string().map((s) => s.replaceAll('/', ' ')),
        (line) => parseLineForSuppressions(line).length === 0,
      ),
    );
  });

  test('eslint-disable rules round-trip', () => {
    const ruleArb = fc.stringMatching(/^[a-z][a-z0-9-]{1,15}$/);
    fc.assert(
      fc.property(fc.uniqueArray(ruleArb, { minLength: 1, maxLength: 4 }), (rules) => {
        // Token split so the suppression report does not count this line.
        const line = ['foo(); // eslint', `disable-line: ${rules.join(', ')}`].join('-');
        return parseLineForSuppressions(line).some(
          (m) => m.kind === 'eslint-disable' && m.rules.join(',') === rules.join(','),
        );
      }),
    );
  });
});

describe('parseLcov properties', () => {
  const fileArb = fc.stringMatching(/^[a-zA-Z0-9._-]{1,12}(\/[a-zA-Z0-9._-]{1,12}){0,2}$/);
  const linesArb = fc.dictionary(
    fc.integer({ min: 1, max: 9999 }).map(String),
    fc.integer({ min: 0, max: 1000 }),
    { minKeys: 1, maxKeys: 20 },
  );
  const covMapArb = fc
    .uniqueArray(fc.tuple(fileArb, linesArb), {
      minLength: 1,
      maxLength: 5,
      selector: (pair) => pair[0],
    })
    .map((pairs) => Object.fromEntries(pairs));

  function renderLcov(cov: Record<string, Record<string, number>>): string {
    const out: string[] = ['TN:'];
    for (const [file, lines] of Object.entries(cov)) {
      out.push(`SF:${file}`);
      for (const [num, hits] of Object.entries(lines)) out.push(`DA:${num},${hits}`);
      out.push('end_of_record');
    }
    return out.join('\n');
  }

  test('generated LCOV round-trips', () => {
    fc.assert(
      fc.property(covMapArb, (cov) => {
        expect(parseLcov(renderLcov(cov))).toEqual(cov);
      }),
    );
  });

  test('total on arbitrary text: never throws, values are numeric maps', () => {
    fc.assert(
      fc.property(fc.string(), (text) => {
        const result = parseLcov(text);
        return Object.values(result).every((lines) =>
          Object.values(lines).every((h) => typeof h === 'number'),
        );
      }),
    );
  });
});
