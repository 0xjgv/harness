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
const ROOT = import.meta.dir;
const BASELINE_FILE = '.harness-baseline';
const SUPPRESSION_BASELINE_PREFIX = 'suppressions.';
const ARCH_CONFIGS = ['.dependency-cruiser.json'] as const;
const ARCH_CONFIG_ALLOW_ENV = 'HARNESS_ALLOW_ARCH_CONFIG';
const PROTECTED_BRANCHES = ['main', 'master'] as const;
const PROTECTED_PUSH_ALLOW_ENV = 'HARNESS_ALLOW_PROTECTED_PUSH';
const PRE_PUSH_REFS_ENV = 'HARNESS_PRE_PUSH_REFS';
const PRE_PUSH_STDIN_WAIT_MS = 1000;
// Where the stop hook's delta starts: env overrides first (HARNESS_ARCH_BASE, then
// GITHUB_BASE_REF), then these. Never fetched — a hook must not touch the network.
const DELTA_BASE_CANDIDATES = ['origin/HEAD', 'origin/main', 'origin/master', 'main', 'master'];
const HOOK_STDIN_WAIT_MS = 1000;
// Finding lines a stop-hook payload carries; the rest are one command away.
const HOOK_FINDING_LIMIT = 20;

// ── Hook wiring (installed by `setup-hooks`) ────────────────────────
// Claude reads .claude/settings.json and runs the harness directly; Codex reads
// .codex/hooks.json and goes through the codex-stop-hook.sh wrapper (which turns
// the exit code into the block/continue JSON Codex expects). Keep both in sync
// with the committed template files so re-running the installer is a no-op. The
// committed .claude/settings.json also carries the PostToolUse (post-edit --hook) wiring.
const CLAUDE_SETTINGS_SCHEMA = 'https://json.schemastore.org/claude-code-settings.json';
const CLAUDE_STOP_COMMAND = 'cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook';
const CODEX_STOP_COMMAND =
  'cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh bun harness.ts stop-hook';
const CLAUDE_STOP_HOOK = { type: 'command', command: CLAUDE_STOP_COMMAND, timeout: 300 };
const CODEX_STOP_HOOK = {
  type: 'command',
  command: CODEX_STOP_COMMAND,
  timeout: 300,
  statusMessage: 'Running stop-hook checks',
};

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
  if (!path.endsWith('.ts')) return false;
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

// Porcelain paths are repository-relative: a project in a subdirectory strips its
// prefix. `--untracked-files=all` lists new files inside new directories.
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

// Tests run without git's hook variables (GIT_DIR, GIT_INDEX_FILE): a test that runs
// `git init` in a temp dir would otherwise write into this repository.
async function checkTests(): Promise<boolean> {
  if (!(await hasTests())) {
    warn(`Tests: no ${TEST_DIR}/*.test.ts or *.spec.ts files; skipped`);
    return true;
  }
  const gate = { description: 'Tests', cmd: ['bun', 'test'], env: envWithoutGit() };
  return printGateResult(await runCapture({ ...gate, extract: extractTestSummary }), {
    noExit: true,
  });
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

// One `lizard --csv` function row, or null. Columns 1, 3, 4 are CCN, params, length.
// Signatures can contain commas, so name/start/end/path come from the self-contained
// quoted `name@start-end@path` location field. Name is empty for anonymous functions.
function lizardRow(row: string) {
  const cols = row.split(',');
  const location = /"([^"@]*)@(\d+)-(\d+)@([^"]+)"/.exec(row);
  const [ccn, , args, length] = cols.slice(1, 5).map(Number);
  if (cols.length < 11 || location === null || !Number.isFinite(ccn)) return null;
  const [, name, start, end, path] = location;
  return { name, path, start: Number(start), end: Number(end), ccn, args, length };
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
  for (const row of lzOut.split('\n')) {
    const fn = lizardRow(row);
    // Anonymous functions: lizard emits an empty name. They share their
    // parent's coverage attribution in LCOV, so a per-function join cannot
    // score them fairly — skip rather than silently misattribute.
    if (!fn?.name) continue;
    const { name, start, end, path, ccn } = fn;
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
// The stop hook judges the change, not the tree; the whole-tree gates stay in check /
// ci / pre-push. Silent 0 when clean, 2 with a stderr payload, 1 when a tool failed.

/** Changed lines per path relative to this project, as inclusive [start, end] spans. */
type ChangedScope = Map<string, [number, number][]>;

/** One delta gate: findings block the stop; a problem means its tool could not run. */
interface DeltaResult {
  gate: string;
  findings: string[];
  problem?: string;
}

interface BiomeReport {
  diagnostics: {
    severity: string;
    category?: string;
    message: string;
    location: { path?: string; start?: { line: number } };
  }[];
}

interface KnipIssue {
  description: string;
  location: { path: string; positions?: { begin: { line: number } } };
}

export const LOOP_GUARD_NOTICE = 'harness: already blocked once on this stop; not blocking again';
const HUNK_RE = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/;
// The new side of a file header; `--dst-prefix=b/` is forced, so `/dev/null` never matches.
const NEW_PATH_RE = /^\+\+\+ "?b\/(.*?)"?\t*$/;
// knip issue types that name a symbol on a line; unused files and dependencies stay in ci.
const KNIP_SYMBOL_ISSUES = 'exports,types,nsExports,nsTypes,enumMembers,classMembers,duplicates';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The command's stdout; throws when it cannot run, or fails outside `ok` or silently. */
async function runTool(tool: string, cmd: string[], ok = [0]): Promise<string> {
  const proc = Bun.spawn(cmd, { cwd: ROOT, stdout: 'pipe', stderr: 'pipe' });
  const [stdout, stderr, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  // A findings exit always carries a report: biome exits 1 on a broken config too.
  if (code === 0 || (ok.includes(code) && stdout.trim() !== '')) return stdout;
  const last = (stderr.trim() || stdout.trim()).split('\n').at(-1);
  throw new Error(`${tool} exited ${code}${last ? `: ${last}` : ''}`);
}

// ── Changed lines ──

/** merge-base(first base ref that resolves, HEAD); HEAD without one; null before a commit. */
async function deltaBase(): Promise<string | null> {
  if ((await gitLines(['rev-parse', '--verify', '--quiet', 'HEAD'])).length === 0) return null;
  const { HARNESS_ARCH_BASE: archBase, GITHUB_BASE_REF: githubBase } = process.env;
  for (const ref of [archBase, githubBase && `origin/${githubBase}`, ...DELTA_BASE_CANDIDATES]) {
    if (!ref) continue;
    const [resolved] = await gitLines(['rev-parse', '--verify', '--quiet', `${ref}^{commit}`]);
    if (resolved === undefined) continue;
    const [mergeBase] = await gitLines(['merge-base', ref, 'HEAD']);
    return mergeBase ?? 'HEAD';
  }
  return 'HEAD';
}

/**
 * `{path: [[start, end]]}` of the new-side lines in a `git diff -U0` (a/ b/ prefixes).
 * Headers are read only before a file's first hunk (an added `++ x` line is no header).
 * A pure deletion (`+N,0`) adds no range but lists its file; a deleted file is not listed.
 */
export function parseDiffRanges(diff: string): ChangedScope {
  const ranges: ChangedScope = new Map();
  let spans: [number, number][] = [];
  let inHeader = false;
  for (const line of diff.split('\n')) {
    const path = inHeader ? NEW_PATH_RE.exec(line)?.[1] : undefined;
    const hunk = HUNK_RE.exec(line);
    if (line.startsWith('diff --git ')) {
      spans = []; // detached until a `+++ b/` header claims it
      inHeader = true;
    } else if (path !== undefined) {
      ranges.set(path, spans);
    } else if (hunk !== null) {
      inHeader = false;
      const start = Number(hunk[1]);
      const count = Number(hunk[2] ?? 1);
      if (count > 0) spans.push([start, start + count - 1]);
    }
  }
  return ranges;
}

/**
 * Changed lines per path: `git diff <base>` (branch commits and uncommitted work; a
 * rename is a new file) plus untracked files, whole; before the first commit, every file.
 */
async function changedScope(base: string | null): Promise<ChangedScope> {
  const git = (...args: string[]) => runTool('git', ['git', '-c', 'core.quotePath=false', ...args]);
  const diff = ['diff', '-U0', '--no-color', '--no-ext-diff', '--no-renames', '--relative'];
  const scope: ChangedScope =
    base === null
      ? new Map()
      : parseDiffRanges(await git(...diff, '--src-prefix=a/', '--dst-prefix=b/', base, '--', '.'));
  const listed = base === null ? ['--cached', '--others'] : ['--others'];
  const whole = await git('ls-files', ...listed, '--exclude-standard', '--', '.');
  for (const path of whole.split('\n')) {
    if (path) scope.set(path, [[1, Number.MAX_SAFE_INTEGER]]);
  }
  return scope;
}

/** True when lines [start, end] overlap a changed span. */
function touches(spans: [number, number][] | undefined, start: number, end = start): boolean {
  return (spans ?? []).some(([from, to]) => from <= end && start <= to);
}

/** Changed paths `keep` accepts that are still files, sorted. */
async function scopedFiles(scope: ChangedScope, keep: (p: string) => boolean): Promise<string[]> {
  const { statSync } = await import('node:fs');
  const isFile = (path: string) => statSync(`${ROOT}/${path}`, { throwIfNoEntry: false })?.isFile();
  return [...scope.keys()].filter((path) => keep(path) && isFile(path)).sort();
}

/** `path:line: message` for each `[path, line, message]` on a changed line. */
function onChangedLines(scope: ChangedScope, items: [string, number, string][]): string[] {
  return items
    .filter(([path, line]) => touches(scope.get(path), line))
    .map(([path, line, message]) => `${path}:${line}: ${message}`);
}

// ── Delta gates ──

/**
 * Biome errors the fix pass left, on changed lines. Like the whole-tree gate (which
 * lints `.`), every changed file goes to biome. Warnings never fail that gate either;
 * line 0 is a whole-file diagnostic (a format diff), which no changed line matches.
 */
async function lintResidue(scope: ChangedScope): Promise<string[]> {
  const files = await scopedFiles(scope, () => true);
  if (files.length === 0) return [];
  const flags = ['--reporter=json', '--max-diagnostics=none', '--files-ignore-unknown=true'];
  const cmd = ['bunx', 'biome', 'check', ...flags, '--no-errors-on-unmatched', ...files];
  const report = JSON.parse(await runTool('biome', cmd, [0, 1])) as BiomeReport;
  const blocking = report.diagnostics.filter((d) => ['error', 'fatal'].includes(d.severity));
  return onChangedLines(
    scope,
    blocking.map((d) => [
      normalizeChangedPath(d.location.path ?? '', ''),
      d.location.start?.line ?? 0,
      d.category ? `${d.category} ${d.message}` : d.message,
    ]),
  );
}

/**
 * `path:start: name CCN 19 (limit 15)` per limit exceeded by a function the change touches.
 * lizard's TypeScript reader ends a function on the line of the next token, so the span
 * ends at the last line up to there that starts with `}` (`sources`: lines per path).
 */
export function touchedOverLimit(
  csv: string,
  scope: ChangedScope,
  sources: Map<string, string[]>,
): string[] {
  const findings: string[] = [];
  for (const row of csv.split('\n')) {
    const fn = lizardRow(row);
    if (fn === null) continue;
    const lines = sources.get(fn.path) ?? [];
    let end = fn.end;
    while (end > fn.start && !lines[end - 1]?.trimStart().startsWith('}')) end--;
    if (!touches(scope.get(fn.path), fn.start, end)) continue;
    const metrics = [
      ['CCN', fn.ccn, COMPLEXITY_MAX_CCN],
      ['args', fn.args, COMPLEXITY_MAX_ARGS],
      ['length', fn.length, COMPLEXITY_MAX_LENGTH],
    ] as const;
    for (const [label, value, limit] of metrics) {
      if (value <= limit) continue;
      findings.push(`${fn.path}:${fn.start}: ${fn.name} ${label} ${value} (limit ${limit})`);
    }
  }
  return findings;
}

/** Over-limit functions the change touches (legacy ones too: leave what you touch better). */
async function complexityResidue(scope: ChangedScope): Promise<string[]> {
  const targets = [...APP_SOURCES, TEST_DIR];
  const files = await scopedFiles(scope, (path) => matchesTsTarget(path, targets));
  // lizard with no file arguments walks the working directory; never let it.
  if (files.length === 0) return [];
  const { readFileSync } = await import('node:fs');
  const sources = new Map(files.map((f) => [f, readFileSync(`${ROOT}/${f}`, 'utf8').split('\n')]));
  const csv = await runTool('lizard', ['uvx', LIZARD, '--csv', ...files]);
  return touchedOverLimit(csv, scope, sources);
}

/** knip symbol findings on changed lines. knip still reads the whole project: deadness is global. */
async function deadcodeResidue(scope: ChangedScope): Promise<string[]> {
  if ((await scopedFiles(scope, isQualityTsFile)).length === 0) return [];
  const cmd = [...deadcodeGate().cmd, '--reporter', 'codeclimate', '--include', KNIP_SYMBOL_ISSUES];
  const issues = JSON.parse(await runTool('knip', cmd, [0, 1])) as KnipIssue[];
  return onChangedLines(
    scope,
    issues.map((i) => [i.location.path, i.location.positions?.begin.line ?? 0, i.description]),
  );
}

async function deltaResult(gate: string, measure: () => Promise<string[]>): Promise<DeltaResult> {
  try {
    return { gate, findings: await measure() };
  } catch (error) {
    return { gate, findings: [], problem: errorMessage(error) };
  }
}

/** Lint, complexity, and dead code on the change; read-only, in parallel. */
async function runDeltaGates(): Promise<DeltaResult[]> {
  let scope: ChangedScope;
  try {
    scope = await changedScope(await deltaBase());
  } catch (error) {
    return [{ gate: 'Changed lines', findings: [], problem: errorMessage(error) }];
  }
  return await Promise.all([
    deltaResult('Lint', () => lintResidue(scope)),
    deltaResult('Complexity', () => complexityResidue(scope)),
    deltaResult('Dead code', () => deadcodeResidue(scope)),
  ]);
}

// ── Stop-hook verdict ──

/** At most HOOK_FINDING_LIMIT findings, then one line counting the rest; `--verbose` lifts it. */
export function capFindings(findings: string[], verbose = VERBOSE): string[] {
  if (verbose || findings.length <= HOOK_FINDING_LIMIT) return [...findings];
  const rest = findings.length - HOOK_FINDING_LIMIT;
  return [
    ...findings.slice(0, HOOK_FINDING_LIMIT),
    `… +${rest} more — run \`bun harness.ts stop-hook --verbose\``,
  ];
}

/** The stderr block an agent reads: failed gates, then their findings; '' when clean. */
export function stopHookPayload(results: DeltaResult[]): string {
  const failed = results.filter((result) => result.findings.length > 0);
  if (failed.length === 0) return '';
  const header = `stop-hook failed: ${failed.map((result) => result.gate).join(', ')}`;
  return [header, ...capFindings(failed.flatMap((result) => result.findings))].join('\n');
}

/** 2 on findings, but 1 when this stop already follows a block (no loop); 1 on a tool failure. */
export function stopHookExit(payload: string, failedTools: number, event: JsonObject): number {
  if (payload) return event.stop_hook_active === true ? 1 : 2;
  return failedTools > 0 ? 1 : 0;
}

/** The agent's hook JSON on stdin; `{}` for a terminal, or for empty or invalid input. */
async function hookEvent(): Promise<JsonObject> {
  if (process.stdin.isTTY) return {};
  try {
    return asObject(JSON.parse((await readStdin(HOOK_STDIN_WAIT_MS)).text)) ?? {};
  } catch {
    return {};
  }
}

/** Fix, then format, `files` in place, silently; what is left surfaces as lint residue. */
async function fixAndFormat(files: string[]): Promise<void> {
  if (files.length === 0) return;
  const cmd = ['bunx', 'biome', 'check', '--write', '--no-errors-on-unmatched', ...files];
  await runCapture({ description: 'Fix & format', cmd });
}

/** Copy CLAUDE.md (canonical) over a missing or different AGENTS.md; true when it wrote. */
async function syncAgentsMd(): Promise<boolean> {
  const { existsSync, readFileSync, writeFileSync } = await import('node:fs');
  const claude = `${ROOT}/CLAUDE.md`;
  const agents = `${ROOT}/AGENTS.md`;
  if (!existsSync(claude)) return false;
  const text = readFileSync(claude);
  if (existsSync(agents) && readFileSync(agents).equals(text)) return false;
  writeFileSync(agents, text);
  return true;
}

/** Post-edit, then lint, complexity, and dead code on the change; see stopHookExit. */
async function cmdStopHook(): Promise<void> {
  const event = await hookEvent(); // stdin belongs to the hook event; read it first
  await fixAndFormat(await changedTsFiles());
  // An uncommitted CLAUDE.md edit carries into AGENTS.md; an AGENTS.md-only edit is
  // left for pre-commit to report.
  if ((await gitLines(['status', '--porcelain', '--', 'CLAUDE.md'])).length > 0) {
    await syncAgentsMd();
  }
  const results = await runDeltaGates();
  const payload = stopHookPayload(results);
  const lines = results
    .filter((result) => result.problem)
    .map((result) => `stop-hook: ${result.gate} could not run: ${result.problem}`);
  const code = stopHookExit(payload, lines.length, event);
  if (payload) lines.push(payload);
  if (payload && code === 1) lines.push(LOOP_GUARD_NOTICE);
  if (lines.length > 0) await Bun.write(Bun.stderr, `${lines.join('\n')}\n`);
  process.exitCode = code;
}

/** The project source file a PostToolUse event names, relative to `root`; else null. */
export async function hookTarget(event: JsonObject, root: string): Promise<string | null> {
  const filePath = asObject(event.tool_input)?.file_path;
  if (typeof filePath !== 'string') return null;
  const { realpathSync, statSync } = await import('node:fs');
  const { relative, resolve, sep } = await import('node:path');
  try {
    const real = realpathSync(resolve(root, filePath));
    const path = relative(realpathSync(root), real).split(sep).join('/');
    // A path outside the project starts with `../` (or a drive), so it is never a target.
    return isProjectTsFile(path) && statSync(real).isFile() ? path : null;
  } catch {
    return null;
  }
}

/** PostToolUse: fix + format the file the event names; never blocks, asks for a re-read. */
async function postEditHook(): Promise<void> {
  const target = await hookTarget(await hookEvent(), ROOT);
  if (target === null) return;
  const { readFileSync } = await import('node:fs');
  const before = readFileSync(`${ROOT}/${target}`);
  await fixAndFormat([target]);
  if (readFileSync(`${ROOT}/${target}`).equals(before)) return;
  const additionalContext = `harness: reformatted ${target}; re-read it before editing it again`;
  console.log(
    JSON.stringify({ hookSpecificOutput: { hookEventName: 'PostToolUse', additionalContext } }),
  );
}

async function cmdPostEdit(): Promise<void> {
  if (process.argv.includes('--hook')) return await postEditHook();
  const files = await changedTsFiles();
  if (files.length === 0) return;
  await run('Fix & format', ['bunx', 'biome', 'check', '--write', ...files], { noExit: true });
}

// ── Stages ──────────────────────────────────────────────────────────

async function checkStopHooksPresent(): Promise<void> {
  // Warn when Claude/Codex Stop or Claude PostToolUse wiring is missing.
  const { existsSync } = await import('node:fs');
  const { readFile } = await import('node:fs/promises');
  const wirings = [
    ['.claude/settings.json', 'Stop', 'stop-hook'],
    ['.claude/settings.json', 'PostToolUse', 'post-edit --hook'],
    ['.codex/hooks.json', 'Stop', 'stop-hook'],
  ];
  for (const [rel, event, command] of wirings) {
    const full = `${ROOT}/${rel}`;
    const text = existsSync(full) ? await readFile(full, 'utf8') : '';
    if (text.includes(event) && text.includes(command)) {
      console.log(`  ${GREEN}✓${RESET} ${event} hook wiring (${rel})`);
    } else {
      console.log(`  ${RED}⚠${RESET} Missing ${event} hook wiring: ${rel}`);
    }
  }
}

type JsonObject = Record<string, unknown>;

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

function isStopHookHandler(handler: unknown): boolean {
  const obj = asObject(handler);
  return obj !== null && obj.type === 'command' && typeof obj.command === 'string'
    ? obj.command.includes('stop-hook')
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

async function installStopHook(
  rel: string,
  hook: JsonObject,
  claudeSettings = false,
): Promise<void> {
  // Inject/refresh the Stop hook, preserving every other hook. Idempotent: an
  // existing stop-hook handler (current or legacy) is replaced and duplicates
  // dropped, so re-running never accumulates entries.
  const { existsSync, readFileSync, writeFileSync, mkdirSync } = await import('node:fs');
  const { dirname } = await import('node:path');
  const path = `${ROOT}/${rel}`;
  let data: JsonObject = {};
  if (existsSync(path)) {
    const text = readFileSync(path, 'utf8').trim();
    if (text) {
      const parsed: unknown = JSON.parse(text);
      const obj = asObject(parsed);
      if (!obj) throw new Error(`${rel} must contain a JSON object`);
      data = obj;
    }
  }
  if (claudeSettings && !('$schema' in data)) data.$schema = CLAUDE_SETTINGS_SCHEMA;

  const hooks = jsonObjectChild(data, 'hooks', rel);
  const stopGroups = jsonListChild(hooks, 'Stop', rel);
  let installed = false;
  for (const group of stopGroups) {
    const groupObj = asObject(group);
    if (!groupObj || !Array.isArray(groupObj.hooks)) continue;
    const next: unknown[] = [];
    for (const handler of groupObj.hooks) {
      if (isStopHookHandler(handler)) {
        if (!installed) {
          next.push({ ...hook });
          installed = true;
        }
        continue;
      }
      next.push(handler);
    }
    groupObj.hooks = next;
  }
  if (!installed) stopGroups.push({ hooks: [{ ...hook }] });

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
  const { existsSync, readFileSync, writeFileSync } = await import('node:fs');
  const claudePath = `${ROOT}/CLAUDE.md`;
  if (!existsSync(claudePath)) {
    console.log(`  ${RED}✗${RESET} sync-agents-md: CLAUDE.md not found`);
    process.exit(1);
  }
  writeFileSync(`${ROOT}/AGENTS.md`, readFileSync(claudePath));
  console.log(`  ${GREEN}✓${RESET} sync-agents-md: AGENTS.md ← CLAUDE.md`);
}

/** pre-commit: a staged CLAUDE.md carries its AGENTS.md mirror into the same commit. */
async function syncAgentsMdStaged(): Promise<void> {
  const staged = await gitLines(['diff', '--cached', '--name-only', '--', 'CLAUDE.md']);
  if (staged.length === 0 || !(await syncAgentsMd())) return;
  // `run` keeps git's hook environment: GIT_INDEX_FILE is the index this commit builds from.
  await run('sync-agents-md: AGENTS.md ← CLAUDE.md (staged)', ['git', 'add', '--', 'AGENTS.md']);
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
  await installStopHook('.codex/hooks.json', CODEX_STOP_HOOK);
  await installStopHook('.claude/settings.json', CLAUDE_STOP_HOOK, true);
  console.log('Installed pre-commit, pre-push, and Claude/Codex Stop hooks');
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
  'setup-hooks': [cmdHooks, 'Install git pre-commit + pre-push hooks and Claude/Codex Stop wiring'],
  'post-edit': [cmdPostEdit, 'Format changed files (--hook: the file a PostToolUse names)'],
  'stop-hook': [
    cmdStopHook,
    'post-edit, then changed-lines lint, touched over-limit functions, changed-lines ' +
      'deadcode; silent on success, exit 2 with findings',
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
