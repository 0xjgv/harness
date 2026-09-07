import { copyFile, mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Given } from '@cucumber/cucumber';

// The `When I run` / `Then the exit code is` / `the output contains` steps and the
// After hook that removes `this.tmp` are shared with crap.steps.ts.
interface PinWorld {
  tmp: string;
}

// harness.ts uses `import.meta.dir` as ROOT, so the pin it reads is the
// package.json next to the copy under test — not the template's own.
const HARNESS_TS = join(import.meta.dir, '..', '..', '..', 'harness.ts');

async function projectWithManifest(manifest: Record<string, unknown>): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'pins-'));
  await writeFile(join(dir, 'package.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  await copyFile(HARNESS_TS, join(dir, 'harness.ts'));
  return dir;
}

Given('a project pinned to bun {string}', async function (this: PinWorld, version: string) {
  this.tmp = await projectWithManifest({ name: 'pinned', packageManager: `bun@${version}` });
});

Given('a project pinned to the running bun version', async function (this: PinWorld) {
  this.tmp = await projectWithManifest({ name: 'pinned', packageManager: `bun@${Bun.version}` });
});

Given('a project with no packageManager field', async function (this: PinWorld) {
  this.tmp = await projectWithManifest({ name: 'unpinned' });
});
