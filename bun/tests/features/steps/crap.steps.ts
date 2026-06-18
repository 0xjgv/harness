import assert from 'node:assert/strict';
import { copyFile, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { After, Given, Then, When } from '@cucumber/cucumber';

interface CrapWorld {
  tmp: string;
  result?: { exitCode: number; output: string };
}

// harness.ts uses `import.meta.dir` as ROOT, so isolating to a tmp dir means
// copying harness.ts there and invoking it locally — anything else would
// resolve paths against the real bun/ template.
const HARNESS_TS = join(import.meta.dir, '..', '..', '..', 'harness.ts');

// Function with 9 branches (CCN ~9): paired with hits=0 lines this scores
// CRAP = 9² × 1³ + 9 = 90, well above --max=0.
const STUB_TS = `export function stub(n: number): number {
  if (n < 1) return 0;
  if (n < 2) return 1;
  if (n < 3) return 2;
  if (n < 4) return 3;
  if (n < 5) return 4;
  if (n < 6) return 5;
  if (n < 7) return 6;
  if (n < 8) return 7;
  return 8;
}
`;

const ZERO_COVERAGE_LCOV = `SF:src/stub.ts
DA:1,0
DA:2,0
DA:3,0
DA:4,0
DA:5,0
DA:6,0
DA:7,0
DA:8,0
DA:9,0
DA:10,0
DA:11,0
end_of_record
`;

async function makeTmp(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'crap-'));
  await mkdir(join(dir, 'src'));
  await mkdir(join(dir, 'tests'));
  await writeFile(join(dir, 'src', 'stub.ts'), STUB_TS);
  await writeFile(
    join(dir, 'tests', 'stub.test.ts'),
    "import { test, expect } from 'bun:test';\nimport { stub } from '../src/stub';\ntest('stub', () => expect(stub(0)).toBe(0));\n",
  );
  await copyFile(HARNESS_TS, join(dir, 'harness.ts'));
  return dir;
}

Given(
  'a coverage artifact for a high-CCN, zero-coverage function',
  async function (this: CrapWorld) {
    this.tmp = await makeTmp();
    await mkdir(join(this.tmp, 'coverage'));
    await writeFile(join(this.tmp, 'coverage', 'lcov.info'), ZERO_COVERAGE_LCOV);
  },
);

Given('no coverage artifact', async function (this: CrapWorld) {
  this.tmp = await makeTmp();
});

When('I run {string}', async function (this: CrapWorld, cmd: string) {
  // Drop leading "harness" — the rest is forwarded to `bun harness.ts`.
  const argv = cmd.split(/\s+/).slice(1);
  const proc = Bun.spawn(['bun', 'harness.ts', ...argv], {
    cwd: this.tmp,
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const [stdout, stderr] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
  ]);
  this.result = { exitCode: await proc.exited, output: stdout + stderr };
});

Then('the exit code is {int}', function (this: CrapWorld, code: number) {
  assert.equal(
    this.result?.exitCode,
    code,
    `expected exit ${code}, got ${this.result?.exitCode}\n--- output ---\n${this.result?.output}`,
  );
});

Then('the output contains {string}', function (this: CrapWorld, text: string) {
  assert.ok(
    this.result?.output.includes(text),
    `expected ${JSON.stringify(text)} in output:\n${this.result?.output}`,
  );
});

Then('the output does not contain {string}', function (this: CrapWorld, text: string) {
  assert.ok(
    !this.result?.output.includes(text),
    `unexpected ${JSON.stringify(text)} in output:\n${this.result?.output}`,
  );
});

After(async function (this: CrapWorld) {
  if (this.tmp) await rm(this.tmp, { recursive: true, force: true });
});
