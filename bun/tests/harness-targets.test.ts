import { afterEach, describe, expect, test } from 'bun:test';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import {
  appTargets,
  biomeCommand,
  complexityGate,
  deadcodeGate,
  existingTargets,
  type Gate,
  hasTests,
  isProjectTsFile,
  isQualityTsFile,
  isTestFile,
  lintGate,
  lizardCsvCommand,
  porcelainPath,
  qualityTargets,
  runGatesParallel,
  setupHooksCommand,
  typecheckGate,
} from '../harness';

function tempProject(withTests = false): string {
  const root = mkdtempSync(join(tmpdir(), 'bun-targets-'));
  mkdirSync(join(root, 'src'));
  writeFileSync(join(root, 'src', 'index.ts'), 'export const value = 1;\n');
  writeFileSync(join(root, 'harness.ts'), '// harness\n');
  if (withTests) {
    mkdirSync(join(root, 'tests'));
    writeFileSync(join(root, 'tests', 'index.test.ts'), "import { test } from 'bun:test';\n");
  }
  return root;
}

let roots: string[] = [];

const HARNESS_SOURCE = readFileSync(join(import.meta.dir, '..', 'harness.ts'), 'utf8');

function writeExecutable(path: string, source: string): void {
  writeFileSync(path, source);
  chmodSync(path, 0o755);
}

function isolatedHarness(): string {
  const root = mkdtempSync(join(tmpdir(), 'bun-managed-runner-'));
  roots.push(root);
  writeFileSync(join(root, 'harness.ts'), HARNESS_SOURCE);
  return root;
}

function poisonAmbientRunners(root: string): { bin: string; log: string } {
  const bin = join(root, '.poison-bin');
  const log = join(root, 'poison.log');
  mkdirSync(bin);
  writeFileSync(log, '');
  const poison = `#!/bin/sh
printf '%s\n' "$(basename "$0") $*" >> "$HARNESS_POISON_LOG"
exit 97
`;
  for (const name of ['bun', 'bunx', 'uvx']) writeExecutable(join(bin, name), poison);
  return { bin, log };
}

function commandRecorder(): string {
  return `#!/bin/sh
{
  printf '%s' "$(basename "$0")"
  for arg in "$@"; do
    printf ' <%s>' "$arg"
  done
  printf '\n'
} >> "$HARNESS_COMMAND_LOG"
`;
}

function runIsolatedHarness(
  root: string,
  task: string,
  environment: Record<string, string>,
): { exitCode: number; output: string } {
  const result = Bun.spawnSync([process.execPath, join(root, 'harness.ts'), task], {
    cwd: root,
    env: { ...process.env, ...environment },
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const decoder = new TextDecoder();
  return {
    exitCode: result.exitCode,
    output: decoder.decode(result.stdout) + decoder.decode(result.stderr),
  };
}

afterEach(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
  roots = [];
});

describe('target helpers', () => {
  test('filter existing quality and app targets', async () => {
    const root = tempProject(true);
    roots.push(root);

    expect(await existingTargets(['src', 'missing', 'harness.ts'], root)).toEqual([
      'src',
      'harness.ts',
    ]);
    expect(await qualityTargets({ base: root })).toEqual(['src', 'harness.ts', 'tests']);
    expect(await qualityTargets({ base: root, includeTests: false })).toEqual([
      'src',
      'harness.ts',
    ]);
    expect(await appTargets({ base: root })).toEqual(['src']);
    expect(await appTargets({ base: root, includeTests: true })).toEqual(['src', 'tests']);
  });

  test('detects Bun test file names', async () => {
    const root = tempProject();
    roots.push(root);

    expect(isTestFile('index.test.ts')).toBe(true);
    expect(isTestFile('index.spec.ts')).toBe(true);
    expect(isTestFile('index.ts')).toBe(false);
    expect(await hasTests(root)).toBe(false);

    mkdirSync(join(root, 'tests'));
    writeFileSync(join(root, 'tests', 'helper.ts'), 'export const helper = true;\n');
    expect(await hasTests(root)).toBe(false);

    writeFileSync(join(root, 'tests', 'feature.spec.ts'), "import { test } from 'bun:test';\n");
    expect(await hasTests(root)).toBe(true);
  });

  test('project file predicates include harness and tests', () => {
    expect(isProjectTsFile('src/index.ts')).toBe(true);
    expect(isProjectTsFile('tests/index.test.ts')).toBe(true);
    expect(isProjectTsFile('harness.ts')).toBe(true);
    expect(isProjectTsFile('docs/example.ts')).toBe(false);
    expect(isProjectTsFile('src/data.json')).toBe(false);
    expect(isQualityTsFile('src/index.ts')).toBe(true);
    expect(isQualityTsFile('harness.ts')).toBe(true);
    expect(isQualityTsFile('tests/index.test.ts')).toBe(false);
  });

  test('porcelain path keeps rename target', () => {
    expect(porcelainPath(' M src/index.ts')).toBe('src/index.ts');
    expect(porcelainPath('R  old.ts -> harness.ts')).toBe('harness.ts');
  });
});

describe('managed command vectors', () => {
  test('quality tools resolve without package download runners', () => {
    expect(biomeCommand('check', '--write', '.')).toEqual([
      './node_modules/.bin/biome',
      'check',
      '--write',
      '.',
    ]);
    expect(lintGate(['src', 'tests']).cmd).toEqual([
      './node_modules/.bin/biome',
      'check',
      'src',
      'tests',
    ]);
    expect(typecheckGate().cmd).toEqual(['./node_modules/.bin/tsc', '--noEmit']);
    expect(lizardCsvCommand(['src'])).toEqual(['lizard', 'src', '--csv']);
    expect(complexityGate(['src', 'tests']).cmd).toEqual([
      'lizard',
      'src',
      'tests',
      '-C',
      '15',
      '-a',
      '8',
      '-L',
      '100',
      '-i',
      '0',
    ]);
    expect(deadcodeGate().cmd).toEqual(['knip', '--no-config-hints']);
    expect(setupHooksCommand()).toEqual(['.harness/workspace.sh', 'install-hooks']);

    expect(HARNESS_SOURCE).not.toMatch(/\b(?:bunx|uvx)\b/);
  });

  test('check fixes and typechecks without installing or invoking poisoned runners', () => {
    const root = isolatedHarness();
    mkdirSync(join(root, 'src'));
    writeFileSync(join(root, 'src', 'index.ts'), 'export const value = 1;\n');
    writeFileSync(join(root, 'CLAUDE.md'), '# Agent instructions\n');
    writeFileSync(join(root, 'AGENTS.md'), '# Agent instructions\n');

    for (const directory of ['.claude', '.codex', 'node_modules/.bin']) {
      mkdirSync(join(root, directory), { recursive: true });
    }
    const stopWiring = '{"hooks":{"Stop":[{"hooks":[{"command":"make stop-hook"}]}]}}\n';
    writeFileSync(join(root, '.claude', 'settings.json'), stopWiring);
    writeFileSync(join(root, '.codex', 'hooks.json'), stopWiring);

    const commandLog = join(root, 'commands.log');
    writeFileSync(commandLog, '');
    const recorder = commandRecorder();
    writeExecutable(join(root, 'node_modules', '.bin', 'biome'), recorder);
    writeExecutable(join(root, 'node_modules', '.bin', 'tsc'), recorder);
    const poison = poisonAmbientRunners(root);

    const result = runIsolatedHarness(root, 'check', {
      HARNESS_COMMAND_LOG: commandLog,
      HARNESS_POISON_LOG: poison.log,
      PATH: `${poison.bin}${delimiter}${process.env.PATH ?? ''}`,
    });

    if (result.exitCode !== 0) throw new Error(result.output);
    expect(result.exitCode).toBe(0);
    expect(readFileSync(commandLog, 'utf8').trim().split('\n')).toEqual([
      'biome <check> <--write> <.>',
      'tsc <--noEmit>',
    ]);
    expect(readFileSync(commandLog, 'utf8')).not.toContain('install');
    expect(readFileSync(poison.log, 'utf8')).toBe('');
  });

  test('setup-hooks invokes only the workspace provisioner and inherits offline mode', () => {
    const root = isolatedHarness();
    mkdirSync(join(root, '.harness'));
    const commandLog = join(root, 'commands.log');
    writeFileSync(commandLog, '');
    writeExecutable(
      join(root, '.harness', 'workspace.sh'),
      `#!/bin/sh
printf 'workspace OFFLINE=%s' "\${OFFLINE-}" >> "$HARNESS_COMMAND_LOG"
for arg in "$@"; do
  printf ' <%s>' "$arg" >> "$HARNESS_COMMAND_LOG"
done
printf '\n' >> "$HARNESS_COMMAND_LOG"
`,
    );
    const poison = poisonAmbientRunners(root);

    const result = runIsolatedHarness(root, 'setup-hooks', {
      HARNESS_COMMAND_LOG: commandLog,
      HARNESS_POISON_LOG: poison.log,
      OFFLINE: '1',
      PATH: `${poison.bin}${delimiter}${process.env.PATH ?? ''}`,
    });

    if (result.exitCode !== 0) throw new Error(result.output);
    expect(result.exitCode).toBe(0);
    expect(readFileSync(commandLog, 'utf8')).toBe(
      'workspace OFFLINE=1 <install-hooks>\n',
    );
    expect(readFileSync(poison.log, 'utf8')).toBe('');
    expect(existsSync(join(root, '.git'))).toBe(false);
    expect(existsSync(join(root, '.claude'))).toBe(false);
    expect(existsSync(join(root, '.codex'))).toBe(false);
  });
});

describe('parallel gate runner', () => {
  function captureLog(): { lines: string[]; restore: () => void } {
    const lines: string[] = [];
    const original = console.log;
    console.log = (...args: unknown[]) => {
      lines.push(args.map(String).join(' '));
    };
    return { lines, restore: () => (console.log = original) };
  }

  test('all gates run to completion on a seeded failure', async () => {
    // A seeded failure in the middle must not short-circuit: every gate still
    // reports, results print in submission order, and the overall result is false.
    const gates: Gate[] = [
      { description: 'first ok', cmd: ['true'] },
      { description: 'seeded fail', cmd: ['false'] },
      { description: 'last ok', cmd: ['true'] },
    ];
    const { lines, restore } = captureLog();
    let allOk: boolean;
    try {
      allOk = await runGatesParallel(gates);
    } finally {
      restore();
    }
    const text = lines.join('\n');

    expect(allOk).toBe(false);
    expect(text).toContain('first ok');
    expect(text).toContain('seeded fail');
    expect(text).toContain('last ok');
    expect(text.indexOf('first ok')).toBeLessThan(text.indexOf('last ok'));
  });

  test('empty batch passes', async () => {
    expect(await runGatesParallel([])).toBe(true);
  });
});
