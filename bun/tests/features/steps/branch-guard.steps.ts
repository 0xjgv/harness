import { copyFile, mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Given } from '@cucumber/cucumber';
import { childEnv } from './crap.steps';

interface BranchGuardWorld {
  tmp: string;
  env?: Record<string, string>;
}

const ZERO = '0'.repeat(40);

// harness.ts resolves ROOT from `import.meta.dir`, so the temp repo gets its
// own copy. `When I run ...`, the exit-code and output steps, and the tmp-dir
// cleanup all come from crap.steps.ts.
const HARNESS_TS = join(import.meta.dir, '..', '..', '..', 'harness.ts');

async function git(cwd: string, args: string[]): Promise<void> {
  const proc = Bun.spawn(['git', ...args], {
    cwd,
    stdout: 'pipe',
    stderr: 'pipe',
    env: childEnv(),
  });
  const code = await proc.exited;
  if (code !== 0) throw new Error(`git ${args.join(' ')} failed (${code})`);
}

Given(
  'a git repository on branch {string}',
  async function (this: BranchGuardWorld, branch: string) {
    this.tmp = await mkdtemp(join(tmpdir(), 'branch-guard-'));
    await copyFile(HARNESS_TS, join(this.tmp, 'harness.ts'));
    await writeFile(join(this.tmp, 'README.md'), '# temp\n');
    await git(this.tmp, ['init', '-b', branch, '-q']);
    await git(this.tmp, ['add', '.']);
    await git(this.tmp, [
      '-c',
      'user.email=harness@example.com',
      '-c',
      'user.name=harness',
      'commit',
      '-q',
      '--no-verify',
      '-m',
      'init',
    ]);
  },
);

Given('the push updates {string}', function (this: BranchGuardWorld, remoteRef: string) {
  this.env = { ...this.env, HARNESS_PRE_PUSH_REFS: `refs/heads/local abc123 ${remoteRef} def456` };
});

Given('the push deletes {string}', function (this: BranchGuardWorld, remoteRef: string) {
  this.env = { ...this.env, HARNESS_PRE_PUSH_REFS: `(delete) ${ZERO} ${remoteRef} def456` };
});

Given('the protected-push override is set', function (this: BranchGuardWorld) {
  this.env = { ...this.env, HARNESS_ALLOW_PROTECTED_PUSH: '1' };
});
