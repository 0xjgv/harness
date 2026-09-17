#!/usr/bin/env bun
/**
 * Pre-flight check runner + development tasks. Zero dependencies — Bun APIs only.
 *
 * Usage:
 *   bun harness.ts                  # full pre-flight (default)
 *   bun harness.ts check            # full pre-flight
 *   bun harness.ts fix              # fix lint errors + format
 *   bun harness.ts pre-commit       # staged fix/format + typecheck
 *   bun harness.ts stop-hook        # agent Stop hook: delta gates, silent when clean
 *   bun harness.ts ci               # CI verification
 *   bun harness.ts acceptance       # cucumber scenarios
 *   bun harness.ts coverage --min=N # tests with coverage threshold
 *   bun harness.ts mutation         # Stryker mutation testing (advisory)
 *   bun harness.ts crap --max=N     # CRAP complexity x coverage (advisory)
 *   bun harness.ts arch             # dependency-cruiser arch checks
 *   bun harness.ts --verbose        # show all output
 */

// ── Configuration ───────────────────────────────────────────────────

const APP_SOURCES = ['src'] as const;
const QUALITY_SOURCES = ['src', 'harness.ts'] as const;
const TEST_DIR = 'tests';
const LIZARD = 'lizard@1.22.2';
const KNIP = 'knip@5.88.1';
const COMPLEXITY_MAX_CCN = 15;
const COMPLEXITY_MAX_ARGS = 8;
const COMPLEXITY_MAX_LENGTH = 100;
const SOURCE_EXTENSIONS = ['.ts', '.tsx', '.js'] as const;
const ROOT = import.meta.dir;
const BASELINE_FILE = '.harness-baseline';
const SUPPRESSION_BASELINE_PREFIX = 'suppressions.';
const ARCH_CONFIGS = ['.dependency-cruiser.json'] as const;
const ARCH_CONFIG_ALLOW_ENV = 'HARNESS_ALLOW_ARCH_CONFIG';
const PROTECTED_BRANCHES = ['main', 'master'] as const;
const PROTECTED_PUSH_ALLOW_ENV = 'HARNESS_ALLOW_PROTECTED_PUSH';
const PRE_PUSH_REFS_ENV = 'HARNESS_PRE_PUSH_REFS';
const PRE_PUSH_STDIN_WAIT_MS = 1000;
const ARCH_BASE_ENV = 'HARNESS_ARCH_BASE';
// Where the stop hook's delta starts: env overrides first (ARCH_BASE_ENV, then
// GITHUB_BASE_REF), then these. Never fetched — a hook must not touch the network.
const DELTA_BASE_CANDIDATES = ['origin/HEAD', 'origin/main', 'origin/master', 'main', 'master'];
const HOOK_STDIN_WAIT_MS = 1000;
// Finding lines a stop-hook payload carries; the rest are one command away.
const HOOK_FINDING_LIMIT = 20;

// ── Hook wiring (installed by `setup-hooks`) ────────────────────────
// Claude reads .claude/settings.json and runs the harness directly; Codex reads
// .codex/hooks.json and goes through the codex-stop-hook.sh wrapper (which turns
// the exit code into the block/continue JSON Codex expects). Keep both in sync
// with the committed template files so re-running the installer is a no-op.
// PostToolUse is Claude-only: it formats the file an Edit/Write just touched.
const CLAUDE_SETTINGS = '.claude/settings.json';
const CLAUDE_SETTINGS_SCHEMA = 'https://json.schemastore.org/claude-code-settings.json';
const CLAUDE_STOP_COMMAND = 'cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook';
const CLAUDE_POST_EDIT_COMMAND = 'cd $CLAUDE_PROJECT_DIR && bun harness.ts post-edit --hook';
const CODEX_STOP_COMMAND =
  'cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh bun harness.ts stop-hook';
export const CLAUDE_STOP_HOOK = { type: 'command', command: CLAUDE_STOP_COMMAND, timeout: 300 };
export const CLAUDE_POST_EDIT_HOOK = {
  type: 'command',
  command: CLAUDE_POST_EDIT_COMMAND,
  timeout: 60,
};
const CODEX_STOP_HOOK = {
  type: 'command',
  command: CODEX_STOP_COMMAND,
  timeout: 300,
  statusMessage: 'Running stop-hook checks',
};

/** One harness hook in an agent settings file; `marker` identifies it on reinstall. */
export interface HookWiring {
  path: string;
  event: string;
  marker: string;
  handler: Record<string, unknown>;
  matcher?: string;
}

export const HOOK_WIRINGS: readonly HookWiring[] = [
  { path: CLAUDE_SETTINGS, event: 'Stop', marker: 'stop-hook', handler: CLAUDE_STOP_HOOK },
  {
    path: CLAUDE_SETTINGS,
    event: 'PostToolUse',
    marker: 'post-edit --hook',
    handler: CLAUDE_POST_EDIT_HOOK,
    matcher: 'Edit|Write',
  },
  { path: '.codex/hooks.json', event: 'Stop', marker: 'stop-hook', handler: CODEX_STOP_HOOK },
];

// ── Output ──────────────────────────────────────────────────────────

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const BLUE = '\x1b[34m';
const DIM = '\x1b[2m';
const RESET = '\x1b[0m';

const VERBOSE = process.argv.includes('--verbose');

function warn(message: string): void {
  console.log(`  ${GREEN}⚠${RESET} ${message}`);
}

async function pathExists(path: string, base = ROOT): Promise<boolean> {
  const { existsSync } = await import('node:fs');
  const { isAbsolute, join } = await import('node:path');
  const full = isAbsolute(path) ? path : join(base, path);
  return existsSync(full);
}

export async function existingTargets(paths: readonly string[], base = ROOT): Promise<string[]> {
  const found: string[] = [];
  for (const path of paths) {
    if (await pathExists(path, base)) found.push(path);
  }
  return found;
}

export async function qualityTargets(
  opts: { includeTests?: boolean; base?: string } = {},
): Promise<string[]> {
  const base = opts.base ?? ROOT;
  const includeTests = opts.includeTests ?? true;
  const targets = await existingTargets(QUALITY_SOURCES, base);
  if (includeTests && (await pathExists(TEST_DIR, base))) targets.push(TEST_DIR);
  return targets;
}

export async function appTargets(
  opts: { includeTests?: boolean; base?: string } = {},
): Promise<string[]> {
  const base = opts.base ?? ROOT;
  const includeTests = opts.includeTests ?? false;
  const targets = await existingTargets(APP_SOURCES, base);
  if (includeTests && (await pathExists(TEST_DIR, base))) targets.push(TEST_DIR);
  return targets;
}

export function isTestFile(path: string): boolean {
  return (
    path.endsWith('.test.ts') ||
    path.endsWith('.spec.ts') ||
    path.includes('_test_') ||
    path.includes('_spec_')
  );
}

export async function hasTests(base = ROOT): Promise<boolean> {
  if (!(await pathExists(TEST_DIR, base))) return false;
  const glob = new Bun.Glob('**/*.ts');
  for await (const path of glob.scan({ cwd: `${base}/${TEST_DIR}`, onlyFiles: true })) {
    if (isTestFile(path)) return true;
  }
  return false;
}

function matchesTsTarget(path: string, targets: readonly string[]): boolean {
  if (!SOURCE_EXTENSIONS.some((extension) => path.endsWith(extension))) return false;
  return targets.some((target) => {
    if (target.endsWith('.ts')) return path === target;
    return path.startsWith(`${target}/`);
  });
}

export function isProjectTsFile(path: string): boolean {
  return matchesTsTarget(path, [...QUALITY_SOURCES, TEST_DIR]);
}

export function isQualityTsFile(path: string): boolean {
  return matchesTsTarget(path, QUALITY_SOURCES);
}

export function porcelainPath(line: string): string {
  const path = line.slice(3);
  if (path.includes(' -> ')) return path.split(' -> ').at(-1) ?? path;
  return path;
}

// ── Runner ──────────────────────────────────────────────────────────

interface RunResult {
  ok: boolean;
  output: string;
}

/** A read-only gate's label + command, shared by the standalone cmd* and the batch. */
export interface Gate {
  description: string;
  cmd: string[];
  extract?: (output: string) => string | undefined;
  hint?: string;
  env?: Record<string, string>;
}

interface GateResult {
  description: string;
  cmd: string[];
  ok: boolean;
  exitCode: number;
  output: string;
  detail?: string;
  hint?: string;
}

/** Run a command with output captured (no printing, no exit): the unit the batch runs. */
async function runCapture(gate: Gate): Promise<GateResult> {
  const proc = Bun.spawn(gate.cmd, { cwd: ROOT, env: gate.env, stdout: 'pipe', stderr: 'pipe' });
  const [stdout, stderr] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
  ]);
  const exitCode = await proc.exited;
  const output = stdout + stderr;
  const ok = exitCode === 0;
  return {
    description: gate.description,
    cmd: gate.cmd,
    ok,
    exitCode,
    output,
    detail: ok ? gate.extract?.(output) : undefined,
    hint: gate.hint,
  };
}

/** Print a gate's ✓/✗ line (with the failure body); exit on failure unless noExit. */
function printGateResult(result: GateResult, opts?: { noExit?: boolean }): boolean {
  if (VERBOSE) console.log(`${DIM}  → ${result.cmd.join(' ')}${RESET}`);
  if (VERBOSE && result.output.trim()) console.log(result.output);

  if (result.ok) {
    const suffix = result.detail ? ` ${DIM}(${result.detail})${RESET}` : '';
    console.log(`  ${GREEN}✓${RESET} ${result.description}${suffix}`);
    return true;
  }
  console.log(`  ${RED}✗${RESET} ${result.description}`);
  if (!VERBOSE && result.output.trim()) console.log(result.output);
  if (result.hint) console.log(`  ↳ fix: ${result.hint}`);
  if (!opts?.noExit) process.exit(result.exitCode);
  return false;
}

async function run(
  description: string,
  cmd: string[],
  opts?: { extract?: (output: string) => string | undefined; noExit?: boolean; stream?: boolean },
): Promise<RunResult> {
  // stream=true inherits stdio for commands whose live output is part of the contract.
  if (opts?.stream) {
    if (VERBOSE) console.log(`${DIM}  → ${cmd.join(' ')}${RESET}`);
    const proc = Bun.spawn(cmd, { cwd: ROOT, stdout: 'inherit', stderr: 'inherit' });
    const exitCode = await proc.exited;
    if (exitCode === 0) {
      console.log(`  ${GREEN}✓${RESET} ${description}`);
      return { ok: true, output: '' };
    }
    console.log(`  ${RED}✗${RESET} ${description}`);
    if (!opts?.noExit) process.exit(exitCode);
    return { ok: false, output: '' };
  }

  const result = await runCapture({ description, cmd, extract: opts?.extract });
  const ok = printGateResult(result, { noExit: opts?.noExit });
  return { ok, output: result.output };
}

/**
 * Run read-only gates concurrently, then print each result in submission order.
 *
 * Returns true when every gate passed. Unlike the fail-fast standalone gates, this
 * runs all gates to completion so one pass surfaces every failure; the caller exits
 * non-zero afterward. Results print in submission order (not as they settle) so a
 * parallel run reads the same every time — matching the monorepo Makefile's
 * buffered, deterministic dump.
 */
export async function runGatesParallel(gates: Gate[]): Promise<boolean> {
  if (gates.length === 0) return true;
  const results = await Promise.all(gates.map((gate) => runCapture(gate)));
  let allOk = true;
  for (const result of results) {
    if (!printGateResult(result, { noExit: true })) allOk = false;
  }
  return allOk;
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

interface SuppressionFinding extends SuppressionMatch {
  location: string;
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

async function scanSuppressionFindings(roots?: string[]): Promise<SuppressionFinding[]> {
  const { readdir, readFile, stat } = await import('node:fs/promises');
  const { isAbsolute, join } = await import('node:path');
  const actualRoots = roots ?? (await qualityTargets());
  const findings: SuppressionFinding[] = [];

  async function scanPath(rawPath: string): Promise<void> {
    const full = isAbsolute(rawPath) ? rawPath : join(ROOT, rawPath);
    const info = await stat(full).catch(() => null);
    if (!info) return;
    if (info.isFile()) {
      if (!full.endsWith('.ts')) return;
      const text = await readFile(full, 'utf8').catch(() => null);
      if (text == null) return;
      for (const [index, line] of text.split('\n').entries()) {
        for (const m of parseLineForSuppressions(line)) {
          findings.push({ ...m, location: `${rawPath}:${index + 1}` });
        }
      }
      return;
    }

    const entries = await readdir(full, { withFileTypes: true }).catch(() => null);
    if (!entries) return;
    for (const e of entries) {
      const child = join(full, e.name);
      if (e.isDirectory()) {
        await scanPath(child);
      } else if (e.isFile() && e.name.endsWith('.ts')) {
        const text = await readFile(child, 'utf8').catch(() => null);
        if (text == null) continue;
        for (const [index, line] of text.split('\n').entries()) {
          for (const m of parseLineForSuppressions(line)) {
            findings.push({ ...m, location: `${child}:${index + 1}` });
          }
        }
      }
    }
  }

  for (const dir of actualRoots) {
    await scanPath(dir);
  }
  return findings;
}

export async function scanSuppressions(roots?: string[]): Promise<Record<string, string[][]>> {
  const results: Record<string, string[][]> = {};
  for (const finding of await scanSuppressionFindings(roots)) {
    const bucket = results[finding.kind] ?? [];
    bucket.push(finding.rules);
    results[finding.kind] = bucket;
  }
  return results;
}

function suppressionCounts(results: Record<string, string[][]>): Record<string, number> {
  return Object.fromEntries(
    Object.entries(results).map(([kind, entries]) => [
      `${SUPPRESSION_BASELINE_PREFIX}${kind}`,
      entries.length,
    ]),
  );
}

export async function readBaseline(base = ROOT): Promise<Record<string, number> | null> {
  const { readFile } = await import('node:fs/promises');
  const text = await readFile(`${base}/${BASELINE_FILE}`, 'utf8').catch(() => null);
  if (text == null) return null;
  const baseline: Record<string, number> = {};
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const [key, rawValue, ...rest] = trimmed.split(/\s+/);
    if (!key || rawValue == null || rest.length > 0) continue;
    const value = Number(rawValue);
    if (Number.isInteger(value)) baseline[key] = value;
  }
  return baseline;
}

export async function coverageMinDefault(base = ROOT): Promise<number> {
  const minArg = process.argv.find((a) => a.startsWith('--min='));
  if (minArg) return Number(minArg.split('=', 2)[1]);
  const baseline = await readBaseline(base);
  return baseline?.['coverage.min'] ?? 0;
}

async function writeBaseline(results: Record<string, string[][]>): Promise<void> {
  const { writeFile } = await import('node:fs/promises');
  const existing = (await readBaseline()) ?? {};
  const counts = suppressionCounts(results);
  const lines = Object.keys(counts)
    .sort()
    .map((key) => `${key} ${counts[key]}`);
  lines.push(`coverage.min ${existing['coverage.min'] ?? 0}`);
  await writeFile(`${ROOT}/${BASELINE_FILE}`, `${lines.join('\n')}\n`);
}

function printSuppressionsBreakdown(results: Record<string, string[][]>): void {
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

async function checkSuppressionsBaseline(opts?: { noExit?: boolean }): Promise<boolean> {
  const findings = await scanSuppressionFindings();
  const results: Record<string, string[][]> = {};
  const locations: Record<string, string[]> = {};
  for (const finding of findings) {
    const bucket = results[finding.kind] ?? [];
    bucket.push(finding.rules);
    results[finding.kind] = bucket;
    const locs = locations[finding.kind] ?? [];
    locs.push(finding.location);
    locations[finding.kind] = locs;
  }

  const current = suppressionCounts(results);
  const baseline = await readBaseline();
  if (baseline == null) {
    printSuppressionsBreakdown(results);
    console.log(`  ${GREEN}⚠${RESET} Suppressions are report-only: no ${BASELINE_FILE} found`);
    console.log('  ↳ fix: run `bun harness.ts suppressions --update-baseline` to start ratcheting');
    return true;
  }

  const total = Object.values(current).reduce((sum, count) => sum + count, 0);
  const baselineTotal = Object.entries(baseline)
    .filter(([key]) => key.startsWith(SUPPRESSION_BASELINE_PREFIX))
    .reduce((sum, [, count]) => sum + count, 0);
  const grown = Object.entries(current).filter(([key, count]) => count > (baseline[key] ?? 0));

  if (grown.length === 0) {
    const suffix =
      total < baselineTotal
        ? ' — run `bun harness.ts suppressions --update-baseline` to ratchet down'
        : '';
    console.log(`  ${GREEN}✓${RESET} Suppressions: ${total} (baseline ${baselineTotal})${suffix}`);
    return true;
  }

  console.log(`  ${RED}✗${RESET} Suppressions grew: ${total} (baseline ${baselineTotal})`);
  for (const [key, count] of grown.sort()) {
    const kind = key.slice(SUPPRESSION_BASELINE_PREFIX.length);
    console.log(`    ${kind}: ${count} > ${baseline[key] ?? 0}`);
    for (const location of (locations[kind] ?? []).slice(0, 10)) {
      console.log(`      ${location}`);
    }
  }
  console.log(
    '  ↳ fix: fix it, or with human sign-off: `bun harness.ts suppressions --update-baseline`',
  );
  if (!opts?.noExit) process.exit(1);
  return false;
}

async function cmdSuppressions(): Promise<void> {
  const results = await scanSuppressions();
  if (process.argv.includes('--update-baseline')) {
    await writeBaseline(results);
    const total = Object.values(results).reduce((sum, arr) => sum + arr.length, 0);
    console.log(`  ${GREEN}✓${RESET} ${BASELINE_FILE}: suppressions baseline set to ${total}`);
    return;
  }
  printSuppressionsBreakdown(results);
  await checkSuppressionsBaseline();
}

// ── Git helpers ─────────────────────────────────────────────────────

async function stagedTsFiles(): Promise<string[]> {
  const proc = Bun.spawn(
    ['git', 'diff', '--cached', '--name-only', '--diff-filter=d', '--relative'],
    {
      cwd: ROOT,
      stdout: 'pipe',
      stderr: 'pipe',
    },
  );
  const stdout = await new Response(proc.stdout).text();
  await proc.exited;
  return stdout
    .trim()
    .split('\n')
    .filter((f) => isProjectTsFile(f));
}

/**
 * Project source files with uncommitted changes, relative to this project.
 *
 * Porcelain paths are repository-relative, so a project in a subdirectory strips
 * its prefix; `--untracked-files=all` lists new files inside new directories.
 */
async function changedTsFiles(): Promise<string[]> {
  const proc = Bun.spawn(['git', 'status', '--porcelain', '--untracked-files=all', '--', '.'], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const stdout = await new Response(proc.stdout).text();
  await proc.exited;
  const prefix = await gitPrefix();
  return stdout
    .split('\n')
    .filter((line) => line.length > 3 && !line.slice(0, 2).includes('D'))
    .map((line) => normalizeChangedPath(porcelainPath(line), prefix))
    .filter((f) => isProjectTsFile(f));
}

// ── Commands ────────────────────────────────────────────────────────

async function cmdFix(files?: string[]): Promise<void> {
  const target = files ?? ['.'];
  await run('Fix & format', ['bunx', 'biome', 'check', '--write', ...target]);
}

function lintGate(files?: string[]): Gate {
  const target = files ?? ['.'];
  return {
    description: 'Lint & format check',
    cmd: ['bunx', 'biome', 'check', ...target],
    hint: 'run `bun harness.ts fix`',
  };
}

async function cmdLint(files?: string[]): Promise<void> {
  const gate = lintGate(files);
  await run(gate.description, gate.cmd);
}

function typecheckGate(): Gate {
  return {
    description: 'Typecheck',
    cmd: ['bunx', 'tsc', '--noEmit'],
    extract: extractTscSummary,
    hint: 'fix the type; ignores are counted by the suppression ratchet',
  };
}

async function cmdTypecheck(): Promise<void> {
  const gate = typecheckGate();
  await run(gate.description, gate.cmd, { extract: gate.extract });
}

async function cmdTest(): Promise<void> {
  if (!(await hasTests())) {
    warn(`Tests: no ${TEST_DIR}/*.test.ts or *.spec.ts files; skipped`);
    return;
  }
  await run('Tests', ['bun', 'test'], { extract: extractTestSummary });
}

/** This environment minus the GIT_* variables git exports to hooks. */
function envWithoutGit(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined && !key.startsWith('GIT_')) env[key] = value;
  }
  return env;
}

/**
 * Run the test suite captured, outside git's hook environment.
 *
 * git exports GIT_DIR (and, for commits, GIT_INDEX_FILE) to hooks; a test that runs
 * `git init` in a temp dir would otherwise write into this repository.
 */
async function checkTests(): Promise<boolean> {
  if (!(await hasTests())) {
    warn(`Tests: no ${TEST_DIR}/*.test.ts or *.spec.ts files; skipped`);
    return true;
  }
  const gate: Gate = {
    description: 'Tests',
    cmd: ['bun', 'test'],
    extract: extractTestSummary,
    env: envWithoutGit(),
  };
  return printGateResult(await runCapture(gate), { noExit: true });
}

function auditGate(): Gate {
  return {
    description: 'Dep audit',
    cmd: ['bun', 'audit'],
    hint: 'bump the vulnerable dependency or escalate',
  };
}

async function cmdAudit(): Promise<void> {
  const gate = auditGate();
  await run(gate.description, gate.cmd);
}

async function cmdCoverage(): Promise<void> {
  if (!(await hasTests())) {
    warn(`Coverage: no ${TEST_DIR}/*.test.ts or *.spec.ts files; skipped`);
    return;
  }

  // Bun's test runner has no built-in per-percentage gate; we emit LCOV and
  // compute the line-coverage percentage ourselves, mirroring python's --min=N.
  const minPct = await coverageMinDefault();

  await run(
    'Coverage (run)',
    ['bun', 'test', '--coverage', '--coverage-reporter=lcov', '--coverage-dir=coverage'],
    { extract: extractTestSummary },
  );

  const { readFile } = await import('node:fs/promises');
  const lcov = await readFile(`${ROOT}/coverage/lcov.info`, 'utf8').catch(() => null);
  if (lcov == null) {
    console.log(`  ${RED}✗${RESET} Coverage: coverage/lcov.info not found`);
    process.exit(1);
  }
  let found = 0;
  let hit = 0;
  for (const line of lcov.split('\n')) {
    if (line.startsWith('LF:')) found += Number(line.slice(3));
    else if (line.startsWith('LH:')) hit += Number(line.slice(3));
  }
  const pct = found === 0 ? 100 : (hit / found) * 100;
  if (pct >= minPct) {
    console.log(`  ${GREEN}✓${RESET} Coverage >= ${minPct}% ${DIM}(${pct.toFixed(1)}%)${RESET}`);
  } else {
    console.log(`  ${RED}✗${RESET} Coverage >= ${minPct}% ${DIM}(got ${pct.toFixed(1)}%)${RESET}`);
    process.exit(1);
  }
}

async function acceptanceGatesOrWarn(): Promise<Gate[]> {
  // Build the cucumber-js gate, or warn + return [] when there are no scenarios.
  const { existsSync } = await import('node:fs');
  const featuresDir = `${ROOT}/${TEST_DIR}/features`;
  let hasFeature = false;
  if (existsSync(featuresDir)) {
    const glob = new Bun.Glob('**/*.feature');
    const matches = await Array.fromAsync(glob.scan({ cwd: featuresDir, onlyFiles: true }));
    hasFeature = matches.length > 0;
  }
  if (!hasFeature) {
    console.log(
      `  ${GREEN}⚠${RESET} Acceptance: no .feature files in ${TEST_DIR}/features/ ` +
        '(add one to enable this gate)',
    );
    return [];
  }
  // cucumber-js runs on Node; invoking its bin through the Bun runtime lets
  // TypeScript step definitions resolve without a separate loader.
  return [
    {
      description: 'Acceptance (cucumber)',
      cmd: ['bun', './node_modules/@cucumber/cucumber/bin/cucumber.js'],
      hint: 'align implementation with the `.feature`, not vice versa',
    },
  ];
}

async function cmdAcceptance(): Promise<void> {
  for (const gate of await acceptanceGatesOrWarn()) await run(gate.description, gate.cmd);
}

async function archGatesOrWarn(): Promise<Gate[]> {
  // Build the dependency-cruiser gate, or warn + return [] when it cannot run.
  const { existsSync } = await import('node:fs');
  if (!existsSync(`${ROOT}/.dependency-cruiser.json`)) {
    console.log(`  ${GREEN}⚠${RESET} Arch: no .dependency-cruiser.json — skipped`);
    return [];
  }
  const targets = (await appTargets()).map((target) => `${target}/**/*.ts`);
  if (targets.length === 0) {
    warn('Arch: no app sources; skipped');
    return [];
  }
  return [
    {
      description: 'Arch (dependency-cruiser)',
      cmd: [
        './node_modules/.bin/depcruise',
        '--config',
        '.dependency-cruiser.json',
        '--no-progress',
        ...targets,
      ],
      hint: "boundary crossed; surface the design decision to the human; don't edit arch config",
    },
  ];
}

async function cmdArch(): Promise<void> {
  for (const gate of await archGatesOrWarn()) await run(gate.description, gate.cmd);
}

async function gitLines(args: string[]): Promise<string[]> {
  const proc = Bun.spawn(['git', ...args], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const [out, code] = await Promise.all([new Response(proc.stdout).text(), proc.exited]);
  if (code !== 0) return [];
  return out
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

async function gitPrefix(): Promise<string> {
  const [prefix] = await gitLines(['rev-parse', '--show-prefix']);
  return (prefix ?? '').replace(/^\.\//, '').replace(/\\/g, '/').replace(/\/$/, '');
}

function normalizeChangedPath(path: string, prefix: string): string {
  const normalized = path.trim().replace(/^\.\//, '').replace(/\\/g, '/');
  if (prefix !== '' && normalized.startsWith(`${prefix}/`)) {
    return normalized.slice(prefix.length + 1);
  }
  return normalized;
}

async function changedPathsFromBase(): Promise<string[]> {
  const bases: string[] = [];
  if (process.env.HARNESS_ARCH_BASE) bases.push(process.env.HARNESS_ARCH_BASE);
  if (process.env.GITHUB_BASE_REF) bases.push(`origin/${process.env.GITHUB_BASE_REF}`);

  const paths: string[] = [];
  for (const base of bases) {
    if ((await gitLines(['rev-parse', '--verify', base])).length === 0) continue;
    paths.push(...(await gitLines(['diff', '--name-only', `${base}...HEAD`, '--', '.'])));
  }
  return paths;
}

// Pre-push refs (`<local ref> <local sha> <remote ref> <remote sha>`) reach the
// harness once per process: from PRE_PUSH_REFS_ENV when a dispatcher forwards them,
// else from git's pre-push stdin. Both guards consume the same resolved result.
type PrePushRefs = { state: 'refs'; text: string } | { state: 'none' } | { state: 'incomplete' };

let prePushRefsCache: PrePushRefs | null = null;

// Agent tools and CI hand the process an open, silent stdin pipe, so a plain read
// would hang. The deadline bounds the whole read, not just the first byte.
function readStdin(waitMs: number): Promise<{ text: string; complete: boolean }> {
  return new Promise((resolve) => {
    const chunks: string[] = [];
    let timer: ReturnType<typeof setTimeout>;
    const finish = (timedOut: boolean): void => {
      clearTimeout(timer);
      process.stdin.pause();
      resolve({ text: chunks.join(''), complete: !timedOut });
    };
    timer = setTimeout(() => finish(true), waitMs);
    // Bun gives redirected stdin a stream without unref; guard the call.
    (process.stdin as { unref?: () => void }).unref?.();
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk: string) => chunks.push(chunk));
    process.stdin.on('end', () => finish(false));
    process.stdin.on('error', () => finish(false));
  });
}

// Partial input is a hard failure: half a ref list would silently under-report
// what is being pushed.
async function readRefsFromStdin(): Promise<PrePushRefs> {
  const { text, complete } = await readStdin(PRE_PUSH_STDIN_WAIT_MS);
  if (!complete && text !== '') return { state: 'incomplete' };
  if (text.trim() === '') return { state: 'none' };
  return { state: 'refs', text };
}

async function prePushRefs(): Promise<PrePushRefs> {
  if (prePushRefsCache !== null) return prePushRefsCache;
  const forwarded = process.env[PRE_PUSH_REFS_ENV];
  if (forwarded !== undefined && forwarded !== '') {
    prePushRefsCache = { state: 'refs', text: forwarded };
  } else if (process.stdin.isTTY) {
    prePushRefsCache = { state: 'none' };
  } else {
    prePushRefsCache = await readRefsFromStdin();
  }
  return prePushRefsCache;
}

function parseRefLines(text: string): string[][] {
  return text
    .split('\n')
    .map((line) => line.trim().split(/\s+/))
    .filter((parts) => parts.length >= 4);
}

async function archDiffBase(): Promise<string | null> {
  for (const ref of ['origin/main', 'origin/master', process.env.HARNESS_ARCH_BASE]) {
    if (!ref) continue;
    if ((await gitLines(['rev-parse', '--verify', ref])).length > 0) return ref;
  }
  return null;
}

// A new branch has no remote sha to diff against. Its tip commit is not the change
// set, so compare the whole branch — merge-base with the integration branch — and
// only fall back to the tip when no integration branch is known.
async function changedPathsForNewBranch(localSha: string): Promise<string[]> {
  const base = await archDiffBase();
  const [mergeBase] = base === null ? [] : await gitLines(['merge-base', base, localSha]);
  if (mergeBase !== undefined) {
    return await gitLines(['diff', '--name-only', `${mergeBase}..${localSha}`, '--', '.']);
  }
  return await gitLines(['diff-tree', '--no-commit-id', '--name-only', '-r', localSha, '--', '.']);
}

async function changedPathsFromRefs(refs: PrePushRefs): Promise<string[]> {
  if (refs.state !== 'refs') return [];
  const zero = '0'.repeat(40);
  const paths: string[] = [];
  for (const [, localSha, , remoteSha] of parseRefLines(refs.text)) {
    if (localSha === zero) continue; // deletion pushes no content to inspect
    if (remoteSha === zero) {
      paths.push(...(await changedPathsForNewBranch(localSha)));
    } else {
      paths.push(...(await gitLines(['diff', '--name-only', remoteSha, localSha, '--', '.'])));
    }
  }
  return paths;
}

async function changedArchConfigs(
  opts: { staged?: boolean; refs?: PrePushRefs } = {},
): Promise<string[]> {
  const paths: string[] = [];
  if (opts.staged) {
    paths.push(...(await gitLines(['diff', '--cached', '--name-only', '--', '.'])));
  } else {
    paths.push(...(await gitLines(['diff', '--name-only', '--', '.'])));
    paths.push(...(await gitLines(['diff', '--cached', '--name-only', '--', '.'])));
    paths.push(...(await gitLines(['ls-files', '--others', '--exclude-standard', '--', '.'])));
    paths.push(...(await changedPathsFromBase()));
  }
  if (opts.refs) paths.push(...(await changedPathsFromRefs(opts.refs)));

  const protectedPaths = new Set<string>(ARCH_CONFIGS);
  const prefix = await gitPrefix();
  return Array.from(
    new Set(paths.map((p) => normalizeChangedPath(p, prefix)).filter((p) => protectedPaths.has(p))),
  ).sort();
}

async function checkArchConfigGuard(
  opts: { warnOnly?: boolean; staged?: boolean; refs?: PrePushRefs } = {},
): Promise<boolean> {
  if (opts.refs?.state === 'incomplete') return reportIncompleteRefs();
  const changed = await changedArchConfigs({ staged: opts.staged, refs: opts.refs });
  if (changed.length === 0) {
    console.log(`  ${GREEN}✓${RESET} Arch config guard`);
    return true;
  }
  const joined = changed.join(', ');
  if (process.env[ARCH_CONFIG_ALLOW_ENV] === '1') {
    console.log(`  ${GREEN}⚠${RESET} Arch config guard override: ${joined}`);
    return true;
  }
  if (opts.warnOnly) {
    console.log(`  ${GREEN}⚠${RESET} Arch config changed: ${joined}`);
    console.log(
      `  ↳ fix: review intentionally, then use ${ARCH_CONFIG_ALLOW_ENV}=1 for commit/push/CI`,
    );
    return true;
  }
  console.log(`  ${RED}✗${RESET} Arch config changed: ${joined}`);
  console.log(`  ↳ fix: review intentionally, then rerun with ${ARCH_CONFIG_ALLOW_ENV}=1`);
  return false;
}

async function cmdArchConfigGuard(): Promise<void> {
  // Forwarded refs let the standalone guard see the same push the hook would;
  // without them it stays a worktree/staged check and never touches stdin.
  const refs = process.env[PRE_PUSH_REFS_ENV] ? await prePushRefs() : undefined;
  const ok = await checkArchConfigGuard({
    warnOnly: process.argv.includes('--warn'),
    staged: process.argv.includes('--staged'),
    refs,
  });
  if (!ok) process.exit(1);
}

function isProtectedBranch(name: string): boolean {
  return (PROTECTED_BRANCHES as readonly string[]).includes(name);
}

/**
 * Name of the protected branch a push targets, or null when it targets none.
 * Pre-push ref lines (`<local ref> <local sha> <remote ref> <remote sha>`) win
 * when present — deletions included, since dropping `main` is as destructive as
 * pushing to it. A well-formed ref list that only touches tags is a real
 * answer (pushing a tag from `main` is legitimate), so it does NOT fall back —
 * only ref text with no parseable record at all (empty, or malformed like
 * `garbage`) falls back to the current branch.
 */
export function protectedPushTarget(refsText: string, currentBranch: string): string | null {
  const refLines = parseRefLines(refsText);
  if (refLines.length === 0) return isProtectedBranch(currentBranch) ? currentBranch : null;
  for (const [, , remoteRef] of refLines) {
    if (!remoteRef.startsWith('refs/heads/')) continue;
    const name = remoteRef.slice('refs/heads/'.length);
    if (isProtectedBranch(name)) return name;
  }
  return null;
}

function reportIncompleteRefs(): boolean {
  console.log(
    `  ${RED}\u2717${RESET} Pre-push refs incomplete after ${PRE_PUSH_STDIN_WAIT_MS / 1000}s`,
  );
  console.log('  \u21b3 fix: rerun the push so git hands over the whole ref list');
  return false;
}

async function checkBranchGuard(refs: PrePushRefs): Promise<boolean> {
  if (refs.state === 'incomplete') return reportIncompleteRefs();
  // Always resolve the real current branch: protectedPushTarget uses it only
  // as a fallback, but a fallback of '' would silently pass a push whose ref
  // text has no parseable ref line at all (e.g. malformed forwarded refs).
  const [branch] = await gitLines(['rev-parse', '--abbrev-ref', 'HEAD']);
  const target = protectedPushTarget(refs.state === 'refs' ? refs.text : '', branch ?? '');
  if (target === null) {
    console.log(`  ${GREEN}\u2713${RESET} Branch guard`);
    return true;
  }
  if (process.env[PROTECTED_PUSH_ALLOW_ENV] === '1') {
    console.log(`  ${GREEN}\u26a0${RESET} Branch guard override: ${target}`);
    return true;
  }
  console.log(`  ${RED}\u2717${RESET} Push targets protected branch: ${target}`);
  console.log(
    `  \u21b3 fix: push a feature branch and open a PR; humans may set ${PROTECTED_PUSH_ALLOW_ENV}=1`,
  );
  return false;
}

async function cmdBranchGuard(): Promise<void> {
  if (!(await checkBranchGuard(await prePushRefs()))) process.exit(1);
}

async function cmdMutation(): Promise<void> {
  // StrykerJS mutation testing. Advisory — not wired into ci.
  // No official Bun runner plugin exists; stryker.conf.json uses the universal
  // 'command' runner which shells out to `bun test` and grades by exit code.
  if (!(await hasTests())) {
    warn(`Mutation: no ${TEST_DIR}/*.test.ts or *.spec.ts files; skipped`);
    return;
  }
  await run('Mutation (Stryker)', ['./node_modules/.bin/stryker', 'run'], { noExit: true });
}

interface CrapFn {
  crap: number;
  ccn: number;
  cov: number;
  loc: string;
}

export function crapScore(ccn: number, cov: number): number {
  return ccn * ccn * (1 - cov) ** 3 + ccn;
}

export function parseLcov(text: string): Record<string, Record<number, number>> {
  const covMap = new Map<string, Map<number, number>>();
  let curFile = '';
  for (const line of text.split('\n')) {
    if (line.startsWith('SF:')) {
      curFile = line.slice(3).trim();
      // Merge into existing entry: LCOV may carry two SF blocks for the same
      // path (sharded runs, hand-merged reports). Overwriting would drop the
      // first block's DA entries.
      if (!covMap.has(curFile)) covMap.set(curFile, new Map());
    } else if (line.startsWith('DA:') && curFile) {
      const [num, hits] = line.slice(3).split(',');
      covMap.get(curFile)?.set(Number(num), Number(hits));
    } else if (line.startsWith('end_of_record')) {
      curFile = '';
    }
  }
  return Object.fromEntries(
    Array.from(covMap, ([file, lines]) => [file, Object.fromEntries(lines)]),
  ) as Record<string, Record<number, number>>;
}

async function artifactIsFresh(path: string, roots: string[]): Promise<boolean> {
  const { stat } = await import('node:fs/promises');
  const { existsSync } = await import('node:fs');
  const artifact = await stat(`${ROOT}/${path}`).catch(() => null);
  if (artifact == null) return false;

  for (const root of roots) {
    const full = `${ROOT}/${root}`;
    if (!existsSync(full)) continue;
    const rootStat = await stat(full).catch(() => null);
    if (rootStat == null) return false;
    if (rootStat.isFile()) {
      if (rootStat.mtimeMs > artifact.mtimeMs) return false;
      continue;
    }

    const glob = new Bun.Glob('**/*.ts');
    for await (const rel of glob.scan({ cwd: full, onlyFiles: true })) {
      const file = await stat(`${full}/${rel}`).catch(() => null);
      if (file == null || file.mtimeMs > artifact.mtimeMs) return false;
    }
  }
  return true;
}

async function cmdCrap(): Promise<void> {
  // CRAP = ccn^2 * (1-cov)^3 + ccn per function. Advisory — lizard + LCOV.
  if (!(await hasTests())) {
    warn('CRAP: no tests; skipped');
    return;
  }

  const maxArg = process.argv.find((a) => a.startsWith('--max='));
  const maxCrap = maxArg ? Number(maxArg.split('=', 2)[1]) : 30;
  const enforce = process.argv.includes('--enforce');

  if (!(await artifactIsFresh('coverage/lcov.info', await qualityTargets()))) {
    await cmdCoverage();
  }

  const { readFile } = await import('node:fs/promises');
  const lcov = await readFile(`${ROOT}/coverage/lcov.info`, 'utf8').catch(() => null);
  if (lcov == null) {
    warn('CRAP: coverage/lcov.info not found after coverage run');
    return;
  }

  // Parse LCOV into { file: { lineNumber: hits } }.
  const covMap = parseLcov(lcov);
  const targets = await appTargets();
  if (targets.length === 0) {
    warn('CRAP: no app sources; skipped');
    return;
  }

  // lizard --csv columns: nloc,ccn,token,param,length,location,file,name,sig,start,end
  const lz = Bun.spawn(['uvx', LIZARD, ...targets, '--csv'], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const [lzOut, lzErr, lzCode] = await Promise.all([
    new Response(lz.stdout).text(),
    new Response(lz.stderr).text(),
    lz.exited,
  ]);
  if (lzCode !== 0) {
    // Lizard could not run (uvx missing, network failure, lizard crash).
    // Reporting "all functions below max" here would be a silent false-pass.
    console.log(
      `  ${RED}✗${RESET} CRAP: lizard failed to run (exit ${lzCode})` +
        `${enforce ? '' : ' (advisory)'}`,
    );
    if (lzErr.trim()) console.log(lzErr.trim());
    if (enforce) process.exit(lzCode);
    return;
  }

  const offenders: CrapFn[] = [];
  for (const line of lzOut.split('\n')) {
    const row = lizardRow(line);
    if (row === null) continue;
    const { name, line: start, ccn } = row.metrics;
    const { end, file: path } = row;
    // Anonymous functions: lizard emits an empty name. They share their
    // parent's coverage attribution in LCOV, so a per-function join cannot
    // score them fairly — skip rather than silently misattribute.
    if (!name) continue;
    const location = `${name}@${start}-${end}@${path}`;

    const lines = covMap[path] ?? covMap[path.replace(/^\.\//, '')] ?? {};
    const inRange: number[] = [];
    for (let n = start; n <= end; n++) {
      if (n in lines) inRange.push(n);
    }
    const cov = inRange.length ? inRange.filter((n) => lines[n] > 0).length / inRange.length : 0;
    const crap = crapScore(ccn, cov);
    if (crap > maxCrap) {
      offenders.push({ crap, ccn, cov, loc: location });
    }
  }

  if (offenders.length === 0) {
    console.log(`  ${GREEN}✓${RESET} CRAP: all functions below ${maxCrap}`);
    return;
  }
  offenders.sort((a, b) => b.crap - a.crap);
  const suffix = enforce ? '' : ' (advisory)';
  console.log(`  ${RED}✗${RESET} CRAP: ${offenders.length} function(s) exceed ${maxCrap}${suffix}`);
  for (const o of offenders.slice(0, 20)) {
    console.log(
      `    CRAP=${o.crap.toFixed(1).padStart(6)}  CCN=${String(o.ccn).padStart(3)}  ` +
        `cov=${(o.cov * 100).toFixed(1).padStart(5)}%  ${o.loc}`,
    );
  }
  if (enforce) process.exit(1);
}

async function complexityGatesOrWarn(): Promise<Gate[]> {
  const targets = await appTargets({ includeTests: true });
  if (targets.length === 0) {
    warn('Complexity: no app sources; skipped');
    return [];
  }
  return [
    {
      description: 'Complexity (lizard)',
      cmd: [
        'uvx',
        LIZARD,
        ...targets,
        '-C',
        String(COMPLEXITY_MAX_CCN),
        '-a',
        String(COMPLEXITY_MAX_ARGS),
        '-L',
        String(COMPLEXITY_MAX_LENGTH),
        '-i',
        '0',
      ],
      hint: `extract helpers or flatten branches until CCN <= ${COMPLEXITY_MAX_CCN}; do not raise the threshold`,
    },
  ];
}

async function cmdComplexity(): Promise<void> {
  for (const gate of await complexityGatesOrWarn()) await run(gate.description, gate.cmd);
}

function deadcodeGate(): Gate {
  // knip finds unused files, exports, and dependencies — coverage biome's
  // per-file noUnusedVariables can't give. Run on-demand via bunx (like lizard
  // via uvx), no devDep. knip.json declares the cucumber step files as entries
  // and ignores the tool devDeps invoked as binaries; --no-config-hints keeps
  // the gate output to genuine findings.
  return {
    description: 'Dead code (knip)',
    cmd: ['bunx', KNIP, '--no-config-hints'],
    hint: 'delete unused code, or allowlist genuine dynamic refs in knip.json',
  };
}

async function cmdDeadcode(): Promise<void> {
  const gate = deadcodeGate();
  await run(gate.description, gate.cmd);
}

// ── Agent hooks ─────────────────────────────────────────────────────
// The stop hook runs after every agent turn and judges the change, not the tree:
// lint left on changed lines, functions pushed over (or further over) a complexity
// limit, dead code on changed lines. Pre-existing debt never blocks a stop; the
// whole-tree gates stay in check / ci / pre-push. Exit contract: silent 0 when
// clean, 2 with a stderr payload the agent reads, 1 when a tool could not run.

/** Inclusive [start, end] line spans. */
type LineRanges = [number, number][];
/** Changed lines per path, relative to this project. */
type ChangedScope = Map<string, LineRanges>;
/** `lizard --csv` functions: file → key (long_name, `#2` for repeats) → metrics. */
type LizardFunctions = Map<string, Map<string, FunctionMetrics>>;

export const WHOLE_FILE: LineRanges = [[1, Number.MAX_SAFE_INTEGER]];
const STOP_HOOK_RERUN = 'bun harness.ts stop-hook --verbose';
export const LOOP_GUARD_NOTICE = 'harness: same findings as the previous stop; not blocking again';
const COMPLEXITY_LIMITS = [
  ['CCN', 'ccn', COMPLEXITY_MAX_CCN],
  ['args', 'args', COMPLEXITY_MAX_ARGS],
  ['length', 'length', COMPLEXITY_MAX_LENGTH],
] as const;
const HUNK_RE = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/;
// biome's `check` fails on these; warnings and infos never block the whole-tree gate either.
const BLOCKING_SEVERITIES = new Set(['error', 'fatal']);
// knip issue types that name a symbol on a line; unused files and dependencies stay in ci.
const KNIP_SYMBOL_ISSUES = [
  ['exports', 'unused export'],
  ['types', 'unused exported type'],
  ['nsExports', 'unused export in namespace'],
  ['nsTypes', 'unused exported type in namespace'],
] as const;
const KNIP_MEMBER_ISSUES = [
  ['enumMembers', 'unused enum member'],
  ['classMembers', 'unused class member'],
] as const;

/** A gate's tool could not run, or printed output the gate cannot read. */
export class ToolError extends Error {}

/** One delta gate: findings block the stop; a problem means its tool failed. */
export interface DeltaResult {
  gate: string;
  findings: string[];
  problem?: string;
}

/** One function as `lizard --csv` measured it. */
export interface FunctionMetrics {
  name: string;
  line: number;
  ccn: number;
  args: number;
  length: number;
}

async function capture(
  cmd: string[],
  cwd: string,
): Promise<{ stdout: string; stderr: string; code: number }> {
  const proc = Bun.spawn(cmd, { cwd, stdout: 'pipe', stderr: 'pipe' });
  const [stdout, stderr, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  return { stdout, stderr, code };
}

/**
 * The command's stdout; ToolError when it cannot start or exits outside `ok`.
 *
 * A non-zero `ok` code means "findings", which always come with a report: the same
 * code with nothing on stdout is a failure (biome exits 1 on a broken config too).
 */
export async function runTool(
  tool: string,
  cmd: string[],
  opts: { ok?: number[]; cwd?: string } = {},
): Promise<string> {
  const result = await capture(cmd, opts.cwd ?? ROOT).catch((error: unknown) => {
    throw new ToolError(`${tool} not runnable: ${errorMessage(error)}`);
  });
  const reported = result.code === 0 || result.stdout.trim() !== '';
  if ((opts.ok ?? [0]).includes(result.code) && reported) return result.stdout;
  const detail = (result.stderr.trim() || result.stdout.trim()).split('\n');
  const reason = `${tool} exited ${result.code}`;
  const last = detail.at(-1)?.trim();
  throw new ToolError(last ? `${reason}: ${last}` : reason);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function gitOutput(args: string[]): Promise<string> {
  return await runTool('git', ['git', '-c', 'core.quotePath=false', ...args]);
}

// ── Changed lines ──

/** The first base ref that resolves: env overrides, then DELTA_BASE_CANDIDATES. */
async function baseRef(): Promise<string | null> {
  const candidates = [process.env[ARCH_BASE_ENV] ?? ''];
  const githubBase = process.env.GITHUB_BASE_REF;
  if (githubBase) candidates.push(`origin/${githubBase}`);
  candidates.push(...DELTA_BASE_CANDIDATES);
  for (const ref of candidates) {
    if (!ref) continue;
    if ((await gitLines(['rev-parse', '--verify', '--quiet', `${ref}^{commit}`])).length > 0) {
      return ref;
    }
  }
  return null;
}

/** merge-base(base ref, HEAD); HEAD without a base ref; null before the first commit. */
async function deltaBase(): Promise<string | null> {
  if ((await gitLines(['rev-parse', '--verify', '--quiet', 'HEAD'])).length === 0) return null;
  const ref = await baseRef();
  const [mergeBase] = ref === null ? [] : await gitLines(['merge-base', ref, 'HEAD']);
  return mergeBase ?? 'HEAD';
}

/** The new-side path of a `+++ b/<path>` header; null for a deleted file. */
function diffPath(header: string): string | null {
  let name = header.slice('+++ '.length).replace(/\t+$/, '');
  if (name === '/dev/null') return null;
  if (name.length > 1 && name.startsWith('"') && name.endsWith('"')) name = name.slice(1, -1);
  return name.startsWith('b/') ? name.slice(2) : name;
}

function addHunk(ranges: ChangedScope, path: string | null, header: string): void {
  const match = HUNK_RE.exec(header);
  if (match === null || path === null) return;
  const count = match[2] === undefined ? 1 : Number(match[2]);
  const start = Number(match[1]);
  if (count > 0) ranges.get(path)?.push([start, start + count - 1]);
}

/**
 * `{path: [[start, end]]}` of the new-side lines in a `git diff -U0` (a/ b/ prefixes).
 *
 * File headers are read only between `diff --git` and the first hunk, so an added line
 * whose text starts with `++ ` is never taken for one. A pure deletion (`+N,0`) adds no
 * range, but its file is still listed.
 */
export function parseDiffRanges(diff: string): ChangedScope {
  const ranges: ChangedScope = new Map();
  let path: string | null = null;
  let inHeader = false;
  for (const line of diff.split('\n')) {
    if (line.startsWith('diff --git ')) {
      path = null;
      inHeader = true;
    } else if (inHeader && line.startsWith('+++ ')) {
      path = diffPath(line);
      if (path !== null) ranges.set(path, []);
    } else if (line.startsWith('@@')) {
      inHeader = false;
      addHunk(ranges, path, line);
    }
  }
  return ranges;
}

/**
 * Changed lines per path relative to this project: `git diff <base>` plus untracked.
 *
 * Covers work committed on the branch and uncommitted work alike. Untracked files, and
 * every file before the first commit, are in scope whole. Renames count as new files.
 */
async function changedScope(base: string | null): Promise<ChangedScope> {
  const listing = ['ls-files', '--others', '--exclude-standard', '--', '.'];
  let scope: ChangedScope = new Map();
  if (base === null) {
    listing.splice(1, 0, '--cached');
  } else {
    const diff = await gitOutput([
      'diff',
      '-U0',
      '--no-color',
      '--no-ext-diff',
      '--no-renames',
      '--relative',
      '--src-prefix=a/',
      '--dst-prefix=b/',
      base,
      '--',
      '.',
    ]);
    scope = parseDiffRanges(diff);
  }
  for (const path of (await gitOutput(listing)).split('\n')) {
    if (path) scope.set(path, [...WHOLE_FILE]);
  }
  return scope;
}

function inRanges(line: number, ranges: LineRanges = []): boolean {
  return ranges.some(([start, end]) => start <= line && line <= end);
}

/** Changed paths `keep` accepts that are still files, sorted. */
async function scopedFiles(
  scope: ChangedScope,
  keep: (path: string) => boolean,
): Promise<string[]> {
  const { statSync } = await import('node:fs');
  const isFile = (path: string): boolean =>
    statSync(`${ROOT}/${path}`, { throwIfNoEntry: false })?.isFile() ?? false;
  return [...scope.keys()].filter((path) => keep(path) && isFile(path)).sort();
}

// ── Lint residue ──

function biomeFinding(item: unknown, scope: ChangedScope): string | null {
  const diagnostic = asObject(item);
  if (!diagnostic) throw new ToolError('unreadable biome output: a diagnostic is not an object');
  if (typeof diagnostic.severity !== 'string' || !BLOCKING_SEVERITIES.has(diagnostic.severity)) {
    return null;
  }
  const location = asObject(diagnostic.location);
  const path = location?.path;
  const line = asObject(location?.start)?.line;
  if (typeof path !== 'string' || typeof line !== 'number') {
    throw new ToolError('unreadable biome output: a diagnostic has no path and line');
  }
  // Line 0 is a whole-file diagnostic (a format diff, a skipped format): no changed line.
  const relative = normalizeChangedPath(path, '');
  if (!inRanges(line, scope.get(relative))) return null;
  const category = typeof diagnostic.category === 'string' ? `${diagnostic.category} ` : '';
  return `${relative}:${line}: ${category}${String(diagnostic.message)}`;
}

/** `path:line: category message` for each blocking biome diagnostic on a changed line. */
export function biomeFindings(report: string, scope: ChangedScope): string[] {
  let diagnostics: unknown;
  try {
    diagnostics = asObject(JSON.parse(report))?.diagnostics;
  } catch (error) {
    throw new ToolError(`unreadable biome output: ${errorMessage(error)}`);
  }
  if (!Array.isArray(diagnostics)) {
    throw new ToolError('unreadable biome output: no diagnostics list');
  }
  return diagnostics.flatMap((item) => biomeFinding(item, scope) ?? []);
}

/**
 * Lint the fix pass could not fix, on changed lines of changed files.
 *
 * The whole-tree gate lints `.`, so every changed file goes to biome, which skips
 * what it ignores or cannot read.
 */
async function lintResidue(scope: ChangedScope): Promise<string[]> {
  const files = await scopedFiles(scope, () => true);
  if (files.length === 0) return [];
  const cmd = [
    'bunx',
    'biome',
    'check',
    '--reporter=json',
    '--max-diagnostics=none',
    '--files-ignore-unknown=true',
    '--no-errors-on-unmatched',
    ...files,
  ];
  return biomeFindings(await runTool('biome', cmd, { ok: [0, 1] }), scope);
}

// ── Complexity delta ──

/** Split one CSV row; quoted fields may hold commas (lizard signatures do). */
export function splitCsvRow(row: string): string[] {
  const cells: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < row.length; i++) {
    const char = row[i];
    if (quoted && char === '"' && row[i + 1] === '"') {
      cell += '"';
      i++;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === ',' && !quoted) {
      cells.push(cell);
      cell = '';
    } else {
      cell += char;
    }
  }
  cells.push(cell);
  return cells;
}

/**
 * One `lizard --csv` row; null for a header or anything else.
 *
 * Columns: nloc, ccn, tokens, params, length, location, file, name, long_name, start, end.
 */
function lizardRow(
  line: string,
): { file: string; longName: string; end: number; metrics: FunctionMetrics } | null {
  const row = splitCsvRow(line);
  const numeric = [1, 3, 4, 9, 10];
  if (row.length < 11 || !numeric.every((i) => /^\d+$/.test(row[i]))) return null;
  const [ccn, args, length, start, end] = numeric.map((i) => Number(row[i]));
  const metrics = { name: row[7], line: start, ccn, args, length };
  return { file: row[6], longName: row[8], end, metrics };
}

/**
 * `{file: {key: metrics}}` from `lizard --csv`, keyed by long_name (the signature).
 *
 * A signature survives a function moving within its file; a start line does not. A
 * repeated signature (two classes' `run ( a )`) is keyed `#2`, `#3` in file order.
 */
export function parseLizardCsv(text: string): LizardFunctions {
  const functions: LizardFunctions = new Map();
  for (const line of text.split('\n')) {
    const row = lizardRow(line);
    if (row === null) continue;
    const inFile = functions.get(row.file) ?? new Map<string, FunctionMetrics>();
    functions.set(row.file, inFile);
    let key = row.longName;
    for (let copy = 2; inFile.has(key); copy++) key = `${row.longName}#${copy}`;
    inFile.set(key, row.metrics);
  }
  return functions;
}

/**
 * The base version of a current function: same signature, else the same unique name.
 *
 * The name fallback keeps a signature-only edit (a new parameter type) on a legacy
 * function from reading as a brand-new function.
 */
function baseTwin(
  key: string,
  now: FunctionMetrics,
  current: Map<string, FunctionMetrics>,
  base: Map<string, FunctionMetrics>,
): FunctionMetrics | undefined {
  const exact = base.get(key);
  if (exact) return exact;
  const named = (functions: Map<string, FunctionMetrics>) =>
    [...functions.values()].filter((fn) => fn.name === now.name);
  const twins = named(base);
  return named(current).length === 1 && twins.length === 1 ? twins[0] : undefined;
}

/** One line per limit `now` exceeds where `was` is absent or measured lower. */
function functionRegressions(path: string, now: FunctionMetrics, was?: FunctionMetrics): string[] {
  const lines: string[] = [];
  for (const [label, field, limit] of COMPLEXITY_LIMITS) {
    const value = now[field];
    const before = was?.[field];
    if (value > limit && (before === undefined || value > before)) {
      const shown = before ?? 'new';
      lines.push(`${path}:${now.line}: ${now.name} ${label} ${shown}→${value} (limit ${limit})`);
    }
  }
  return lines;
}

/** Functions over a limit now that are new, or worse than their base version. */
export function complexityDelta(current: LizardFunctions, base: LizardFunctions): string[] {
  const findings: string[] = [];
  for (const [path, functions] of current) {
    const baseFunctions = base.get(path) ?? new Map<string, FunctionMetrics>();
    for (const [key, now] of functions) {
      const was = baseTwin(key, now, functions, baseFunctions);
      findings.push(...functionRegressions(path, now, was));
    }
  }
  return findings;
}

async function lizardFunctions(files: string[], cwd = ROOT): Promise<LizardFunctions> {
  // lizard with no file arguments walks the working directory; never let it.
  if (files.length === 0) return new Map();
  return parseLizardCsv(await runTool('lizard', ['uvx', LIZARD, '--csv', ...files], { cwd }));
}

/** Write each file's `base` version under `root`; returns those that existed at base. */
async function writeBaseSources(files: string[], base: string, root: string): Promise<string[]> {
  const written: string[] = [];
  for (const path of files) {
    const proc = Bun.spawn(['git', 'show', `${base}:./${path}`], {
      cwd: ROOT,
      stdout: 'pipe',
      stderr: 'ignore',
    });
    const [bytes, code] = await Promise.all([new Response(proc.stdout).arrayBuffer(), proc.exited]);
    if (code !== 0) continue; // absent at base: every function in it is new
    await Bun.write(`${root}/${path}`, bytes);
    written.push(path);
  }
  return written;
}

function isComplexityTarget(path: string): boolean {
  return matchesTsTarget(path, [...APP_SOURCES, TEST_DIR]);
}

/** Complexity this change introduced or worsened, over the complexity gate's targets. */
async function complexityRegressions(scope: ChangedScope, base: string | null): Promise<string[]> {
  const files = await scopedFiles(scope, isComplexityTarget);
  const current = await lizardFunctions(files);
  if (current.size === 0) return [];
  const { mkdtemp, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const tmp = await mkdtemp(join(tmpdir(), 'harness-base-'));
  try {
    const written = base === null ? [] : await writeBaseSources(files, base, tmp);
    return complexityDelta(current, await lizardFunctions(written, tmp));
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
}

// ── Dead-code delta ──

type KnipSymbol = { line: number; name: string };

function knipSymbols(value: unknown): KnipSymbol[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const symbol = asObject(item);
    return typeof symbol?.line === 'number'
      ? [{ line: symbol.line, name: String(symbol.name) }]
      : [];
  });
}

/** `[line, message]` for every symbol-level issue in one knip JSON file row. */
function knipIssueLines(issue: JsonObject): [number, string][] {
  const lines: [number, string][] = [];
  for (const [type, label] of KNIP_SYMBOL_ISSUES) {
    for (const { line, name } of knipSymbols(issue[type])) lines.push([line, `${label} ${name}`]);
  }
  for (const [type, label] of KNIP_MEMBER_ISSUES) {
    for (const [parent, members] of Object.entries(asObject(issue[type]) ?? {})) {
      for (const { line, name } of knipSymbols(members)) {
        lines.push([line, `${label} ${parent}.${name}`]);
      }
    }
  }
  for (const group of Array.isArray(issue.duplicates) ? issue.duplicates : []) {
    const names = (Array.isArray(group) ? group : []).map((item) => asObject(item)?.name);
    const [first] = knipSymbols(group);
    if (first) lines.push([first.line, `duplicate export ${names.join(', ')}`]);
  }
  return lines;
}

/** `path:line: message` for each knip symbol finding on a changed line. */
export function knipFindings(report: string, scope: ChangedScope): string[] {
  let issues: unknown;
  try {
    issues = asObject(JSON.parse(report))?.issues;
  } catch (error) {
    throw new ToolError(`unreadable knip output: ${errorMessage(error)}`);
  }
  if (!Array.isArray(issues)) throw new ToolError('unreadable knip output: no issues list');
  const findings: string[] = [];
  for (const item of issues) {
    const issue = asObject(item);
    if (typeof issue?.file !== 'string') {
      throw new ToolError('unreadable knip output: an issue has no file');
    }
    const ranges = scope.get(issue.file);
    for (const [line, message] of knipIssueLines(issue)) {
      if (inRanges(line, ranges)) findings.push(`${issue.file}:${line}: ${message}`);
    }
  }
  return findings;
}

/** Dead code on changed lines. knip still reads the whole project: deadness is global. */
async function deadcodeResidue(scope: ChangedScope): Promise<string[]> {
  if ((await scopedFiles(scope, isQualityTsFile)).length === 0) return [];
  const cmd = [...deadcodeGate().cmd, '--reporter', 'json'];
  return knipFindings(await runTool('knip', cmd, { ok: [0, 1] }), scope);
}

// ── Stop-hook verdict ──

async function deltaResult(gate: string, measure: () => Promise<string[]>): Promise<DeltaResult> {
  try {
    return { gate, findings: await measure() };
  } catch (error) {
    return { gate, findings: [], problem: errorMessage(error) };
  }
}

/** Lint residue, complexity delta, and dead-code delta; read-only, in parallel. */
async function runDeltaGates(scope: ChangedScope, base: string | null): Promise<DeltaResult[]> {
  return await Promise.all([
    deltaResult('Lint', () => lintResidue(scope)),
    deltaResult('Complexity', () => complexityRegressions(scope, base)),
    deltaResult('Dead code', () => deadcodeResidue(scope)),
  ]);
}

/**
 * At most HOOK_FINDING_LIMIT findings, then one line counting the rest.
 *
 * `--verbose` lifts the cap, which is what the counting line tells the reader to run.
 */
export function capFindings(findings: string[], verbose = VERBOSE): string[] {
  if (verbose || findings.length <= HOOK_FINDING_LIMIT) return [...findings];
  const rest = findings.length - HOOK_FINDING_LIMIT;
  return [...findings.slice(0, HOOK_FINDING_LIMIT), `… +${rest} more — run \`${STOP_HOOK_RERUN}\``];
}

/** The stderr block an agent reads: failed gates, then their findings; '' when clean. */
export function stopHookPayload(results: DeltaResult[]): string {
  const failed = results.filter((result) => result.findings.length > 0);
  if (failed.length === 0) return '';
  const header = `stop-hook failed: ${failed.map((result) => result.gate).join(', ')}`;
  return [header, ...capFindings(failed.flatMap((result) => result.findings))].join('\n');
}

export function payloadDigest(payload: string): string {
  return new Bun.CryptoHasher('sha256').update(payload).digest('hex');
}

/**
 * 2 blocks on findings; 1 for a tool failure or a repeated block; 0 when clean.
 *
 * A repeat is the stored digest of the same payload while the agent is already
 * continuing because of a stop hook (`stop_hook_active`): blocking again would loop.
 * A payload that changed blocks again.
 */
export function stopHookExit(
  payload: string,
  failedTools: number,
  event: JsonObject,
  stored: string,
): number {
  if (payload) {
    const repeat = event.stop_hook_active === true && stored === payloadDigest(payload);
    return repeat ? 1 : 2;
  }
  return failedTools ? 1 : 0;
}

/** A file-name-safe key for this project within its repository. */
export function loopGuardKey(prefix: string): string {
  return prefix.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'root';
}

async function loopGuardPath(): Promise<string | null> {
  const [gitPath] = await gitLines(['rev-parse', '--git-path', 'harness']);
  if (gitPath === undefined) return null;
  const { resolve } = await import('node:path');
  return resolve(ROOT, gitPath, `stop-hook-${loopGuardKey(await gitPrefix())}`);
}

async function readDigest(path: string | null): Promise<string> {
  if (path === null) return '';
  try {
    return (await Bun.file(path).text()).trim();
  } catch {
    return '';
  }
}

/** Remember a block's digest; forget it once the stop is clean. */
async function updateLoopGuard(path: string | null, code: number, payload: string): Promise<void> {
  if (path === null) return;
  const { rm } = await import('node:fs/promises');
  if (code === 2) await Bun.write(path, payloadDigest(payload));
  else if (code === 0) await rm(path, { force: true });
}

/** Print the verdict to stderr (nothing when clean) and return the exit code. */
async function reportStopHook(results: DeltaResult[], event: JsonObject): Promise<number> {
  const payload = stopHookPayload(results);
  const problems = results
    .filter((result) => result.problem)
    .map((result) => `stop-hook: ${result.gate} could not run: ${result.problem}`);
  const guard = await loopGuardPath();
  const code = stopHookExit(payload, problems.length, event, await readDigest(guard));
  const lines = [...problems];
  if (payload) lines.push(payload);
  if (payload && code === 1) lines.push(LOOP_GUARD_NOTICE);
  if (lines.length > 0) await Bun.write(Bun.stderr, `${lines.join('\n')}\n`);
  await updateLoopGuard(guard, code, payload);
  return code;
}

/** The agent's hook JSON; `{}` for empty or invalid input. */
export function parseHookEvent(text: string): JsonObject {
  try {
    return asObject(JSON.parse(text)) ?? {};
  } catch {
    return {};
  }
}

/** The hook event on stdin; `{}` for a terminal. */
async function hookEvent(): Promise<JsonObject> {
  if (process.stdin.isTTY) return {};
  return parseHookEvent((await readStdin(HOOK_STDIN_WAIT_MS)).text);
}

/** Fix, then format, `files` in place, silently; what is left surfaces as lint residue. */
async function fixAndFormat(files: string[]): Promise<void> {
  if (files.length === 0) return;
  const cmd = ['bunx', 'biome', 'check', '--write', '--no-errors-on-unmatched', ...files];
  await capture(cmd, ROOT).catch(() => null);
}

/** True when CLAUDE.md exists and AGENTS.md is missing or differs from it. */
async function agentsMdStale(): Promise<boolean> {
  const { existsSync, readFileSync } = await import('node:fs');
  const claude = `${ROOT}/CLAUDE.md`;
  const agents = `${ROOT}/AGENTS.md`;
  if (!existsSync(claude)) return false;
  return !existsSync(agents) || !readFileSync(agents).equals(readFileSync(claude));
}

async function mirrorClaudeMd(): Promise<void> {
  const { readFileSync, writeFileSync } = await import('node:fs');
  writeFileSync(`${ROOT}/AGENTS.md`, readFileSync(`${ROOT}/CLAUDE.md`));
}

/**
 * Stop hook: an uncommitted CLAUDE.md edit carries into AGENTS.md, silently.
 *
 * CLAUDE.md is canonical. An edit to AGENTS.md alone is left for pre-commit to report.
 */
async function syncAgentsMdAfterEdit(): Promise<void> {
  const edited = await gitLines(['status', '--porcelain', '--', 'CLAUDE.md']);
  if (edited.length > 0 && (await agentsMdStale())) await mirrorClaudeMd();
}

/**
 * Post-edit, then changed-lines lint, complexity delta, dead-code delta.
 *
 * Silent on success. Findings exit 2 with a capped stderr payload; a tool that could
 * not run exits 1; the same findings on a stop the agent is already continuing from
 * exit 1 (loop guard). `check` and `ci` keep the whole-tree gates.
 */
async function cmdStopHook(): Promise<void> {
  const event = await hookEvent(); // stdin belongs to the hook event; read it first
  await fixAndFormat(await changedTsFiles());
  await syncAgentsMdAfterEdit();
  const base = await deltaBase();
  let scope: ChangedScope;
  try {
    scope = await changedScope(base);
  } catch (error) {
    await Bun.write(Bun.stderr, `stop-hook: changed lines could not run: ${errorMessage(error)}\n`);
    process.exitCode = 1;
    return;
  }
  const code = await reportStopHook(await runDeltaGates(scope, base), event);
  if (VERBOSE && code === 0) {
    console.log(`stop-hook: clean (${scope.size} changed path(s) vs ${base ?? 'no commits'})`);
  }
  process.exitCode = code;
}

async function realpathOrNull(path: string): Promise<string | null> {
  const { realpath } = await import('node:fs/promises');
  return await realpath(path).catch(() => null);
}

/** The project source file a PostToolUse event names, relative to `root`; else null. */
export async function hookTarget(event: JsonObject, root: string): Promise<string | null> {
  const filePath = asObject(event.tool_input)?.file_path;
  if (typeof filePath !== 'string' || filePath === '') return null;
  const { statSync } = await import('node:fs');
  const { isAbsolute, relative, resolve, sep } = await import('node:path');
  const [real, realRoot] = await Promise.all([
    realpathOrNull(resolve(root, filePath)),
    realpathOrNull(root),
  ]);
  if (real === null || realRoot === null) return null;
  const rel = relative(realRoot, real);
  // outside this project: another harness owns it
  if (rel === '..' || rel.startsWith(`..${sep}`) || isAbsolute(rel)) return null;
  const path = rel.split(sep).join('/');
  return isProjectTsFile(path) && statSync(real).isFile() ? path : null;
}

/**
 * Fix and format the one file a PostToolUse event names. Never blocks.
 *
 * Prints one additionalContext line when the file changed, so the agent re-reads it
 * before its next edit; otherwise nothing.
 */
async function postEditHook(): Promise<void> {
  const target = await hookTarget(await hookEvent(), ROOT);
  if (target === null) return;
  const { readFileSync } = await import('node:fs');
  const path = `${ROOT}/${target}`;
  let changed: boolean;
  try {
    const before = readFileSync(path);
    await fixAndFormat([target]);
    changed = !readFileSync(path).equals(before);
  } catch {
    return;
  }
  if (!changed) return;
  const context = {
    hookEventName: 'PostToolUse',
    additionalContext: `harness: reformatted ${target}; re-read it before editing it again`,
  };
  console.log(JSON.stringify({ hookSpecificOutput: context }));
}

/** Format source files with uncommitted changes; `--hook`: the file a hook event names. */
async function cmdPostEdit(): Promise<void> {
  if (process.argv.includes('--hook')) {
    await postEditHook();
    return;
  }
  const files = await changedTsFiles();
  if (files.length === 0) return;
  await run('Fix & format', ['bunx', 'biome', 'check', '--write', ...files], { noExit: true });
}

// ── Stages ──────────────────────────────────────────────────────────

/** True when the settings file has a handler for this hook under its event. */
export async function hookWired(wiring: HookWiring, root = ROOT): Promise<boolean> {
  let data: JsonObject;
  try {
    data = await readJsonObject(`${root}/${wiring.path}`, wiring.path);
  } catch {
    return false;
  }
  const groups = asObject(data.hooks)?.[wiring.event];
  if (!Array.isArray(groups)) return false;
  return groups.some((group) => {
    const handlers = asObject(group)?.hooks;
    return Array.isArray(handlers) && handlers.some((h) => isHarnessHandler(h, wiring.marker));
  });
}

/** Warn when the Claude/Codex Stop or Claude PostToolUse wiring is missing. */
export async function checkStopHooksPresent(root = ROOT): Promise<void> {
  for (const wiring of HOOK_WIRINGS) {
    const label = `${wiring.event} hook wiring`;
    if (await hookWired(wiring, root)) {
      console.log(`  ${GREEN}✓${RESET} ${label} (${wiring.path})`);
    } else {
      console.log(`  ${RED}⚠${RESET} Missing ${label}: ${wiring.path}`);
    }
  }
}

type JsonObject = Record<string, unknown>;

async function readJsonObject(path: string, label: string): Promise<JsonObject> {
  const { existsSync, readFileSync } = await import('node:fs');
  if (!existsSync(path)) return {};
  const text = readFileSync(path, 'utf8').trim();
  if (!text) return {};
  const obj = asObject(JSON.parse(text));
  if (!obj) throw new Error(`${label} must contain a JSON object`);
  return obj;
}

function asObject(value: unknown): JsonObject | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function jsonObjectChild(data: JsonObject, key: string, label: string): JsonObject {
  if (data[key] === undefined) data[key] = {};
  const child = asObject(data[key]);
  if (!child) throw new Error(`${label}:${key} must contain a JSON object`);
  return child;
}

function jsonListChild(data: JsonObject, key: string, label: string): unknown[] {
  if (data[key] === undefined) data[key] = [];
  if (!Array.isArray(data[key])) throw new Error(`${label}:${key} must contain a JSON array`);
  return data[key] as unknown[];
}

/** True for a command handler that already runs this harness hook (any form). */
function isHarnessHandler(handler: unknown, marker: string): boolean {
  const obj = asObject(handler);
  return obj !== null && obj.type === 'command' && typeof obj.command === 'string'
    ? obj.command.includes(marker)
    : false;
}

async function gitHookPath(name: string): Promise<string> {
  // Resolve via git so worktrees / core.hooksPath land in the right place. Strip
  // GIT_* env so an ambient GIT_DIR from a parent process can't redirect us.
  const proc = Bun.spawn(['git', 'rev-parse', '--git-path', `hooks/${name}`], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
    env: envWithoutGit(),
  });
  const out = (await new Response(proc.stdout).text()).trim();
  const code = await proc.exited;
  const { isAbsolute, join } = await import('node:path');
  if (code === 0 && out) return isAbsolute(out) ? out : join(ROOT, out);
  return join(ROOT, '.git', 'hooks', name);
}

async function installGitHook(name: string): Promise<void> {
  const { mkdirSync, writeFileSync, chmodSync } = await import('node:fs');
  const { dirname } = await import('node:path');
  const path = await gitHookPath(name);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `#!/bin/sh\nbun harness.ts ${name}\n`);
  chmodSync(path, 0o755);
}

/**
 * Inject/refresh one hook in its settings file, preserving every other hook.
 *
 * Idempotent: an existing handler carrying the wiring's marker (current or legacy) is
 * replaced in place and duplicates dropped, so re-running never accumulates entries.
 */
export async function installHook(wiring: HookWiring, root = ROOT): Promise<void> {
  const { writeFileSync, mkdirSync } = await import('node:fs');
  const { dirname } = await import('node:path');
  const path = `${root}/${wiring.path}`;
  const data = await readJsonObject(path, wiring.path);
  if (wiring.path === CLAUDE_SETTINGS && !('$schema' in data)) {
    data.$schema = CLAUDE_SETTINGS_SCHEMA;
  }

  const hooks = jsonObjectChild(data, 'hooks', wiring.path);
  const eventGroups = jsonListChild(hooks, wiring.event, wiring.path);
  let installed = false;
  for (const group of eventGroups) {
    const groupObj = asObject(group);
    if (!groupObj || !Array.isArray(groupObj.hooks)) continue;
    const next: unknown[] = [];
    for (const handler of groupObj.hooks) {
      if (isHarnessHandler(handler, wiring.marker)) {
        if (!installed) {
          next.push({ ...wiring.handler });
          installed = true;
        }
        continue;
      }
      next.push(handler);
    }
    groupObj.hooks = next;
  }
  if (!installed) {
    const matcher = wiring.matcher === undefined ? {} : { matcher: wiring.matcher };
    eventGroups.push({ ...matcher, hooks: [{ ...wiring.handler }] });
  }

  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`);
}

function firstDiffLine(a: string, b: string): number {
  const al = a.split('\n');
  const bl = b.split('\n');
  const len = Math.min(al.length, bl.length);
  for (let i = 0; i < len; i++) {
    if (al[i] !== bl[i]) return i + 1;
  }
  return len + 1;
}

async function checkAgentsMdDrift(noExit = false): Promise<RunResult> {
  const { existsSync, readFileSync } = await import('node:fs');
  const claudePath = `${ROOT}/CLAUDE.md`;
  const agentsPath = `${ROOT}/AGENTS.md`;
  const fail = (msg: string): RunResult => {
    console.log(`  ${RED}✗${RESET} agents-md-drift: ${msg}`);
    if (!noExit) process.exit(1);
    return { ok: false, output: msg };
  };
  if (!existsSync(claudePath)) return fail('CLAUDE.md not found');
  if (!existsSync(agentsPath)) {
    return fail('AGENTS.md missing — run `harness sync-agents-md`');
  }
  const a = readFileSync(claudePath);
  const b = readFileSync(agentsPath);
  if (a.equals(b)) {
    console.log(`  ${GREEN}✓${RESET} agents-md-drift`);
    return { ok: true, output: '' };
  }
  const line = firstDiffLine(a.toString('utf8'), b.toString('utf8'));
  return fail(
    `AGENTS.md differs from CLAUDE.md (first diff at line ${line}) — ` +
      'run `harness sync-agents-md`',
  );
}

async function cmdSyncAgentsMd(): Promise<void> {
  const { existsSync } = await import('node:fs');
  if (!existsSync(`${ROOT}/CLAUDE.md`)) {
    console.log(`  ${RED}✗${RESET} sync-agents-md: CLAUDE.md not found`);
    process.exit(1);
  }
  await mirrorClaudeMd();
  console.log(`  ${GREEN}✓${RESET} sync-agents-md: AGENTS.md ← CLAUDE.md`);
}

/**
 * pre-commit: a staged CLAUDE.md carries AGENTS.md into the same commit.
 *
 * The `git add` inherits git's hook environment on purpose: GIT_INDEX_FILE is the
 * index this commit is being built from.
 */
async function syncAgentsMdStaged(): Promise<void> {
  const staged = await gitLines(['diff', '--cached', '--name-only', '--', 'CLAUDE.md']);
  if (staged.length === 0 || !(await agentsMdStale())) return;
  await mirrorClaudeMd();
  const added = await capture(['git', 'add', '--', 'AGENTS.md'], ROOT);
  if (added.code !== 0) {
    console.log(`  ${RED}✗${RESET} sync-agents-md: git add AGENTS.md failed`);
    process.stdout.write(added.stderr);
    process.exit(1);
  }
  console.log(`  ${GREEN}✓${RESET} sync-agents-md: AGENTS.md ← CLAUDE.md (staged)`);
}

async function cmdAgentsMdDrift(): Promise<void> {
  await checkAgentsMdDrift();
}

function agentsMdDriftGate(): Gate {
  return {
    description: 'Agents drift',
    cmd: ['bun', 'harness.ts', 'agents-md-drift'],
    hint: 'run `bun harness.ts sync-agents-md`',
  };
}

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
  if (await hasTests()) {
    results.push(
      await run('Tests', ['bun', 'test'], { extract: extractTestSummary, noExit: true }),
    );
  } else {
    warn(`Tests: no ${TEST_DIR}/*.test.ts or *.spec.ts files; skipped`);
    results.push({ ok: true, output: '' });
  }

  await checkStopHooksPresent();
  await checkArchConfigGuard({ warnOnly: true });
  results.push(await checkAgentsMdDrift(true));
  results.push({ ok: await checkSuppressionsBaseline({ noExit: true }), output: '' });

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
  // Fix/format staged files, typecheck, and mirror a staged CLAUDE.md; tests run at pre-push.
  console.log(`\n${BLUE}[pre-commit]${RESET}\n`);
  await checkArchConfigGuard({ warnOnly: true, staged: true });
  await syncAgentsMdStaged();

  const files = await stagedTsFiles();
  const stagedDocs = await gitLines([
    'diff',
    '--cached',
    '--name-only',
    '--',
    'AGENTS.md',
    'CLAUDE.md',
  ]);
  if (files.length > 0 || stagedDocs.length > 0) await checkAgentsMdDrift();
  if (files.length === 0) {
    console.log('No staged TypeScript files — skipping checks');
    return;
  }

  await cmdFix(files);
  await cmdTypecheck();
}

async function cmdCi(): Promise<void> {
  console.log(`\n${BLUE}[ci]${RESET}\n`);
  // Read-only gates run as a parallel batch (captured, printed in submission order,
  // run to completion). Coverage is captured and CRAP is advisory — both after the batch.
  const gates: Gate[] = [
    lintGate(),
    typecheckGate(),
    auditGate(),
    agentsMdDriftGate(),
    ...(await complexityGatesOrWarn()),
    deadcodeGate(),
    ...(await acceptanceGatesOrWarn()),
    ...(await archGatesOrWarn()),
  ];
  const allOk = await runGatesParallel(gates);
  await cmdCoverage(); // self-skips; after the batch
  await cmdCrap(); // advisory unless --enforce
  const archConfigOk = await checkArchConfigGuard();
  const suppressionsOk = await checkSuppressionsBaseline({ noExit: true });
  if (!allOk || !archConfigOk || !suppressionsOk) process.exit(1);
}

async function cmdPrePush(): Promise<void> {
  // Read-only push gate: the offline checks pre-commit and stop-hook do not run.
  // pre-commit covers fix/format/typecheck on staged files; stop-hook covers the
  // change's delta. This fills the gap with the deterministic, offline gates none of
  // them run — tests, lint (biome covers format), agents-md drift, acceptance, arch —
  // validating the whole pushed tree (after merges/rebases/--no-verify) before it
  // leaves the machine. Tests run first and alone: they spawn processes and write temp
  // repos. Network (audit) and advisory (coverage/CRAP) gates stay in ci.
  console.log(`\n${BLUE}[pre-push]${RESET}\n`);
  const refs = await prePushRefs();
  if (!(await checkBranchGuard(refs))) process.exit(1);
  const archConfigOk = await checkArchConfigGuard({ refs });
  const testsOk = await checkTests();
  const gates: Gate[] = [
    lintGate(),
    agentsMdDriftGate(),
    ...(await acceptanceGatesOrWarn()),
    ...(await archGatesOrWarn()),
  ];
  if (!(await runGatesParallel(gates)) || !archConfigOk || !testsOk) process.exit(1);
}

async function cmdHooks(): Promise<void> {
  await installGitHook('pre-commit');
  await installGitHook('pre-push');
  for (const wiring of HOOK_WIRINGS) await installHook(wiring);
  console.log('Installed pre-commit, pre-push, Claude/Codex Stop, and Claude PostToolUse hooks');
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

const TASKS: Record<string, [(() => Promise<void>) | ((f?: string[]) => Promise<void>), string]> = {
  fix: [cmdFix, 'Fix lint errors + format code'],
  lint: [cmdLint, 'Lint + format check (read-only)'],
  typecheck: [cmdTypecheck, 'Type-check with tsc'],
  test: [cmdTest, 'Run tests'],
  audit: [cmdAudit, 'Audit dependencies for known vulnerabilities'],
  acceptance: [cmdAcceptance, 'Run acceptance scenarios (cucumber)'],
  coverage: [cmdCoverage, 'Tests with coverage threshold (--min=N)'],
  mutation: [cmdMutation, 'Mutation testing (Stryker, advisory)'],
  crap: [cmdCrap, 'CRAP complexity x coverage gate (advisory)'],
  suppressions: [cmdSuppressions, 'Show or update suppression baseline'],
  complexity: [cmdComplexity, 'Cyclomatic complexity gate (lizard, CCN 15, args 8)'],
  deadcode: [cmdDeadcode, 'Detect unused files/exports/deps (knip, via bunx)'],
  arch: [cmdArch, 'Architecture checks (dependency-cruiser)'],
  'arch-config-guard': [cmdArchConfigGuard, 'Block unreviewed arch config changes'],
  'branch-guard': [cmdBranchGuard, 'Refuse pushes to main/master'],
  check: [cmdCheck, 'Full pre-flight: lockfile + fix + typecheck + tests'],
  'pre-commit': [cmdPreCommit, 'Staged fix/format + typecheck; mirrors a staged CLAUDE.md'],
  'pre-push': [cmdPrePush, 'Read-only push gate: branch guard, tests, lint, acceptance, arch'],
  ci: [
    cmdCi,
    'Lint + typecheck + audit + complexity + deadcode + acceptance + coverage + crap + arch',
  ],
  'setup-hooks': [
    cmdHooks,
    'Install git pre-commit + pre-push hooks and Claude/Codex agent hook wiring',
  ],
  'post-edit': [cmdPostEdit, 'Format changed files (--hook: the file a PostToolUse names)'],
  'stop-hook': [
    cmdStopHook,
    'post-edit, then changed-lines lint, complexity delta, deadcode delta; ' +
      'silent on success, exit 2 with findings',
  ],
  'agents-md-drift': [cmdAgentsMdDrift, 'Fail if AGENTS.md differs from CLAUDE.md'],
  'sync-agents-md': [cmdSyncAgentsMd, 'Overwrite AGENTS.md from CLAUDE.md'],
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
