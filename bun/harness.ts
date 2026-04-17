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

const VERBOSE = process.argv.includes('--verbose');

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

// ── Suppressions ────────────────────────────────────────────────────

export interface SuppressionMatch {
  kind: string;
  rules: string[];
}

const TS_DIRECTIVE_PATTERNS: { kind: string; pattern: RegExp }[] = [
  { kind: 'ts-ignore', pattern: /\/\/\s*@ts-ignore\b/ },
  { kind: 'ts-expect-error', pattern: /\/\/\s*@ts-expect-error\b/ },
  { kind: 'ts-nocheck', pattern: /\/\/\s*@ts-nocheck\b/ },
];
const ESLINT_PATTERN =
  /(?:\/\/|\/\*)\s*eslint-disable(?:-line|-next-line)?(?::\s*([^*\n]+?))?(?:\s*\*\/|\s*$)/;
const BIOME_PATTERN = /\/\/\s*biome-ignore\s+([a-zA-Z0-9_/-]+)/;

export function parseLineForSuppressions(line: string): SuppressionMatch[] {
  const out: SuppressionMatch[] = [];
  for (const d of TS_DIRECTIVE_PATTERNS) {
    if (d.pattern.test(line)) out.push({ kind: d.kind, rules: [] });
  }
  const em = ESLINT_PATTERN.exec(line);
  if (em) {
    const rules = em[1]
      ? em[1]
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)
      : [];
    out.push({ kind: 'eslint-disable', rules });
  }
  const bm = BIOME_PATTERN.exec(line);
  if (bm) {
    out.push({ kind: 'biome-ignore', rules: [bm[1]] });
  }
  return out;
}

export async function scanSuppressions(roots?: string[]): Promise<Record<string, string[][]>> {
  const { readdir, readFile } = await import('node:fs/promises');
  const { join } = await import('node:path');
  const actualRoots = roots ?? [SRC_DIR, TEST_DIR].map((d) => join(ROOT, d));
  const results: Record<string, string[][]> = {};

  async function walk(dir: string): Promise<void> {
    const entries = await readdir(dir, { withFileTypes: true }).catch(() => null);
    if (!entries) return;
    for (const e of entries) {
      const full = join(dir, e.name);
      if (e.isDirectory()) {
        await walk(full);
      } else if (e.isFile() && e.name.endsWith('.ts')) {
        const text = await readFile(full, 'utf8').catch(() => null);
        if (text == null) continue;
        for (const line of text.split('\n')) {
          for (const m of parseLineForSuppressions(line)) {
            const bucket = results[m.kind] ?? [];
            bucket.push(m.rules);
            results[m.kind] = bucket;
          }
        }
      }
    }
  }

  for (const dir of actualRoots) {
    await walk(dir);
  }
  return results;
}

async function printSuppressionsReport(): Promise<void> {
  const results = await scanSuppressions();
  const total = Object.values(results).reduce((sum, arr) => sum + arr.length, 0);
  console.log('\n=== Suppressions ===\n');
  console.log(`Suppressions: ${total} total`);
  if (total === 0) return;
  for (const kind of Object.keys(results).sort()) {
    const entries = results[kind];
    console.log(`  ${kind}: ${entries.length}`);
    const ruleCounts: Record<string, number> = {};
    for (const rules of entries) {
      for (const r of rules) {
        ruleCounts[r] = (ruleCounts[r] ?? 0) + 1;
      }
    }
    const sorted = Object.entries(ruleCounts).sort(
      (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
    );
    for (const [rule, count] of sorted.slice(0, 10)) {
      console.log(`    ${rule}: ${count}`);
    }
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

async function changedTsFiles(): Promise<string[]> {
  const proc = Bun.spawn(['git', 'status', '--porcelain'], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const stdout = await new Response(proc.stdout).text();
  await proc.exited;
  return stdout
    .trim()
    .split('\n')
    .map((line) => line.slice(3))
    .filter(
      (f) => f.endsWith('.ts') && (f.startsWith(`${SRC_DIR}/`) || f.startsWith(`${TEST_DIR}/`)),
    );
}

// ── Commands ────────────────────────────────────────────────────────

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

async function cmdPostEdit(): Promise<void> {
  if ((await changedTsFiles()).length === 0) return;
  await run('Fix & format', ['bunx', 'biome', 'check', '--write', '.'], { noExit: true });
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

  await printSuppressionsReport();

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
  fix: [cmdFix, 'Fix lint errors + format code'],
  lint: [cmdLint, 'Lint + format check (read-only)'],
  typecheck: [cmdTypecheck, 'Type-check with tsc'],
  test: [cmdTest, 'Run tests'],
  audit: [cmdAudit, 'Audit dependencies for known vulnerabilities'],
  check: [cmdCheck, 'Full pre-flight: lockfile + fix + typecheck + tests'],
  'pre-commit': [cmdPreCommit, 'Staged checks + tests'],
  ci: [cmdCi, 'Lint + typecheck + tests with coverage (CI verification)'],
  'setup-hooks': [cmdHooks, 'Install git pre-commit hook'],
  'post-edit': [cmdPostEdit, 'Format if source files changed (Claude Code hook)'],
  clean: [cmdClean, 'Remove caches and build artifacts'],
};

if (import.meta.main) {
  const args = process.argv.slice(2).filter((a) => !a.startsWith('-'));
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
}
