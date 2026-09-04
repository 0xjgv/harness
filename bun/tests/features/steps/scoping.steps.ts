import { appendFile, copyFile, mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Given } from '@cucumber/cucumber';

interface ScopingWorld {
  tmp: string;
}

// harness.ts uses `import.meta.dir` as ROOT, so the scratch project gets its own
// copy; the change set is read with git, so the project has to be a repository.
const HARNESS_TS = join(import.meta.dir, '..', '..', '..', 'harness.ts');

const STUB_TS = 'export function stub(n: number): number {\n  return n;\n}\n';
const STUB_TEST_TS = [
  "import { expect, test } from 'bun:test';",
  "import { stub } from '../src/stub';",
  "test('stub', () => expect(stub(0)).toBe(0));",
  '',
].join('\n');

async function git(cwd: string, args: string[]): Promise<void> {
  const proc = Bun.spawn(['git', ...args], { cwd, stdout: 'pipe', stderr: 'pipe' });
  if ((await proc.exited) !== 0) throw new Error(`git ${args.join(' ')} failed in ${cwd}`);
}

async function commitAll(dir: string, message: string): Promise<void> {
  await git(dir, ['add', '-A']);
  await git(dir, [
    '-c',
    'user.email=harness@example.com',
    '-c',
    'user.name=harness',
    '-c',
    'commit.gpgsign=false',
    'commit',
    '-qm',
    message,
  ]);
}

async function committedProject(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'scoping-'));
  await mkdir(join(dir, 'src'));
  await mkdir(join(dir, 'tests'));
  await writeFile(join(dir, 'src', 'stub.ts'), STUB_TS);
  await writeFile(join(dir, 'tests', 'stub.test.ts'), STUB_TEST_TS);
  await copyFile(HARNESS_TS, join(dir, 'harness.ts'));
  await git(dir, ['init', '-q']);
  await commitAll(dir, 'init');
  return dir;
}

Given('a committed project with a test for each source', async function (this: ScopingWorld) {
  this.tmp = await committedProject();
});

Given('a committed project with one source edited', async function (this: ScopingWorld) {
  this.tmp = await committedProject();
  await appendFile(join(this.tmp, 'src', 'stub.ts'), '// edited\n');
});

Given(
  'a committed project with one source edited in the last commit',
  async function (this: ScopingWorld) {
    this.tmp = await committedProject();
    await appendFile(join(this.tmp, 'src', 'stub.ts'), '// edited\n');
    await commitAll(this.tmp, 'edit stub');
  },
);

Given('a committed project with an untested source added', async function (this: ScopingWorld) {
  this.tmp = await committedProject();
  await writeFile(join(this.tmp, 'src', 'orphan.ts'), 'export const orphan = 1;\n');
});
