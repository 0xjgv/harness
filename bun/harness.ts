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
const ROOT = import.meta.dir;

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
}

interface GateResult {
  description: string;
  cmd: string[];
  ok: boolean;
  exitCode: number;
  output: string;
  detail?: string;
}

/** Run a command with output captured (no printing, no exit): the unit the batch runs. */
async function runCapture(gate: Gate): Promise<GateResult> {
  const proc = Bun.spawn(gate.cmd, { cwd: ROOT, stdout: 'pipe', stderr: 'pipe' });
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
  if (!opts?.noExit) process.exit(result.exitCode);
  return false;
}

async function run(
  description: string,
  cmd: string[],
  opts?: { extract?: (output: string) => string | undefined; noExit?: boolean; stream?: boolean },
): Promise<RunResult> {
  // stream=true inherits stdio for long commands (tests, coverage) so their live
  // output shows instead of being captured — captured silence looks like a hang.
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
  const { readdir, readFile, stat } = await import('node:fs/promises');
  const { isAbsolute, join } = await import('node:path');
  const actualRoots = roots ?? (await qualityTargets());
  const results: Record<string, string[][]> = {};

  async function scanPath(rawPath: string): Promise<void> {
    const full = isAbsolute(rawPath) ? rawPath : join(ROOT, rawPath);
    const info = await stat(full).catch(() => null);
    if (!info) return;
    if (info.isFile()) {
      if (!full.endsWith('.ts')) return;
      const text = await readFile(full, 'utf8').catch(() => null);
      if (text == null) return;
      for (const line of text.split('\n')) {
        for (const m of parseLineForSuppressions(line)) {
          const bucket = results[m.kind] ?? [];
          bucket.push(m.rules);
          results[m.kind] = bucket;
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
    await scanPath(dir);
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
    .filter((line) => line.length > 3 && !line.slice(0, 2).includes('D'))
    .map(porcelainPath)
    .filter((f) => isProjectTsFile(f));
}

// ── Commands ────────────────────────────────────────────────────────

async function cmdFix(files?: string[]): Promise<void> {
  const target = files ?? ['.'];
  await run('Fix & format', ['bunx', 'biome', 'check', '--write', ...target]);
}

function lintGate(files?: string[]): Gate {
  const target = files ?? ['.'];
  return { description: 'Lint & format check', cmd: ['bunx', 'biome', 'check', ...target] };
}

async function cmdLint(files?: string[]): Promise<void> {
  const gate = lintGate(files);
  await run(gate.description, gate.cmd);
}

function typecheckGate(): Gate {
  return { description: 'Typecheck', cmd: ['bunx', 'tsc', '--noEmit'], extract: extractTscSummary };
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
  // Stream: `bun test` is a long command, so live output beats captured silence.
  await run('Tests', ['bun', 'test'], { stream: true });
}

function auditGate(): Gate {
  return { description: 'Dep audit', cmd: ['bun', 'audit'] };
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
  const minArg = process.argv.find((a) => a.startsWith('--min='));
  const minPct = minArg ? Number(minArg.split('=', 2)[1]) : 0;

  await run(
    'Coverage (run)',
    ['bun', 'test', '--coverage', '--coverage-reporter=lcov', '--coverage-dir=coverage'],
    { stream: true },
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
    },
  ];
}

async function cmdArch(): Promise<void> {
  for (const gate of await archGatesOrWarn()) await run(gate.description, gate.cmd);
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
        '15',
        '-a',
        String(COMPLEXITY_MAX_ARGS),
        '-L',
        '100',
        '-i',
        '0',
      ],
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
  return { description: 'Dead code (knip)', cmd: ['bunx', KNIP, '--no-config-hints'] };
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
  // read-only batch: complexity + dead code
  const allOk = await runGatesParallel([...(await complexityGatesOrWarn()), deadcodeGate()]);
  await cmdCrap(); // streaming advisory — after the batch
  if (!allOk) process.exit(1);
}

// ── Stages ──────────────────────────────────────────────────────────

async function checkHooksPresent(): Promise<void> {
  // Warn when required hook scripts are missing (drift detection).
  const { existsSync } = await import('node:fs');
  const required = [
    '.claude/scripts/session-start.sh',
    '.claude/scripts/ups-classify.sh',
    '.claude/scripts/pre-bash-gate.sh',
    '.claude/scripts/pre-edit-gate.sh',
  ];
  const missing = required.filter((p) => !existsSync(`${ROOT}/${p}`));
  if (missing.length > 0) {
    console.log(`  ${RED}⚠${RESET} Missing hook scripts: ${missing.join(', ')}`);
  }
}

async function checkStopHookPresent(): Promise<void> {
  const { existsSync, readFileSync } = await import('node:fs');
  for (const rel of ['.claude/settings.json', '.codex/hooks.json']) {
    const path = `${ROOT}/${rel}`;
    const content = existsSync(path) ? readFileSync(path, 'utf8') : '';
    if (!content.includes('Stop') || !content.includes('stop-hook')) {
      console.log(`  ${RED}⚠${RESET} Missing Stop hook wiring: ${rel}`);
      continue;
    }
    console.log(`  ${GREEN}✓${RESET} Stop hook wiring (${rel})`);
  }
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

  await checkHooksPresent();
  results.push(await checkAgentsMdDrift(true));
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
  await checkAgentsMdDrift();

  if (files.some((f) => isQualityTsFile(f))) {
    await cmdTest();
  }
}

async function cmdCi(): Promise<void> {
  console.log(`\n${BLUE}[ci]${RESET}\n`);
  // Read-only gates run as a parallel batch (captured, printed in submission order,
  // run to completion). Coverage streams and CRAP is advisory — both after the batch.
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
  await cmdCoverage(); // streams; self-skips; after the batch
  await cmdCrap(); // advisory unless --enforce
  if (!allOk) process.exit(1);
}

async function cmdPrePush(): Promise<void> {
  // Read-only push gate: the offline checks pre-commit and stop-hook do not run.
  // pre-commit covers fix/format/typecheck/test on staged files; stop-hook adds
  // complexity. This fills the gap with the deterministic, offline gates none of
  // them run — lint (biome covers format), acceptance, arch — validating the whole
  // pushed tree (after merges/rebases/--no-verify) before it leaves the machine.
  // Network (audit) and advisory (coverage/CRAP) gates stay in ci.
  console.log(`\n${BLUE}[pre-push]${RESET}\n`);
  const gates: Gate[] = [lintGate(), ...(await acceptanceGatesOrWarn()), ...(await archGatesOrWarn())];
  if (!(await runGatesParallel(gates))) process.exit(1);
}

async function cmdHooks(): Promise<void> {
  const hookPath = `${ROOT}/.git/hooks/pre-commit`;
  const hookDir = `${ROOT}/.git/hooks`;

  const { mkdirSync, writeFileSync, chmodSync } = await import('node:fs');
  mkdirSync(hookDir, { recursive: true });
  writeFileSync(hookPath, '#!/bin/sh\nbun harness.ts pre-commit\n');
  chmodSync(hookPath, 0o755);
  console.log('Installed pre-commit hook');
  await checkStopHookPresent();
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
  complexity: [cmdComplexity, 'Cyclomatic complexity gate (lizard, CCN 15, args 8)'],
  deadcode: [cmdDeadcode, 'Detect unused files/exports/deps (knip, via bunx)'],
  arch: [cmdArch, 'Architecture checks (dependency-cruiser)'],
  check: [cmdCheck, 'Full pre-flight: lockfile + fix + typecheck + tests'],
  'pre-commit': [cmdPreCommit, 'Staged checks + tests'],
  'pre-push': [cmdPrePush, 'Read-only push gate: lint, acceptance, arch'],
  ci: [cmdCi, 'Lint + typecheck + audit + complexity + deadcode + acceptance + coverage + crap + arch'],
  'setup-hooks': [cmdHooks, 'Install git pre-commit hook and verify stop-hook wiring'],
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
