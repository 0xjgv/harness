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
const COMPLEXITY_MAX_ARGS = 8;
const COMPLEXITY_MAX_CCN = 15;
const CRAP_MAX_DEFAULT = 30;
// `-i N` high enough that lizard never exits non-zero on warnings: used wherever
// lizard is a measuring tape (report-only complexity, CRAP scoring, baseline
// measurement) rather than a gate.
const REPORT_ONLY_LIMIT = 1_000_000;
const COVERAGE_CMD = [
  'bun',
  'test',
  '--coverage',
  '--coverage-reporter=lcov',
  '--coverage-dir=coverage',
] as const;
const ROOT = import.meta.dir;
const BASELINE_FILE = '.harness-baseline';
const SUPPRESSION_BASELINE_PREFIX = 'suppressions.';
// Every gate that finds no floor points at the same command.
const BASELINE_FLOOR_HINT = 'run `bun harness.ts suppressions --update-baseline` to record a floor';
const ARCH_CONFIGS = ['.dependency-cruiser.json'] as const;
const ARCH_CONFIG_ALLOW_ENV = 'HARNESS_ALLOW_ARCH_CONFIG';
const GHERKIN_ALLOW_ENV = 'HARNESS_ALLOW_NO_FEATURE';

// ── Hook wiring (installed by `setup-hooks`) ────────────────────────
// Claude reads .claude/settings.json and runs the harness directly; Codex reads
// .codex/hooks.json and goes through the codex-stop-hook.sh wrapper (which turns
// the exit code into the block/continue JSON Codex expects). Keep both in sync
// with the committed template files so re-running the installer is a no-op.
const CLAUDE_SETTINGS_SCHEMA = 'https://json.schemastore.org/claude-code-settings.json';
const CLAUDE_STOP_COMMAND = 'cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook || exit 2';
const CODEX_STOP_COMMAND =
  'cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh bun harness.ts stop-hook';
const CLAUDE_STOP_HOOK = { type: 'command', command: CLAUDE_STOP_COMMAND };
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
  /**
   * Decide pass/fail from the tool's output. Receives the exit code so a gate can
   * ignore it (depcruise: exit code = error count) or refuse to score a crashed
   * tool (lizard). `detail` replaces `extract` on the result line; `output`
   * replaces what is echoed on failure.
   */
  verdict?: (output: string, exitCode: number) => { ok: boolean; detail?: string; output?: string };
  hint?: string;
}

export interface GateResult {
  description: string;
  cmd: string[];
  ok: boolean;
  exitCode: number;
  output: string;
  detail?: string;
  hint?: string;
}

/** Run a command with output captured (no printing, no exit): the unit the batch runs. */
export async function runCapture(gate: Gate): Promise<GateResult> {
  const proc = Bun.spawn(gate.cmd, { cwd: ROOT, stdout: 'pipe', stderr: 'pipe' });
  const [stdout, stderr] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
  ]);
  const exitCode = await proc.exited;
  const output = stdout + stderr;
  const verdict = gate.verdict?.(output, exitCode);
  const ok = verdict ? verdict.ok : exitCode === 0;
  // a verdict that fails on a clean exit still has to exit non-zero
  const effectiveExit = ok ? exitCode : exitCode === 0 ? 1 : exitCode;
  return {
    description: gate.description,
    cmd: gate.cmd,
    ok,
    exitCode: effectiveExit,
    output: verdict?.output ?? output,
    detail: verdict?.detail ?? (ok ? gate.extract?.(output) : undefined),
    hint: gate.hint,
  };
}

/** Print a gate's ✓/✗ line (with the failure body); exit on failure unless noExit. */
function printGateResult(result: GateResult, opts?: { noExit?: boolean }): boolean {
  if (VERBOSE) console.log(`${DIM}  → ${result.cmd.join(' ')}${RESET}`);
  if (VERBOSE && result.output.trim()) console.log(result.output);

  const suffix = result.detail ? ` ${DIM}(${result.detail})${RESET}` : '';
  if (result.ok) {
    console.log(`  ${GREEN}✓${RESET} ${result.description}${suffix}`);
    return true;
  }
  console.log(`  ${RED}✗${RESET} ${result.description}${suffix}`);
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
  return (await runGatesParallelDetailed(gates)).ok;
}

/** Same as runGatesParallel, plus which gates failed — stop-hook needs the names for its stderr summary. */
async function runGatesParallelDetailed(gates: Gate[]): Promise<{ ok: boolean; failed: string[] }> {
  if (gates.length === 0) return { ok: true, failed: [] };
  const results = await Promise.all(gates.map((gate) => runCapture(gate)));
  const failed: string[] = [];
  for (const result of results) {
    if (!printGateResult(result, { noExit: true })) failed.push(result.description);
  }
  return { ok: failed.length === 0, failed };
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

export interface SuppressionFinding extends SuppressionMatch {
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
// Every kind the scanner can report — the baseline writer records each one, so a
// kind that vanished from the tree ratchets to 0 instead of lingering at its old count.
const SUPPRESSION_KINDS = [
  ...TS_DIRECTIVE_PATTERNS.map((d) => d.kind),
  'eslint-disable',
  'biome-ignore',
];

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

export async function scanSuppressionFindings(roots?: string[]): Promise<SuppressionFinding[]> {
  const { readdir, readFile, stat } = await import('node:fs/promises');
  const { isAbsolute, join, relative } = await import('node:path');
  const actualRoots = roots ?? (await qualityTargets());
  const findings: SuppressionFinding[] = [];

  // Locations are always reported relative to ROOT (the template root), never
  // absolute and never as the raw (possibly relative-to-cwd) input path — both the
  // single-file branch and the directory-walk branch route through the same
  // relative(ROOT, full) so the suppression report never mixes formats.
  async function scanPath(rawPath: string): Promise<void> {
    const full = isAbsolute(rawPath) ? rawPath : join(ROOT, rawPath);
    const info = await stat(full).catch(() => null);
    if (!info) return;
    if (info.isFile()) {
      if (!full.endsWith('.ts')) return;
      const text = await readFile(full, 'utf8').catch(() => null);
      if (text == null) return;
      const location = relative(ROOT, full);
      for (const [index, line] of text.split('\n').entries()) {
        for (const m of parseLineForSuppressions(line)) {
          findings.push({ ...m, location: `${location}:${index + 1}` });
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
        const location = relative(ROOT, child);
        for (const [index, line] of text.split('\n').entries()) {
          for (const m of parseLineForSuppressions(line)) {
            findings.push({ ...m, location: `${location}:${index + 1}` });
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

export type MinArgResult =
  | { present: false }
  | { present: true; ok: true; value: number }
  | { present: true; ok: false; raw: string };

/** Parse `--min=N` out of argv. Pure — kept separate so the invalid-value path is testable. */
export function parseMinArg(argv: string[]): MinArgResult {
  const minArg = argv.find((a) => a.startsWith('--min='));
  if (!minArg) return { present: false };
  const raw = minArg.split('=', 2)[1];
  const value = Number(raw);
  if (Number.isInteger(value)) return { present: true, ok: true, value };
  return { present: true, ok: false, raw };
}

export async function coverageMinDefault(base = ROOT): Promise<number> {
  const parsed = parseMinArg(process.argv);
  if (parsed.present) {
    if (!parsed.ok) {
      console.log(`  ${RED}✗${RESET} Coverage: --min=${parsed.raw} is not an integer`);
      process.exit(1);
    }
    return parsed.value;
  }
  const baseline = await readBaseline(base);
  return baseline?.['coverage.min'] ?? 0;
}

/**
 * The committed floor for a ratcheted metric, or undefined when there is none.
 *
 * undefined means "never measured here" — no `.harness-baseline` at all, or a file
 * that does not carry this key. Every gate reading a floor must then run
 * report-only: retrofitting the harness into an existing repo has to be green on
 * day one, and a floor of 0 inferred from a missing number is not a floor, it is
 * a demand that the repo already be perfect.
 */
export async function baselineFloor(key: string, base = ROOT): Promise<number | undefined> {
  const baseline = await readBaseline(base);
  return baseline?.[key];
}

/**
 * A metric's value, or the reason there isn't one. Three states, deliberately not
 * collapsed into `number | undefined`:
 *   - `value` — measured, including a legitimate 0.
 *   - `unavailable` — the metric does not apply to this repo (no tests, no app
 *     sources). The baseline key is dropped, and the gate goes report-only.
 *   - `error` — the measuring tool ran and failed. `--update-baseline` aborts and
 *     writes nothing: a floor recorded from a broken run is worse than no floor,
 *     because every downstream gate trusts it.
 */
export type Measurement = { value: number } | { unavailable: string } | { error: string };

/** One ratcheted `.harness-baseline` key and how `--update-baseline` measures it. */
export interface RatchetedMetric {
  key: string;
  measure: () => Promise<Measurement>;
}

export type BaselineWrite =
  | { ok: true; baseline: Record<string, number> }
  | { ok: false; broken: [string, string][] };

/**
 * Merge measured floors over the existing baseline; unknown keys are preserved.
 *
 * Every key the harness measures is rewritten (so a metric that improved ratchets
 * down); every key it does not recognise is carried through untouched
 * (`mutation.min` is one — a mutation run costs minutes, never the automatic pass).
 *
 * A metric that does not apply here has its key *removed*, never carried forward:
 * the shipped template's own numbers must not survive into an adopting repo's
 * first baseline. A metric that could not be measured aborts the whole write and
 * nothing is written — see `Measurement`.
 */
export async function writeBaseline(
  results: Record<string, string[][]>,
  measurements: Record<string, Measurement>,
  base = ROOT,
): Promise<BaselineWrite> {
  const broken: [string, string][] = [];
  for (const [key, measured] of Object.entries(measurements)) {
    if ('error' in measured) broken.push([key, measured.error]);
  }
  if (broken.length > 0) return { ok: false, broken };

  const baseline = (await readBaseline(base)) ?? {};
  for (const kind of SUPPRESSION_KINDS) baseline[`${SUPPRESSION_BASELINE_PREFIX}${kind}`] = 0;
  Object.assign(baseline, suppressionCounts(results));
  for (const [key, measured] of Object.entries(measurements)) {
    if ('value' in measured) {
      baseline[key] = measured.value;
    } else if ('unavailable' in measured && key in baseline) {
      delete baseline[key];
      warn(`${key}: dropped — ${measured.unavailable}`);
    }
  }

  const lines = Object.keys(baseline)
    .sort()
    .map((key) => `${key} ${baseline[key]}`);
  const { writeFile } = await import('node:fs/promises');
  await writeFile(`${base}/${BASELINE_FILE}`, `${lines.join('\n')}\n`);
  return { ok: true, baseline };
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
    const written = await writeBaseline(results, await measureRatcheted());
    if (!written.ok) {
      console.log(`  ${RED}✗${RESET} ${BASELINE_FILE} not written — could not measure:`);
      for (const [key, reason] of written.broken) console.log(`    ${key}: ${reason}`);
      console.log(
        '  ↳ fix: make the measurement pass, then rerun `bun harness.ts suppressions --update-baseline`',
      );
      process.exit(1);
    }
    const total = Object.values(results).reduce((sum, arr) => sum + arr.length, 0);
    const recorded = [
      `suppressions ${total}`,
      ...RATCHETED_METRICS.filter(({ key }) => key in written.baseline).map(
        ({ key }) => `${key} ${written.baseline[key]}`,
      ),
    ].join(', ');
    console.log(`  ${GREEN}✓${RESET} ${BASELINE_FILE}: ${recorded}`);
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

async function changedTsFiles(): Promise<string[]> {
  // `git status --porcelain` always prints repo-root-relative paths, regardless of
  // cwd — `-- .` scopes results to this template's subtree, and gitPrefix()/
  // normalizeChangedPath() convert those repo-root-relative paths back to paths
  // relative to ROOT (needed whenever the template isn't the git root, e.g. this
  // monorepo-style checkout).
  const proc = Bun.spawn(['git', 'status', '--porcelain', '--', '.'], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
  });
  const stdout = await new Response(proc.stdout).text();
  await proc.exited;
  const prefix = await gitPrefix();
  return stdout
    .trim()
    .split('\n')
    .filter((line) => line.length > 3 && !line.slice(0, 2).includes('D'))
    .map(porcelainPath)
    .map((f) => normalizeChangedPath(f, prefix))
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

  await run('Coverage (run)', [...COVERAGE_CMD], { extract: extractTestSummary });

  const lcov = await readLcov();
  if (lcov == null) {
    console.log(`  ${RED}✗${RESET} Coverage: coverage/lcov.info not found`);
    process.exit(1);
  }
  const pct = lcovLinePercent(lcov);
  if (pct >= minPct) {
    console.log(`  ${GREEN}✓${RESET} Coverage >= ${minPct}% ${DIM}(${pct.toFixed(1)}%)${RESET}`);
  } else {
    console.log(`  ${RED}✗${RESET} Coverage >= ${minPct}% ${DIM}(got ${pct.toFixed(1)}%)${RESET}`);
    process.exit(1);
  }
}

async function readLcov(): Promise<string | null> {
  const { readFile } = await import('node:fs/promises');
  return readFile(`${ROOT}/coverage/lcov.info`, 'utf8').catch(() => null);
}

/** Line coverage percent over every LCOV record; 100 when the report covers no lines. */
function lcovLinePercent(lcov: string): number {
  let found = 0;
  let hit = 0;
  for (const line of lcov.split('\n')) {
    if (line.startsWith('LF:')) found += Number(line.slice(3));
    else if (line.startsWith('LH:')) hit += Number(line.slice(3));
  }
  return found === 0 ? 100 : (hit / found) * 100;
}

async function measuredCoverage(): Promise<Measurement> {
  if (!(await hasTests())) return { unavailable: `no ${TEST_DIR}/*.test.ts or *.spec.ts files` };
  if (!(await artifactIsFresh('coverage/lcov.info', await qualityTargets()))) {
    // The test run's exit code is load-bearing: a suite whose every module fails
    // to import still produces a coverage number (a very low one), and recording
    // that as the floor bakes a broken run into the ratchet.
    const { exitCode } = await runCapture({
      description: 'Coverage (run)',
      cmd: [...COVERAGE_CMD],
    });
    if (exitCode !== 0) return { error: `the test run under coverage failed (exit ${exitCode})` };
  }
  const lcov = await readLcov();
  if (lcov == null) return { error: 'coverage/lcov.info not found after coverage run' };
  return { value: Math.floor(lcovLinePercent(lcov)) };
}

// Shared by acceptanceGatesOrWarn (warns when absent) and the gherkin-first
// guard (skips silently when absent) — both need to know whether the
// template has adopted an acceptance suite at all.
async function hasAnyFeatureFiles(base = ROOT): Promise<boolean> {
  const { existsSync } = await import('node:fs');
  const featuresDir = `${base}/${TEST_DIR}/features`;
  if (!existsSync(featuresDir)) return false;
  const glob = new Bun.Glob('**/*.feature');
  const matches = await Array.fromAsync(glob.scan({ cwd: featuresDir, onlyFiles: true }));
  return matches.length > 0;
}

async function acceptanceGatesOrWarn(): Promise<Gate[]> {
  // Build the cucumber-js gate, or warn + return [] when there are no scenarios.
  if (!(await hasAnyFeatureFiles())) {
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

export function normalizeChangedPath(path: string, prefix: string): string {
  const normalized = path.trim().replace(/^\.\//, '').replace(/\\/g, '/');
  if (prefix !== '' && normalized.startsWith(`${prefix}/`)) {
    return normalized.slice(prefix.length + 1);
  }
  return normalized;
}

// GITHUB_BASE_REF is only set for pull_request events; a direct push to main (or
// any branch push trigger) has neither env var set, so without a fallback the
// arch-config guard silently skips the diff-from-base check entirely on push.
const ARCH_BASE_FALLBACK_REFS = ['origin/HEAD', 'origin/main', 'main'] as const;

/** First ref that resolves, in priority order — or undefined if none do. Pure
 * apart from the injected verify() so the ordering is unit-testable. */
export async function resolveFallbackArchBase(
  verify: (ref: string) => Promise<boolean>,
): Promise<string | undefined> {
  for (const ref of ARCH_BASE_FALLBACK_REFS) {
    if (await verify(ref)) return ref;
  }
  return undefined;
}

async function changedPathsFromBase(): Promise<string[]> {
  const bases: string[] = [];
  if (process.env.HARNESS_ARCH_BASE) bases.push(process.env.HARNESS_ARCH_BASE);
  if (process.env.GITHUB_BASE_REF) bases.push(`origin/${process.env.GITHUB_BASE_REF}`);

  if (bases.length === 0) {
    const fallback = await resolveFallbackArchBase(
      async (ref) => (await gitLines(['rev-parse', '--verify', ref])).length > 0,
    );
    if (fallback) bases.push(fallback);
  }

  const paths: string[] = [];
  for (const base of bases) {
    if ((await gitLines(['rev-parse', '--verify', base])).length === 0) continue;
    paths.push(
      ...(await gitLines(['diff', '--name-only', '--diff-filter=d', `${base}...HEAD`, '--', '.'])),
    );
  }
  return paths;
}

async function changedPathsFromPrePushStdin(): Promise<string[]> {
  if (process.stdin.isTTY) return [];
  const text = await new Response(Bun.stdin.stream()).text();
  if (text.trim() === '') return [];
  const zero = '0'.repeat(40);
  const paths: string[] = [];
  for (const line of text.split('\n')) {
    const parts = line.trim().split(/\s+/);
    if (parts.length < 4) continue;
    const [, localSha, , remoteSha] = parts;
    if (localSha === zero) continue;
    if (remoteSha === zero) {
      paths.push(
        ...(await gitLines([
          'diff-tree',
          '--no-commit-id',
          '--name-only',
          '-r',
          localSha,
          '--',
          '.',
        ])),
      );
    } else {
      paths.push(
        ...(await gitLines([
          'diff',
          '--name-only',
          '--diff-filter=d',
          remoteSha,
          localSha,
          '--',
          '.',
        ])),
      );
    }
  }
  return paths;
}

/**
 * Gather changed/staged/untracked paths (normalized to template-relative),
 * deduplicated. Shared plumbing for every changed-path guard — callers apply
 * their own filter (arch-config-guard filters to ARCH_CONFIGS, gherkin-guard
 * filters to production sources / .feature files).
 */
async function changedPathsForGuard(
  opts: { staged?: boolean; includePrePushStdin?: boolean } = {},
): Promise<string[]> {
  const paths: string[] = [];
  if (opts.staged) {
    paths.push(
      ...(await gitLines(['diff', '--cached', '--name-only', '--diff-filter=d', '--', '.'])),
    );
  } else {
    paths.push(...(await gitLines(['diff', '--name-only', '--diff-filter=d', '--', '.'])));
    paths.push(
      ...(await gitLines(['diff', '--cached', '--name-only', '--diff-filter=d', '--', '.'])),
    );
    paths.push(...(await gitLines(['ls-files', '--others', '--exclude-standard', '--', '.'])));
    paths.push(...(await changedPathsFromBase()));
  }
  if (opts.includePrePushStdin) paths.push(...(await changedPathsFromPrePushStdin()));

  const prefix = await gitPrefix();
  return Array.from(new Set(paths.map((p) => normalizeChangedPath(p, prefix))));
}

async function changedArchConfigs(
  opts: { staged?: boolean; includePrePushStdin?: boolean } = {},
): Promise<string[]> {
  const protectedPaths = new Set<string>(ARCH_CONFIGS);
  const paths = await changedPathsForGuard(opts);
  return paths.filter((p) => protectedPaths.has(p)).sort();
}

async function checkArchConfigGuard(
  opts: { warnOnly?: boolean; staged?: boolean; includePrePushStdin?: boolean } = {},
): Promise<boolean> {
  const changed = await changedArchConfigs({
    staged: opts.staged,
    includePrePushStdin: opts.includePrePushStdin,
  });
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
  const ok = await checkArchConfigGuard({
    warnOnly: process.argv.includes('--warn'),
    staged: process.argv.includes('--staged'),
  });
  if (!ok) process.exit(1);
}

// ── Gherkin-first guard ─────────────────────────────────────────────
// Mechanizes the "write a .feature before changing user-visible behavior"
// rule from the behavior contract. "Production source" is deliberately
// scoped to APP_SOURCES (src/), excluding TEST_DIR — not the runner itself,
// not tests — so the trigger is predictable: it fires only on the kind of
// change the behavior contract is actually about.

export function isProductionSourcePath(path: string): boolean {
  const underAppSource = APP_SOURCES.some((src) => path === src || path.startsWith(`${src}/`));
  if (!underAppSource) return false;
  return !(path === TEST_DIR || path.startsWith(`${TEST_DIR}/`));
}

export interface GherkinGuardInput {
  changedPaths: string[];
  hasFeatureFiles: boolean;
  overrideEnv?: string;
}

export interface GherkinGuardDecision {
  /** No .feature files anywhere in the template — guard is inactive, pass silently. */
  skip: boolean;
  /** Production source changed with no matching .feature change. */
  trigger: boolean;
  /** trigger is true, but HARNESS_ALLOW_NO_FEATURE=1 overrides it. */
  override: boolean;
  changedProductionSources: string[];
}

/** Pure decision logic — no git, no fs — so it's unit-testable directly. */
export function evaluateGherkinGuard(input: GherkinGuardInput): GherkinGuardDecision {
  if (!input.hasFeatureFiles) {
    return { skip: true, trigger: false, override: false, changedProductionSources: [] };
  }
  const changedProductionSources = input.changedPaths.filter(isProductionSourcePath);
  const changedFeature = input.changedPaths.some((p) => p.endsWith('.feature'));
  const trigger = changedProductionSources.length > 0 && !changedFeature;
  const override = trigger && input.overrideEnv === '1';
  return { skip: false, trigger, override, changedProductionSources };
}

async function checkGherkinGuard(
  opts: { warnOnly?: boolean; staged?: boolean; includePrePushStdin?: boolean } = {},
): Promise<boolean> {
  const hasFeatureFiles = await hasAnyFeatureFiles();
  const changedPaths = hasFeatureFiles
    ? await changedPathsForGuard({
        staged: opts.staged,
        includePrePushStdin: opts.includePrePushStdin,
      })
    : [];
  const decision = evaluateGherkinGuard({
    changedPaths,
    hasFeatureFiles,
    overrideEnv: process.env[GHERKIN_ALLOW_ENV],
  });

  // Retrofitting into a repo with no acceptance suite must never block — pass
  // with no output at all rather than warn every run about a rule that
  // doesn't apply yet.
  if (decision.skip) return true;

  if (!decision.trigger) {
    console.log(`  ${GREEN}✓${RESET} Gherkin-first`);
    return true;
  }

  const joined = decision.changedProductionSources.join(', ');
  if (decision.override) {
    console.log(`  ${GREEN}⚠${RESET} Gherkin-first override: ${joined}`);
    return true;
  }
  const hint = `add a scenario under ${TEST_DIR}/features/, or set ${GHERKIN_ALLOW_ENV}=1 after review`;
  if (opts.warnOnly) {
    console.log(
      `  ${GREEN}⚠${RESET} Gherkin-first: production source changed with no .feature: ${joined}`,
    );
    console.log(`  ↳ fix: ${hint}`);
    return true;
  }
  console.log(
    `  ${RED}✗${RESET} Gherkin-first: production source changed with no .feature: ${joined}`,
  );
  console.log(`  ↳ fix: ${hint}`);
  return false;
}

async function cmdGherkinGuard(): Promise<void> {
  const ok = await checkGherkinGuard({
    warnOnly: process.argv.includes('--warn'),
    staged: process.argv.includes('--staged'),
  });
  if (!ok) process.exit(1);
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

/**
 * Glyph for a CRAP offender line. A passing gate (exit 0, the default
 * advisory mode) must never show the red ✗ — that's reserved for --enforce,
 * which actually exits non-zero on offenders. Pure so it's unit-testable
 * without shelling out to lizard.
 */
export function crapOffenderGlyph(enforce: boolean): string {
  return enforce ? `${RED}✗${RESET}` : `${GREEN}⚠${RESET}`;
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

interface CrapMeasurement {
  offenders: CrapFn[];
  /** Why scoring did not happen. With `code` set the tool failed; without it, a benign skip. */
  problem?: string;
  detail?: string;
  code?: number;
}

/** Score every function's CRAP against `maxCrap`, refreshing coverage if stale. */
async function crapMeasure(maxCrap: number): Promise<CrapMeasurement> {
  if (!(await artifactIsFresh('coverage/lcov.info', await qualityTargets()))) {
    await cmdCoverage();
  }

  const lcov = await readLcov();
  if (lcov == null) {
    return { offenders: [], problem: 'coverage/lcov.info not found after coverage run' };
  }

  // Parse LCOV into { file: { lineNumber: hits } }.
  const covMap = parseLcov(lcov);
  const targets = await appTargets();
  if (targets.length === 0) return { offenders: [], problem: 'no app sources; skipped' };

  // lizard --csv columns: nloc,ccn,token,param,length,location,file,name,sig,start,end
  // `-i` high: here lizard is a measuring tape, not a gate. At its default
  // (`-i 0`, CCN 15) it exits 1 on any complex function, and CRAP would report
  // "lizard failed to run" for exactly the repos that need scoring most.
  const lz = Bun.spawn(['uvx', LIZARD, ...targets, '--csv', '-i', String(REPORT_ONLY_LIMIT)], {
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
    return {
      offenders: [],
      problem: `lizard failed to run (exit ${lzCode})`,
      detail: lzErr.trim(),
      code: lzCode,
    };
  }

  // lizard --csv: column 1 is CCN; the quoted location field encodes
  // `name@start-end@path`. Signatures can contain commas, so derive the
  // location (and thus path/start/end) from that self-contained field.
  // Name may be empty for anonymous arrows/IIFEs — match but skip cleanly.
  const locRe = /"([^"@]*)@(\d+)-(\d+)@([^"]+)"/;
  const offenders: CrapFn[] = [];
  for (const row of lzOut.split('\n')) {
    const cols = row.split(',');
    if (cols.length < 11) continue;
    const ccn = Number(cols[1]);
    if (!Number.isFinite(ccn)) continue;
    const lm = locRe.exec(row);
    if (!lm) continue;
    const [, name, startS, endS, path] = lm;
    // Anonymous functions: lizard emits an empty name. They share their
    // parent's coverage attribution in LCOV, so a per-function join cannot
    // score them fairly — skip rather than silently misattribute.
    if (!name) continue;
    const start = Number(startS);
    const end = Number(endS);
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
  offenders.sort((a, b) => b.crap - a.crap);
  return { offenders };
}

async function cmdCrap(): Promise<void> {
  // CRAP = ccn^2 * (1-cov)^3 + ccn per function. Advisory — lizard + LCOV.
  if (!(await hasTests())) {
    warn('CRAP: no tests; skipped');
    return;
  }

  const maxArg = process.argv.find((a) => a.startsWith('--max='));
  const maxCrap = maxArg ? Number(maxArg.split('=', 2)[1]) : CRAP_MAX_DEFAULT;
  const enforce = process.argv.includes('--enforce');
  const suffix = enforce ? '' : ' (advisory)';

  const measurement = await crapMeasure(maxCrap);
  if (measurement.problem) {
    if (measurement.code == null) {
      warn(`CRAP: ${measurement.problem}`);
      return;
    }
    console.log(`  ${crapOffenderGlyph(enforce)} CRAP: ${measurement.problem}${suffix}`);
    if (measurement.detail) console.log(measurement.detail);
    if (enforce) process.exit(measurement.code);
    return;
  }

  const { offenders } = measurement;
  if (offenders.length === 0) {
    console.log(`  ${GREEN}✓${RESET} CRAP: all functions below ${maxCrap}`);
    return;
  }

  // The baseline is a count floor: a repo adopting the harness starts wherever it
  // already is, and that number may only come down.
  const floor = await baselineFloor('crap.max_violations');
  if (floor === undefined) {
    // Nothing recorded is not a floor of 0; it is a repo that has never been
    // measured. Report what is there and pass — `--enforce` included — so
    // retrofitting the harness into a legacy tree is green on day one.
    console.log(
      `  ${GREEN}⚠${RESET} CRAP: ${offenders.length} function(s) exceed ` +
        `${maxCrap} (report-only: no ${BASELINE_FILE} floor)`,
    );
    printCrapOffenders(offenders);
    console.log(`  ↳ fix: ${BASELINE_FLOOR_HINT}`);
    return;
  }

  const summary = `CRAP: ${offenders.length} function(s) exceed ${maxCrap} (baseline ${floor})`;
  if (offenders.length <= floor) {
    console.log(`  ${GREEN}✓${RESET} ${summary}`);
    return;
  }
  console.log(`  ${crapOffenderGlyph(enforce)} ${summary}${suffix}`);
  printCrapOffenders(offenders);
  if (enforce) process.exit(1);
}

/** List the worst offenders, capped so a legacy tree does not bury the summary. */
function printCrapOffenders(offenders: CrapFn[]): void {
  for (const o of offenders.slice(0, 20)) {
    console.log(
      `    CRAP=${o.crap.toFixed(1).padStart(6)}  CCN=${String(o.ccn).padStart(3)}  ` +
        `cov=${(o.cov * 100).toFixed(1).padStart(5)}%  ${o.loc}`,
    );
  }
}

async function measuredCrapViolations(): Promise<Measurement> {
  if (!(await hasTests())) return { unavailable: `no ${TEST_DIR}/*.test.ts or *.spec.ts files` };
  const measurement = await crapMeasure(CRAP_MAX_DEFAULT);
  if (measurement.problem) {
    // `code` set is a tool failure; unset is a benign skip (no app sources).
    if (measurement.code == null) return { unavailable: measurement.problem };
    return { error: measurement.problem };
  }
  return { value: measurement.offenders.length };
}

/**
 * lizard argv at this template's thresholds, tolerating `maxViolations` warnings.
 * lizard's own `-i N` is the count ratchet: it exits 0 while the number of
 * flagged functions stays at or below N, so lizard does the counting.
 */
function complexityArgv(targets: string[], maxViolations: number): string[] {
  return [
    'uvx',
    LIZARD,
    ...targets,
    '-C',
    String(COMPLEXITY_MAX_CCN),
    '-a',
    String(COMPLEXITY_MAX_ARGS),
    '-L',
    '100',
    '-i',
    String(maxViolations),
  ];
}

/** Read the `Warning cnt` column out of lizard's final summary row. */
export function lizardWarningCount(stdout: string): number | null {
  const lines = stdout.split('\n');
  const header = lines.findIndex((line) => line.startsWith('Total nloc'));
  if (header < 0) return null;
  for (const row of lines.slice(header + 1)) {
    const fields = row.trim().split(/\s+/);
    if (fields.length < 6 || /^-+$/.test(row.trim())) continue;
    const count = Number(fields[5]);
    return Number.isInteger(count) ? count : null;
  }
  return null;
}

/**
 * Run lizard over the gate's own targets and turn its report into a Measurement.
 *
 * `count` returning null is an `error`, never a 0: lizard printing no countable
 * report means the run is unusable, and a floor recorded from an unusable run is
 * worse than no floor at all — every downstream gate trusts it.
 */
async function measuredLizard(
  description: string,
  argv: (targets: string[]) => string[],
  count: (output: string) => number | null,
  missing: string,
): Promise<Measurement> {
  const targets = await appTargets({ includeTests: true });
  if (targets.length === 0) return { unavailable: 'no app sources' };
  const res = await runCapture({ description, cmd: argv(targets) });
  if (!res.ok) return { error: `lizard failed to run (exit ${res.exitCode})` };
  const value = count(res.output);
  if (value == null) return { error: missing };
  return { value };
}

async function measuredComplexityViolations(): Promise<Measurement> {
  // `-i` high enough that lizard exits 0 and still prints the summary row.
  return measuredLizard(
    'Complexity',
    (targets) => complexityArgv(targets, REPORT_ONLY_LIMIT),
    lizardWarningCount,
    'lizard printed no summary row to count warnings from',
  );
}

/**
 * lizard argv for the duplicate-block scan: the same target set as the
 * complexity gate (the floor only reproduces against an identical one) plus
 * `-Eduplicate`. `-w` drops the per-function table, and `-i` is parked high
 * because lizard's exit code is driven by CCN warnings only — the duplicate
 * count never reaches it, so the runner enforces the floor itself.
 */
function duplicationArgv(targets: string[]): string[] {
  return ['uvx', LIZARD, ...targets, '-Eduplicate', '-w', '-i', String(REPORT_ONLY_LIMIT)];
}

/**
 * Count the `Duplicate block:` headers in lizard's `-Eduplicate` report.
 *
 * null means there was no report to count, never a clean 0: lizard prints the
 * `Total duplicate rate:` footer whenever the extension ran, including at zero
 * blocks, so its absence is a garbled run.
 */
export function duplicateBlockCount(stdout: string): number | null {
  const lines = stdout.split('\n').map((line) => line.trimEnd());
  if (!lines.some((line) => line.startsWith('Total duplicate rate:'))) return null;
  return lines.filter((line) => line === 'Duplicate block:').length;
}

async function measuredDuplicateBlocks(): Promise<Measurement> {
  return measuredLizard(
    'Duplication',
    duplicationArgv,
    duplicateBlockCount,
    'lizard printed no duplicate report to count blocks from',
  );
}

/**
 * The duplicate-block gate at the committed floor, or report-only when absent.
 *
 * Copy-paste is the one complexity signal a per-function CCN gate cannot see: a
 * block duplicated ten times is ten simple functions. lizard reports a block
 * once it spans ~70 unified tokens, and overlapping near-duplicates are counted
 * separately, so the number jitters by one on trivial edits — fine for a floor
 * that only has to stop the trend, wrong for a hard threshold.
 */
function duplicationGate(targets: string[], floor: number | undefined): Gate {
  return {
    description:
      floor === undefined
        ? `Duplicate blocks (lizard, report-only: no ${BASELINE_FILE} floor)`
        : `Duplicate blocks (lizard, baseline ${floor})`,
    cmd: duplicationArgv(targets),
    verdict: (output, exitCode) => {
      // A crashed lizard prints no findings; scoring that as zero blocks is the
      // silent false-pass this gate exists to avoid. The verdict owns the rule
      // because the shared runner hands the exit code over rather than acting on
      // it — depcruise's arch gate reads its exit code as a count.
      if (exitCode !== 0) return { ok: false, detail: `lizard exited ${exitCode}` };
      // Report-only passes whatever it finds — a garbled report included. A repo
      // with no recorded floor must not go red on day one, and
      // `--update-baseline` errors on the same output anyway, so the missing
      // report is never recorded as a clean 0.
      const count = duplicateBlockCount(output);
      const detail =
        count == null ? 'no duplicate report to count blocks from' : `${count} block(s)`;
      if (floor === undefined) return { ok: true, detail: `${detail}; ${BASELINE_FLOOR_HINT}` };
      return { ok: count != null && count <= floor, detail };
    },
    hint: 'extract the repeated block into a shared helper; do not raise the floor',
  };
}

/**
 * The lizard gates at their committed floors, or report-only when there is none.
 * With no floor recorded, `-i 0` would demand a legacy tree already be perfect —
 * exactly the day-one red that stops the harness being adopted. Measure instead.
 */
export async function complexityGatesOrWarn(base = ROOT): Promise<Gate[]> {
  const targets = await appTargets({ includeTests: true, base });
  if (targets.length === 0) {
    warn('Complexity: no app sources; skipped');
    return [];
  }
  const duplication = duplicationGate(targets, await baselineFloor('duplication.max_blocks', base));
  const floor = await baselineFloor('complexity.max_violations', base);
  if (floor === undefined) {
    return [
      {
        description: `Complexity (lizard, report-only: no ${BASELINE_FILE} floor)`,
        cmd: complexityArgv(targets, REPORT_ONLY_LIMIT),
        extract: () => BASELINE_FLOOR_HINT,
        hint: BASELINE_FLOOR_HINT,
      },
      duplication,
    ];
  }
  return [
    {
      description: floor ? `Complexity (lizard, baseline ${floor})` : 'Complexity (lizard)',
      cmd: complexityArgv(targets, floor),
      hint: `extract helpers or flatten branches until CCN <= ${COMPLEXITY_MAX_CCN}; do not raise the threshold`,
    },
    duplication,
  ];
}

/**
 * Every key `--update-baseline` rewrites, in measurement order. Later gates
 * append here; a key absent from this table is carried through the file
 * untouched (see writeBaseline).
 */
export const RATCHETED_METRICS: RatchetedMetric[] = [
  { key: 'coverage.min', measure: measuredCoverage },
  { key: 'complexity.max_violations', measure: measuredComplexityViolations },
  { key: 'duplication.max_blocks', measure: measuredDuplicateBlocks },
  // CRAP last: it re-runs the coverage suite a second time, and measureRatcheted
  // stops at the first error, so put it after the metrics that cannot pay for it.
  { key: 'crap.max_violations', measure: measuredCrapViolations },
];

/**
 * Measure every ratcheted metric, stopping at the first one that failed.
 * Sequential and short-circuiting on purpose: a broken tool usually breaks the
 * metrics after it too (CRAP re-runs the coverage suite), so the first failure
 * is the one worth reporting, and the later ones would only be noise.
 */
export async function measureRatcheted(
  metrics: readonly RatchetedMetric[] = RATCHETED_METRICS,
): Promise<Record<string, Measurement>> {
  const measured: Record<string, Measurement> = {};
  for (const { key, measure } of metrics) {
    const measurement = await measure();
    measured[key] = measurement;
    if ('error' in measurement) break;
  }
  return measured;
}

async function cmdComplexity(): Promise<void> {
  // The same batch runner the stages use, so this path keeps `extract` (the
  // report-only hint) and `verdict` (the duplicate-block count) — `run` takes a
  // command, not a gate, and would silently drop both.
  if (!(await runGatesParallel(await complexityGatesOrWarn()))) process.exit(1);
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

async function cmdPostEdit(): Promise<void> {
  const files = await changedTsFiles();
  if (files.length === 0) return;
  await run('Fix & format', ['bunx', 'biome', 'check', '--write', ...files], { noExit: true });
}

async function cmdStopHook(): Promise<void> {
  console.log('\n=== Stop Hook Checks ===\n');
  await cmdPostEdit(); // mutating — sequential, first
  await checkArchConfigGuard({ warnOnly: true });
  await checkGherkinGuard({ warnOnly: true });
  // read-only batch: complexity + dead code
  const { ok, failed } = await runGatesParallelDetailed([
    ...(await complexityGatesOrWarn()),
    deadcodeGate(),
  ]);
  if (!ok) {
    // Claude Code only treats a Stop hook as blocking on exit 2, and only reads
    // the failure back from stderr; any other non-zero exit is silently ignored
    // by the model. Codex's wrapper (.codex/hooks/codex-stop-hook.sh) turns any
    // non-zero exit into a block regardless, so this is safe for both agents.
    console.error(`stop-hook failed: ${failed.join(', ')}`);
    process.exit(2);
  }
}

// ── Stages ──────────────────────────────────────────────────────────

async function checkStopHooksPresent(): Promise<void> {
  // Warn when Claude/Codex Stop hook wiring is missing.
  const { existsSync } = await import('node:fs');
  const { readFile } = await import('node:fs/promises');
  for (const rel of ['.claude/settings.json', '.codex/hooks.json']) {
    const full = `${ROOT}/${rel}`;
    const text = existsSync(full) ? await readFile(full, 'utf8') : '';
    if (text.includes('Stop') && text.includes('stop-hook')) {
      console.log(`  ${GREEN}✓${RESET} Stop hook wiring (${rel})`);
    } else {
      console.log(`  ${RED}⚠${RESET} Missing Stop hook wiring: ${rel}`);
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
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined && !key.startsWith('GIT_')) env[key] = value;
  }
  const proc = Bun.spawn(['git', 'rev-parse', '--git-path', `hooks/${name}`], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
    env,
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

async function cmdAgentsMdDrift(): Promise<void> {
  await checkAgentsMdDrift();
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

  // Read-only offline gates, after the mutating fix step above. Folded into
  // `results` as one entry so the summary count stays accurate. Invariant (see
  // CLAUDE.md): `check` runs every gate that is offline, fast, and takes no
  // build lock — arch (dependency-cruiser) qualifies (local devDependency,
  // offline, no build lock), so it joins the batch too; only the network
  // audit, coverage, and CRAP stay `ci`-only.
  const offlineGates: Gate[] = [
    ...(await complexityGatesOrWarn()),
    deadcodeGate(),
    ...(await acceptanceGatesOrWarn()),
    ...(await archGatesOrWarn()),
  ];
  results.push({ ok: await runGatesParallel(offlineGates), output: '' });

  await checkStopHooksPresent();
  await checkArchConfigGuard({ warnOnly: true });
  await checkGherkinGuard({ warnOnly: true });
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

async function restageFixedFiles(files: string[]): Promise<void> {
  // cmdFix rewrites the working tree; without this the commit still records the
  // pre-fix INDEX blob. Only re-add files that still exist (cmdFix never deletes,
  // but stay defensive). Caveat documented in CLAUDE.md: for a partially staged
  // file, `git add` also stages its unstaged hunks — same trade-off lint-staged makes.
  const { existsSync } = await import('node:fs');
  const { join } = await import('node:path');
  const existing = files.filter((f) => existsSync(join(ROOT, f)));
  if (existing.length === 0) return;
  const proc = Bun.spawn(['git', 'add', '--', ...existing], {
    cwd: ROOT,
    stdout: 'pipe',
    stderr: 'pipe',
  });
  await proc.exited;
  console.log(`  ${GREEN}✓${RESET} Re-staged ${existing.length} fixed file(s)`);
}

// The arch-config and gherkin-first guards run before the staged-files early
// return: both are staged-mode and cheap, and a commit that stages only a
// non-source file (an arch config edit alone, a `.md`, a lockfile) must not
// bypass them.
async function cmdPreCommit(): Promise<void> {
  console.log(`\n${BLUE}[pre-commit]${RESET}\n`);
  if (!(await checkArchConfigGuard({ staged: true }))) process.exit(1);
  if (!(await checkGherkinGuard({ staged: true }))) process.exit(1);

  const files = await stagedTsFiles();
  if (files.length === 0) {
    console.log('No staged TypeScript files — skipping checks');
    return;
  }

  await cmdFix(files);
  await restageFixedFiles(files);
  await cmdTypecheck();
  await checkAgentsMdDrift();

  if (files.some((f) => isQualityTsFile(f))) {
    await cmdTest();
  }
}

async function cmdCi(): Promise<void> {
  console.log(`\n${BLUE}[ci]${RESET}\n`);
  // Read-only gates run as a parallel batch (captured, printed in submission order,
  // run to completion). Coverage is captured and CRAP is advisory — both after the batch.
  const gates: Gate[] = [
    lintGate(),
    typecheckGate(),
    auditGate(),
    ...(await complexityGatesOrWarn()),
    deadcodeGate(),
    ...(await acceptanceGatesOrWarn()),
    ...(await archGatesOrWarn()),
  ];
  const allOk = await runGatesParallel(gates);
  await cmdCoverage(); // self-skips; after the batch
  await cmdCrap(); // advisory unless --enforce
  const archConfigOk = await checkArchConfigGuard();
  const gherkinOk = await checkGherkinGuard();
  const suppressionsOk = await checkSuppressionsBaseline({ noExit: true });
  if (!allOk || !archConfigOk || !gherkinOk || !suppressionsOk) process.exit(1);
}

async function cmdPrePush(): Promise<void> {
  // Read-only push gate: the offline checks pre-commit and stop-hook do not run.
  // pre-commit covers fix/format/typecheck/test on staged files; stop-hook adds
  // complexity. This fills the gap with the deterministic, offline gates none of
  // them run — lint (biome covers format), acceptance, arch — validating the whole
  // pushed tree (after merges/rebases/--no-verify) before it leaves the machine.
  // Network (audit) and advisory (coverage/CRAP) gates stay in ci.
  console.log(`\n${BLUE}[pre-push]${RESET}\n`);
  const archConfigOk = await checkArchConfigGuard({ includePrePushStdin: true });
  const gherkinOk = await checkGherkinGuard({ includePrePushStdin: true });
  const gates: Gate[] = [
    lintGate(),
    ...(await acceptanceGatesOrWarn()),
    ...(await archGatesOrWarn()),
  ];
  if (!(await runGatesParallel(gates)) || !archConfigOk || !gherkinOk) process.exit(1);
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
  suppressions: [cmdSuppressions, 'Show suppressions; --update-baseline re-measures every floor'],
  complexity: [
    cmdComplexity,
    'Complexity + duplicate-block gates (lizard, CCN 15, args 8; both ratcheted by .harness-baseline)',
  ],
  deadcode: [cmdDeadcode, 'Detect unused files/exports/deps (knip, via bunx)'],
  arch: [cmdArch, 'Architecture checks (dependency-cruiser)'],
  'arch-config-guard': [cmdArchConfigGuard, 'Block unreviewed arch config changes'],
  'gherkin-guard': [
    cmdGherkinGuard,
    'Block production-source changes with no matching .feature (skips if no .feature files exist)',
  ],
  check: [cmdCheck, 'Full pre-flight: lockfile + fix + typecheck + tests'],
  'pre-commit': [cmdPreCommit, 'Staged checks + tests'],
  'pre-push': [cmdPrePush, 'Read-only push gate: lint, acceptance, arch'],
  ci: [
    cmdCi,
    'Lint + typecheck + audit + complexity + deadcode + acceptance + coverage + crap + arch',
  ],
  'setup-hooks': [cmdHooks, 'Install git pre-commit + pre-push hooks and Claude/Codex Stop wiring'],
  'post-edit': [cmdPostEdit, 'Format if source files changed'],
  'stop-hook': [cmdStopHook, 'Format changed files, then run stop-hook checks'],
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
