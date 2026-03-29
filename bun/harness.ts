#!/usr/bin/env bun
/**
 * Pre-flight check runner + development tasks. Zero dependencies — Bun APIs only.
 *
 * Usage:
 *   bun harness.ts                  # full pre-flight (default)
 *   bun harness.ts check            # full pre-flight
 *   bun harness.ts fix              # fix lint errors + format
 *   bun harness.ts pre-commit       # staged checks + tests
 *   bun harness.ts ci               # CI verification
 *   bun harness.ts --verbose        # show all output
 *   bun harness.ts help             # show all commands
 */

// ── Configuration ───────────────────────────────────────────────────

const SRC_DIR = 'src';
const TEST_DIR = 'tests';
const ROOT = import.meta.dir;

// ── Output ──────────────────────────────────────────────────────────

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const BLUE = '\x1b[34m';
const DIM = '\x1b[2m';
const RESET = '\x1b[0m';

const VERBOSE = process.argv.includes('--verbose') || process.env.VERBOSE === '1';

// ── Runner ──────────────────────────────────────────────────────────

interface RunResult {
  ok: boolean;
  output: string;
}

async function run(
  description: string,
  cmd: string[],
  opts?: { extract?: (output: string) => string | undefined; noExit?: boolean },
): Promise<RunResult> {
  if (VERBOSE) console.log(`${DIM}  → ${cmd.join(' ')}${RESET}`);

  const proc = Bun.spawn(cmd, { cwd: ROOT, stdout: 'pipe', stderr: 'pipe' });
  const [stdout, stderr] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
  ]);
  const exitCode = await proc.exited;
  const output = stdout + stderr;

  if (VERBOSE && output.trim()) console.log(output);

  if (exitCode === 0) {
    const detail = opts?.extract?.(output);
    const suffix = detail ? ` ${DIM}(${detail})${RESET}` : '';
    console.log(`  ${GREEN}✓${RESET} ${description}${suffix}`);
    return { ok: true, output };
  }

  console.log(`  ${RED}✗${RESET} ${description}`);
  if (!VERBOSE && output.trim()) console.log(output);
  if (!opts?.noExit) process.exit(exitCode);
  return { ok: false, output };
}

// ── Extractors ──────────────────────────────────────────────────────

function extractTscSummary(output: string): string | undefined {
  if (!output.trim()) return 'no errors';
  const errors = output.match(/Found (\d+) errors?/)?.[1];
  if (errors) return `${errors} errors`;
}

function extractTestSummary(output: string): string | undefined {
  const pass = output.match(/(\d+) pass/)?.[1];
  const fail = output.match(/(\d+) fail/)?.[1];
  if (pass) {
    const parts = [`${pass} passed`];
    if (fail && fail !== '0') parts.push(`${fail} failed`);
    return parts.join(', ');
  }
}

// ── Git helpers ─────────────────────────────────────────────────────

async function stagedTsFiles(): Promise<string[]> {
  const proc = Bun.spawn(['git', 'diff', '--cached', '--name-only', '--diff-filter=d'], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const stdout = await new Response(proc.stdout).text();
  await proc.exited;
  return stdout
    .trim()
    .split('\n')
    .filter(
      (f) => f.endsWith('.ts') && (f.startsWith(`${SRC_DIR}/`) || f.startsWith(`${TEST_DIR}/`)),
    );
}

// ── Commands ────────────────────────────────────────────────────────

async function cmdInstall(): Promise<void> {
  console.log(`\n${BLUE}[install]${RESET}\n`);
  await run('Install dependencies', ['bun', 'install']);
}

async function cmdFix(files?: string[]): Promise<void> {
  const target = files ?? ['.'];
  await run('Fix & format', ['bunx', 'biome', 'check', '--write', ...target]);
}

async function cmdLint(files?: string[]): Promise<void> {
  const target = files ?? ['.'];
  await run('Lint & format check', ['bunx', 'biome', 'check', ...target]);
}

async function cmdTypecheck(): Promise<void> {
  await run('Typecheck', ['bunx', 'tsc', '--noEmit'], { extract: extractTscSummary });
}

async function cmdTest(): Promise<void> {
  await run('Tests', ['bun', 'test'], { extract: extractTestSummary });
}

async function cmdAudit(): Promise<void> {
  await run('Dep audit', ['bun', 'audit']);
}

// ── Stages ──────────────────────────────────────────────────────────

async function cmdCheck(): Promise<void> {
  const start = performance.now();
  console.log(`\n${BLUE}[check]${RESET} Running pre-flight checks...\n`);

  const results: RunResult[] = [];
  results.push(
    await run('Lockfile sync', ['bun', 'install', '--frozen-lockfile'], { noExit: true }),
  );
  results.push(
    await run('Fix & format', ['bunx', 'biome', 'check', '--write', '.'], { noExit: true }),
  );
  results.push(
    await run('Typecheck', ['bunx', 'tsc', '--noEmit'], {
      extract: extractTscSummary,
      noExit: true,
    }),
  );
  results.push(await run('Tests', ['bun', 'test'], { extract: extractTestSummary, noExit: true }));

  const elapsed = ((performance.now() - start) / 1000).toFixed(1);
  const passed = results.filter((r) => r.ok).length;
  const failed = results.filter((r) => !r.ok).length;

  console.log();
  if (failed > 0) {
    console.log(
      `${RED}FAIL${RESET} ${passed} passed, ${failed} failed ${DIM}(${elapsed}s)${RESET}`,
    );
    process.exit(1);
  } else {
    console.log(`${GREEN}OK${RESET} ${passed} passed ${DIM}(${elapsed}s)${RESET}`);
  }
}

async function cmdPreCommit(): Promise<void> {
  const files = await stagedTsFiles();
  if (files.length === 0) {
    console.log('No staged TypeScript files — skipping checks');
    return;
  }

  console.log(`\n${BLUE}[pre-commit]${RESET}\n`);
  await cmdFix(files);
  await cmdTypecheck();

  if (files.some((f) => f.startsWith(`${SRC_DIR}/`))) {
    await cmdTest();
  }
}

async function cmdCi(): Promise<void> {
  console.log(`\n${BLUE}[ci]${RESET}\n`);
  await cmdLint();
  await cmdTypecheck();
  await cmdAudit();
  await run('Tests', ['bun', 'test', '--coverage'], { extract: extractTestSummary });
}

async function cmdHooks(): Promise<void> {
  const hookPath = `${ROOT}/.git/hooks/pre-commit`;
  const hookDir = `${ROOT}/.git/hooks`;

  const { mkdirSync, writeFileSync, chmodSync } = await import('node:fs');
  mkdirSync(hookDir, { recursive: true });
  writeFileSync(hookPath, '#!/bin/sh\nbun harness.ts pre-commit\n');
  chmodSync(hookPath, 0o755);
  console.log('Installed pre-commit hook');
}

async function cmdClean(): Promise<void> {
  console.log(`\n${BLUE}[clean]${RESET}\n`);
  const { rmSync, existsSync } = await import('node:fs');
  for (const name of ['node_modules/.cache', 'coverage']) {
    if (existsSync(`${ROOT}/${name}`)) {
      rmSync(`${ROOT}/${name}`, { recursive: true });
      console.log(`  ${GREEN}✓${RESET} Removed ${name}`);
    }
  }
  const glob = new Bun.Glob('**/*.tsbuildinfo');
  for await (const path of glob.scan({ cwd: ROOT })) {
    rmSync(`${ROOT}/${path}`);
    console.log(`  ${GREEN}✓${RESET} Removed ${path}`);
  }
}

// ── CLI dispatch ────────────────────────────────────────────────────

const TASKS: Record<string, [() => Promise<void>, string]> = {
  install: [cmdInstall, 'Install dependencies'],
  fix: [cmdFix, 'Fix lint errors + format code'],
  lint: [cmdLint, 'Lint + format check (read-only)'],
  typecheck: [cmdTypecheck, 'Type-check with tsc'],
  test: [cmdTest, 'Run tests'],
  audit: [cmdAudit, 'Audit dependencies for known vulnerabilities'],
  check: [cmdCheck, 'Full pre-flight: lockfile + fix + typecheck + tests'],
  'pre-commit': [cmdPreCommit, 'Staged checks + tests'],
  ci: [cmdCi, 'Lint + typecheck + tests with coverage (CI verification)'],
  'setup-hooks': [cmdHooks, 'Install git pre-commit hook'],
  clean: [cmdClean, 'Remove caches and build artifacts'],
};

const args = process.argv.slice(2).filter((a) => !a.startsWith('-'));

if (args[0] === 'help') {
  console.log('Usage: bun harness.ts <command> [--verbose]\n');
  console.log('Commands:');
  for (const [name, [, desc]] of Object.entries(TASKS)) {
    console.log(`  ${name.padEnd(16)} ${desc}`);
  }
  console.log(`  ${'help'.padEnd(16)} Show this help`);
  process.exit(0);
}

const taskName = args[0];

if (taskName && !(taskName in TASKS)) {
  console.error(`Unknown command: ${taskName}`);
  process.exit(1);
}

if (taskName) {
  await TASKS[taskName][0]();
} else {
  await cmdCheck();
}
