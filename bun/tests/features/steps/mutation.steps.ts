import { copyFile, symlink } from 'node:fs/promises';
import { join } from 'node:path';
import { Given } from '@cucumber/cucumber';
import { makeTmp } from './crap.steps';

interface MutationWorld {
  tmp: string;
}

const TEMPLATE_ROOT = join(import.meta.dir, '..', '..', '..');

// `stub` is asserted by the test, so its mutants die; `unreferenced` is never
// called, so with coverageAnalysis "off" its mutants still run and survive.
// That pins the score strictly between 0 and 100 without asserting a number —
// mutant counts shift on a Stryker bump, exit codes and labels do not.
const PARTLY_TESTED_TS = `export function stub(n: number): number {
  if (n === 0) return 0;
  return n + 1;
}

export function unreferenced(n: number): number {
  return n * 3;
}
`;

const PARTLY_TESTED_TEST_TS = `import { expect, test } from 'bun:test';
import { stub } from '../src/stub';

test('stub', () => {
  expect(stub(0)).toBe(0);
  expect(stub(1)).toBe(2);
});
`;

Given('a project with a partly tested source file', async function (this: MutationWorld) {
  this.tmp = await makeTmp(PARTLY_TESTED_TS, PARTLY_TESTED_TEST_TS);
  // A real Stryker run is the only honest test of the gate. At this fixture's
  // size it takes about a second, so the template's node_modules is linked
  // (never copied, never reinstalled) and its config copied alongside.
  await symlink(join(TEMPLATE_ROOT, 'node_modules'), join(this.tmp, 'node_modules'));
  await copyFile(join(TEMPLATE_ROOT, 'stryker.conf.json'), join(this.tmp, 'stryker.conf.json'));
});
