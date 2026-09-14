import { afterEach, describe, expect, test } from 'bun:test';
import { copyFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const HARNESS_TS = join(import.meta.dir, '..', 'harness.ts');
const ZERO = '0'.repeat(40);

// The guard must never inherit a reviewer's override from the ambient shell.
const OVERRIDE_ENV = [
  'HARNESS_ALLOW_PROTECTED_PUSH',
  'HARNESS_ALLOW_ARCH_CONFIG',
  'HARNESS_PRE_PUSH_REFS',
];

function childEnv(extra: Record<string, string> = {}): Record<string, string | undefined> {
  const env: Record<string, string | undefined> = { ...process.env };
  for (const key of OVERRIDE_ENV) delete env[key];
  return { ...env, ...extra };
}

async function git(cwd: string, args: string[]): Promise<string> {
  const proc = Bun.spawn(['git', ...args], {
    cwd,
    stdout: 'pipe',
    stderr: 'pipe',
    env: childEnv(),
  });
  const [out, code] = await Promise.all([new Response(proc.stdout).text(), proc.exited]);
  if (code !== 0) throw new Error(`git ${args.join(' ')} failed (${code})`);
  return out.trim();
}

async function commit(cwd: string, message: string): Promise<string> {
  await git(cwd, ['add', '.']);
  await git(cwd, [
    '-c',
    'user.email=harness@example.com',
    '-c',
    'user.name=harness',
    'commit',
    '-q',
    '--no-verify',
    '-m',
    message,
  ]);
  return await git(cwd, ['rev-parse', 'HEAD']);
}

let roots: string[] = [];

afterEach(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
  roots = [];
});

describe('arch config guard on a new-branch push', () => {
  test('inspects the whole branch, not just its tip commit', async () => {
    const root = mkdtempSync(join(tmpdir(), 'arch-guard-'));
    roots.push(root);
    copyFileSync(HARNESS_TS, join(root, 'harness.ts'));
    await git(root, ['init', '-b', 'main', '-q', '.']);

    writeFileSync(join(root, 'README.md'), '# temp\n');
    const base = await commit(root, 'base');
    await git(root, ['update-ref', 'refs/remotes/origin/main', base]);

    // The arch config change is buried mid-branch: the tip commit alone misses it.
    writeFileSync(join(root, '.dependency-cruiser.json'), '{}\n');
    await commit(root, 'arch config');
    writeFileSync(join(root, 'notes.txt'), 'unrelated\n');
    const tip = await commit(root, 'unrelated');

    const proc = Bun.spawn(['bun', 'harness.ts', 'arch-config-guard'], {
      cwd: root,
      stdout: 'pipe',
      stderr: 'pipe',
      env: childEnv({
        HARNESS_PRE_PUSH_REFS: `refs/heads/topic ${tip} refs/heads/topic ${ZERO}\n`,
      }),
    });
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
      proc.exited,
    ]);

    const output = stdout + stderr;
    expect(output).toContain('Arch config changed: .dependency-cruiser.json');
    expect(exitCode).toBe(1);
  });
});
