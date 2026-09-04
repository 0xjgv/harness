import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { Given, Then } from '@cucumber/cucumber';
import { makeTmp } from './crap.steps';

interface BaselineWorld {
  tmp: string;
}

const BASELINE = '.harness-baseline';

// CCN 21 — over the template's threshold of 15, so lizard flags exactly one function.
const COMPLEX_TS = `export function stub(n: number): number {
  let t = 0;
${Array.from({ length: 20 }, (_, i) => `  if (n === ${i}) t += ${i};\n`).join('')}  return t;
}
`;

async function writeBaselineLine(tmp: string, line: string): Promise<void> {
  await writeFile(join(tmp, BASELINE), `${line}\n`);
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

// Chained after a Given from crap.steps.ts that already built `this.tmp`.
Given('the baseline line {string}', async function (this: BaselineWorld, line: string) {
  await writeBaselineLine(this.tmp, line);
});

Then('the baseline file contains {string}', async function (this: BaselineWorld, text: string) {
  const content = await readFile(join(this.tmp, BASELINE), 'utf8');
  assert.ok(content.includes(text), `expected ${JSON.stringify(text)} in ${BASELINE}:\n${content}`);
});
