import assert from 'node:assert/strict';
import { copyFile, mkdir, readFile, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { Given, Then } from '@cucumber/cucumber';
import { makeTmp } from './crap.steps';

interface BaselineWorld {
  tmp: string;
}

const BASELINE = '.harness-baseline';
const TEMPLATE = join(import.meta.dir, '..', '..', '..');

// CCN 21 — over the template's threshold of 15, so lizard flags exactly one function.
const COMPLEX_TS = `export function stub(n: number): number {
  let t = 0;
${Array.from({ length: 20 }, (_, i) => `  if (n === ${i}) t += ${i};\n`).join('')}  return t;
}
`;

// Two copies of one token-dense body. lizard only reports a duplicate block
// once it spans ~70 unified tokens, so the body is packed with operators and
// calls rather than merely long — line count alone would not trip it.
const DUPLICATED_BODY = [
  '  const s = a * b + c * a - b * c + a / (b + 1) + c * c - a + b;',
  '  const t = Math.max(a, b) * Math.min(b, c) + Math.abs(a - c) + s * 2;',
  '  const u = Math.round(t * 1.5) + Math.floor(s / 2) - Math.ceil(t / 3);',
  '  const v = (u + s) * (t - u) + (a + 1) ** 2 - Math.sqrt(Math.abs(c));',
  '  const w = [s, t, u, v].reduce((acc, n) => acc + n * 2 - 1, 0);',
  '  return Math.round(w + s * t - u / (v + 1) + a * b * c);',
  '}',
  '',
].join('\n');
const DUPLICATED_TS = `${['alpha', 'beta']
  .map((name) => `export function ${name}(a: number, b: number, c: number): number {\n`)
  .map((signature) => signature + DUPLICATED_BODY)
  .join('\n')}
// makeTmp's test file imports \`stub\`; keep it resolvable so a later scenario can
// chain a type-checking command onto this fixture.
export const stub = (n: number): number => n;
`;

async function writeBaselineLine(tmp: string, line: string): Promise<void> {
  await writeFile(join(tmp, BASELINE), `${line}\n`);
}

/**
 * A project that breaks the template's own `no-internal-leak` rule: `src/leak.ts`
 * reaches into `src/internal/`. The real `.dependency-cruiser.json` is copied and
 * `node_modules` symlinked (dependency-cruiser is a devDependency, resolved as
 * `./node_modules/.bin/depcruise` relative to the project under test).
 */
async function makeArchTmp(): Promise<string> {
  const tmp = await makeTmp();
  await copyFile(join(TEMPLATE, '.dependency-cruiser.json'), join(tmp, '.dependency-cruiser.json'));
  await symlink(join(TEMPLATE, 'node_modules'), join(tmp, 'node_modules'));
  await mkdir(join(tmp, 'src', 'internal'));
  await writeFile(join(tmp, 'src', 'internal', 'secret.ts'), 'export const secret = 1;\n');
  await writeFile(
    join(tmp, 'src', 'leak.ts'),
    "import { secret } from './internal/secret';\nexport const leak = secret;\n",
  );
  return tmp;
}

Given('a project with no baseline', async function (this: BaselineWorld) {
  this.tmp = await makeTmp();
});

Given('a project with a CCN-21 function and no baseline', async function (this: BaselineWorld) {
  this.tmp = await makeTmp(COMPLEX_TS);
});

Given(
  'a project with a CCN-21 function and a baseline line {string}',
  async function (this: BaselineWorld, line: string) {
    this.tmp = await makeTmp(COMPLEX_TS);
    await writeBaselineLine(this.tmp, line);
  },
);

Given('a project with a boundary violation and no baseline', async function (this: BaselineWorld) {
  this.tmp = await makeArchTmp();
});

Given(
  'a project with a boundary violation and a baseline line {string}',
  async function (this: BaselineWorld, line: string) {
    this.tmp = await makeArchTmp();
    await writeBaselineLine(this.tmp, line);
  },
);

Given(
  'a project with two copies of the same function and a baseline line {string}',
  async function (this: BaselineWorld, line: string) {
    this.tmp = await makeTmp(DUPLICATED_TS);
    await writeBaselineLine(this.tmp, line);
  },
);

// Chained after a Given from crap.steps.ts that already built `this.tmp`.
Given('the baseline line {string}', async function (this: BaselineWorld, line: string) {
  await writeBaselineLine(this.tmp, line);
});

Then('the baseline file contains {string}', async function (this: BaselineWorld, text: string) {
  const content = await readFile(join(this.tmp, BASELINE), 'utf8');
  assert.ok(content.includes(text), `expected ${JSON.stringify(text)} in ${BASELINE}:\n${content}`);
});
