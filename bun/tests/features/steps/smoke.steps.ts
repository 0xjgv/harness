import assert from 'node:assert/strict';
import {
  chmod,
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { delimiter, join, relative, sep } from 'node:path';
import { After, Given, Then, When, type DataTable } from '@cucumber/cucumber';

interface CommandResult {
  exitCode: number;
  stderr: string;
  stdout: string;
}

interface SnapshotEntry {
  content: Buffer;
  mode: number;
  path: string;
}

type Snapshot = SnapshotEntry[];
type MutationSnapshot = Array<[string, Snapshot | null]>;
type OperationRecord = [string, string];

interface SmokeWorld {
  downloadLog: string;
  downloadsBefore: Buffer;
  downloadsBeforeReruns: Buffer;
  error: unknown;
  managedSnapshotAfterOffline: Snapshot;
  managedSnapshotAfterOnline: Snapshot;
  managedSnapshotBefore: Snapshot;
  module: unknown;
  mutationsBefore: MutationSnapshot;
  offlineOperations: OperationRecord[];
  offlineResult: CommandResult;
  onlineOperations: OperationRecord[];
  onlineResult: CommandResult;
  operationLog: string;
  poisonBin: string;
  poisonLog: string;
  result: CommandResult;
  workspaceRoot: string;
}

const TEMPLATE_ROOT = join(import.meta.dir, '..', '..', '..');
const MAKE = Bun.which('make');
if (MAKE === null) throw new Error('make is required for workspace acceptance tests');

const FAKE_WORKSPACE = `#!/bin/sh
set -eu

case $0 in
  */*) script_dir=\${0%/*} ;;
  *) script_dir=. ;;
esac
root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
state=$root/.fake-workspace
logs=$root/.fake-logs
offline=\${OFFLINE:-0}
mkdir -p "$logs"
printf '%s\t%s\n' "$offline" "$*" >>"$logs/operations"

require_bun_profile() {
  [ "$#" -eq 2 ] && [ "$2" = bun ] || {
    printf '%s\n' 'fake workspace: expected the Bun profile' >&2
    exit 64
  }
}

case \${1:-} in
  preflight)
    require_bun_profile "$@"
    if [ -f "$root/.fake-unmanaged-hook" ]; then
      printf '%s\n' 'fake workspace: unmanaged hook' >&2
      exit 65
    fi
    ;;
  install)
    require_bun_profile "$@"
    if [ "$offline" = 1 ] && [ ! -f "$state/cache/tools" ]; then
      printf '%s\n' 'fake workspace: cold offline tool cache' >&2
      exit 66
    fi
    if [ ! -f "$state/cache/tools" ]; then
      printf '%s\n' 'bun tools' >>"$logs/downloads"
      mkdir -p "$state/cache"
      printf '%s\n' 'cached Bun tools' >"$state/cache/tools"
    fi
    if [ ! -f "$state/tools/bun" ]; then
      mkdir -p "$state/tools"
      printf '%s\n' 'managed Bun profile' >"$state/tools/bun"
    fi
    ;;
  exec)
    [ "\${2:-}" = bun ] && [ "\${3:-}" = -- ] || {
      printf '%s\n' 'fake workspace: malformed managed exec' >&2
      exit 67
    }
    [ -f "$state/tools/bun" ] || {
      printf '%s\n' 'fake workspace: tools are not installed' >&2
      exit 68
    }
    shift 3
    case $* in
      'bun install --frozen-lockfile')
        [ "$offline" = 0 ] || {
          printf '%s\n' 'fake workspace: offline dependency restore lacks --offline' >&2
          exit 69
        }
        if [ ! -f "$state/cache/dependencies" ]; then
          printf '%s\n' 'bun dependencies' >>"$logs/downloads"
          mkdir -p "$state/cache"
          printf '%s\n' 'cached frozen dependencies' >"$state/cache/dependencies"
        fi
        mkdir -p "$state/dependencies"
        if [ ! -f "$state/dependencies/frozen" ]; then
          printf '%s\n' 'frozen dependencies' >"$state/dependencies/frozen"
        fi
        ;;
      'bun install --frozen-lockfile --offline')
        [ "$offline" = 1 ] || {
          printf '%s\n' 'fake workspace: online dependency restore used --offline' >&2
          exit 70
        }
        [ -f "$state/cache/dependencies" ] || {
          printf '%s\n' 'fake workspace: cold offline dependency cache' >&2
          exit 71
        }
        mkdir -p "$state/dependencies"
        if [ ! -f "$state/dependencies/frozen" ]; then
          printf '%s\n' 'frozen dependencies' >"$state/dependencies/frozen"
        fi
        ;;
      'bun harness.ts check')
        [ -f "$state/dependencies/frozen" ] || {
          printf '%s\n' 'fake workspace: checks ran before dependencies' >&2
          exit 72
        }
        ;;
      *)
        printf 'fake workspace: unexpected managed command: %s\n' "$*" >&2
        exit 73
        ;;
    esac
    ;;
  sync-skills)
    [ "$#" -eq 1 ] || exit 74
    [ -f "$state/tools/bun" ] && [ -f "$state/dependencies/frozen" ] || exit 75
    if [ ! -f "$state/skills/harness/SKILL.md" ]; then
      mkdir -p "$state/skills/harness"
      printf '%s\n' 'managed harness skill' >"$state/skills/harness/SKILL.md"
    fi
    ;;
  install-hooks)
    [ "$#" -eq 1 ] || exit 76
    [ -f "$state/tools/bun" ] && [ -f "$state/dependencies/frozen" ] || exit 77
    if [ ! -f "$state/hooks/pre-commit" ]; then
      mkdir -p "$state/hooks"
      printf '%s\n' '#!/bin/sh' 'exit 0' >"$state/hooks/pre-commit"
      chmod +x "$state/hooks/pre-commit"
    fi
    ;;
  verify)
    require_bun_profile "$@"
    [ -f "$state/tools/bun" ] || exit 78
    [ -f "$state/dependencies/frozen" ] || exit 79
    [ -f "$state/hooks/pre-commit" ] || exit 80
    [ -f "$state/skills/harness/SKILL.md" ] || exit 81
    ;;
  *)
    printf 'fake workspace: unexpected operation: %s\n' "\${1:-}" >&2
    exit 82
    ;;
esac
`;

const POISON_BUN = `#!/bin/sh
printf '%s\n' 'ambient bun executed' >>"$POISON_BUN_LOG"
exit 97
`;

async function writeExecutable(path: string, content: string): Promise<void> {
  await writeFile(path, content);
  await chmod(path, 0o755);
}

async function snapshotTree(root: string): Promise<Snapshot> {
  const entries: Snapshot = [];

  async function visit(directory: string): Promise<void> {
    const children = await readdir(directory, { withFileTypes: true });
    children.sort((left, right) => left.name.localeCompare(right.name));
    for (const child of children) {
      const path = join(directory, child.name);
      const metadata = await stat(path);
      const relativePath = relative(root, path).split(sep).join('/');
      if (child.isDirectory()) {
        entries.push({
          content: Buffer.alloc(0),
          mode: metadata.mode & 0o777,
          path: `${relativePath}/`,
        });
        await visit(path);
      } else {
        entries.push({
          content: await readFile(path),
          mode: metadata.mode & 0o777,
          path: relativePath,
        });
      }
    }
  }

  try {
    await visit(root);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw error;
  }
  return entries;
}

async function managedSnapshot(root: string): Promise<Snapshot> {
  return snapshotTree(join(root, '.fake-workspace'));
}

async function mutationSnapshot(root: string): Promise<MutationSnapshot> {
  const state = join(root, '.fake-workspace');
  const mutations: MutationSnapshot = [];
  for (const name of ['hooks', 'skills']) {
    const path = join(state, name);
    try {
      await stat(path);
      mutations.push([name, await snapshotTree(path)]);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      mutations.push([name, null]);
    }
  }
  return mutations;
}

async function readLines(path: string): Promise<string[]> {
  const content = await readFile(path, 'utf8');
  return content === '' ? [] : content.trimEnd().split('\n');
}

async function makeDryRun(root: string, target: string): Promise<CommandResult> {
  const proc = Bun.spawn(
    [MAKE, '--no-print-directory', '--dry-run', target, 'OFFLINE=1'],
    {
      cwd: root,
      stderr: 'pipe',
      stdout: 'pipe',
    },
  );
  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  return { exitCode, stderr, stdout };
}

async function assertMakeDispatchContract(root: string): Promise<void> {
  const setupHooks = await makeDryRun(root, 'setup-hooks');
  assertCommandSucceeded(setupHooks);
  assert.equal(
    setupHooks.stdout.trim(),
    'OFFLINE=1 .harness/workspace.sh install-hooks',
  );
  assert.doesNotMatch(setupHooks.stdout, /\bexec bun\b|\bbun harness\.ts\b/);

  const check = await makeDryRun(root, 'check');
  assertCommandSucceeded(check);
  assert.equal(
    check.stdout.trim(),
    'OFFLINE=1 .harness/workspace.sh exec bun -- bun harness.ts check',
  );
}

async function newIsolatedRepository(context: SmokeWorld): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), 'bun-workspace-'));
  context.workspaceRoot = root;
  await copyFile(join(TEMPLATE_ROOT, 'Makefile'), join(root, 'Makefile'));
  await mkdir(join(root, '.harness'));
  await writeExecutable(join(root, '.harness', 'workspace.sh'), FAKE_WORKSPACE);
  await assertMakeDispatchContract(root);

  const poisonBin = join(root, '.poison-bin');
  await mkdir(poisonBin);
  await writeExecutable(join(poisonBin, 'bun'), POISON_BUN);

  const logs = join(root, '.fake-logs');
  await mkdir(logs);
  const operationLog = join(logs, 'operations');
  const downloadLog = join(logs, 'downloads');
  const poisonLog = join(logs, 'poison-bun');
  await Promise.all([
    writeFile(operationLog, ''),
    writeFile(downloadLog, ''),
    writeFile(poisonLog, ''),
  ]);

  context.operationLog = operationLog;
  context.downloadLog = downloadLog;
  context.poisonLog = poisonLog;
  context.poisonBin = poisonBin;
  context.mutationsBefore = await mutationSnapshot(root);
  context.downloadsBefore = await readFile(downloadLog);
}

async function runMake(
  context: SmokeWorld,
  target: string,
  offline: boolean,
): Promise<CommandResult> {
  const proc = Bun.spawn([MAKE, '--no-print-directory', target], {
    cwd: context.workspaceRoot,
    env: {
      ...process.env,
      OFFLINE: offline ? '1' : '0',
      PATH: `${context.poisonBin}${delimiter}${process.env.PATH ?? ''}`,
      POISON_BUN_LOG: context.poisonLog,
    },
    stderr: 'pipe',
    stdout: 'pipe',
  });
  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  return { exitCode, stderr, stdout };
}

async function operationRecords(path: string): Promise<OperationRecord[]> {
  return (await readLines(path)).map((line) => {
    const separator = line.indexOf('\t');
    assert.notEqual(separator, -1, `malformed operation record: ${JSON.stringify(line)}`);
    return [line.slice(0, separator), line.slice(separator + 1)] as OperationRecord;
  });
}

function assertCommandSucceeded(result: CommandResult): void {
  assert.equal(
    result.exitCode,
    0,
    `expected workspace success, got ${result.exitCode}\n` +
      `--- stdout ---\n${result.stdout}\n--- stderr ---\n${result.stderr}`,
  );
}

async function assertPoisonUnused(context: SmokeWorld): Promise<void> {
  assert.equal(
    await readFile(context.poisonLog, 'utf8'),
    '',
    'workspace invoked poisoned ambient bun',
  );
}

Given('a fresh runtime', function (this: SmokeWorld) {
  this.error = null;
  this.module = null;
});

When('I import src', async function (this: SmokeWorld) {
  try {
    this.module = await import('../../../src/index');
  } catch (exc) {
    this.error = exc;
  }
});

Then('no exception is raised', function (this: SmokeWorld) {
  assert.equal(this.error, null, `unexpected error: ${this.error}`);
  assert.notEqual(this.module, null);
});

Given('an isolated clean Bun template repository', async function (this: SmokeWorld) {
  await newIsolatedRepository(this);
});

When('I run the Bun workspace target online', async function (this: SmokeWorld) {
  this.result = await runMake(this, 'workspace', false);
});

Then('the workspace command succeeds', async function (this: SmokeWorld) {
  assertCommandSucceeded(this.result);
  await assertPoisonUnused(this);
});

Then(
  'the workspace operations run in order:',
  async function (this: SmokeWorld, table: DataTable) {
    const records = await operationRecords(this.operationLog);
    const actual = records.map(([, operation]) => operation);
    const expected = table.hashes().map((row) => row.operation);
    assert.deepEqual(actual, expected);
    assert.ok(records.every(([offline]) => offline === '0'));
  },
);

Given('an isolated warm Bun template repository', async function (this: SmokeWorld) {
  await newIsolatedRepository(this);
  const initialResult = await runMake(this, 'workspace', false);
  assertCommandSucceeded(initialResult);
  await assertPoisonUnused(this);
  this.managedSnapshotBefore = await managedSnapshot(this.workspaceRoot);
  this.downloadsBeforeReruns = await readFile(this.downloadLog);
  await writeFile(this.operationLog, '');
});

When(
  'I rerun the Bun workspace target online and bootstrap offline',
  async function (this: SmokeWorld) {
    this.onlineResult = await runMake(this, 'workspace', false);
    this.onlineOperations = await operationRecords(this.operationLog);
    this.managedSnapshotAfterOnline = await managedSnapshot(this.workspaceRoot);
    await writeFile(this.operationLog, '');
    this.offlineResult = await runMake(this, 'bootstrap', true);
    this.offlineOperations = await operationRecords(this.operationLog);
    this.managedSnapshotAfterOffline = await managedSnapshot(this.workspaceRoot);
  },
);

Then('both workspace commands succeed', async function (this: SmokeWorld) {
  assertCommandSucceeded(this.onlineResult);
  assertCommandSucceeded(this.offlineResult);
  await assertPoisonUnused(this);
});

Then('online dependencies use {string}', function (this: SmokeWorld, command: string) {
  const expected: OperationRecord = ['0', `exec bun -- ${command}`];
  assert.equal(
    this.onlineOperations.filter(
      ([offline, operation]) => offline === expected[0] && operation === expected[1],
    ).length,
    1,
  );
});

Then('offline dependencies use {string}', function (this: SmokeWorld, command: string) {
  const expected: OperationRecord = ['1', `exec bun -- ${command}`];
  assert.equal(
    this.offlineOperations.filter(
      ([offline, operation]) => offline === expected[0] && operation === expected[1],
    ).length,
    1,
  );
  assert.ok(this.offlineOperations.every(([offline]) => offline === '1'));
});

Then('the managed workspace snapshot is unchanged', async function (this: SmokeWorld) {
  assert.deepEqual(this.managedSnapshotAfterOnline, this.managedSnapshotBefore);
  assert.deepEqual(this.managedSnapshotAfterOffline, this.managedSnapshotBefore);
  assert.deepEqual(await readFile(this.downloadLog), this.downloadsBeforeReruns);
});

Given('an isolated cold offline Bun template repository', async function (this: SmokeWorld) {
  await newIsolatedRepository(this);
});

When('I run the Bun workspace target offline', async function (this: SmokeWorld) {
  this.result = await runMake(this, 'workspace', true);
});

Then('the workspace command fails during tool installation', async function (this: SmokeWorld) {
  assert.notEqual(this.result.exitCode, 0);
  assert.deepEqual(await operationRecords(this.operationLog), [
    ['1', 'preflight bun'],
    ['1', 'install bun'],
  ]);
  assert.match(this.result.stderr, /cold offline tool cache/);
  await assertPoisonUnused(this);
});

Then('neither hooks nor skills are modified', async function (this: SmokeWorld) {
  assert.deepEqual(await mutationSnapshot(this.workspaceRoot), this.mutationsBefore);
});

Given(
  'an isolated Bun template repository with an unmanaged hook',
  async function (this: SmokeWorld) {
    await newIsolatedRepository(this);
    await writeFile(join(this.workspaceRoot, '.fake-unmanaged-hook'), 'unmanaged\n');
  },
);

Then('the workspace command fails during preflight', async function (this: SmokeWorld) {
  assert.notEqual(this.result.exitCode, 0);
  assert.deepEqual(await operationRecords(this.operationLog), [['0', 'preflight bun']]);
  assert.match(this.result.stderr, /unmanaged hook/);
  await assertPoisonUnused(this);
});

Then('no tools are downloaded', async function (this: SmokeWorld) {
  assert.deepEqual(await readFile(this.downloadLog), this.downloadsBefore);
});

After(async function (this: SmokeWorld) {
  if (this.workspaceRoot) await rm(this.workspaceRoot, { force: true, recursive: true });
});
