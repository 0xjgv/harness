//! Project development tasks. Zero dependencies — std only.
//!
//! Usage: cargo harness <command> [--verbose]

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::env;
use std::fs;
use std::io::{self, IsTerminal, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::time::Instant;

// ── Configuration ───────────────────────────────────────────────────

fn root() -> &'static Path {
    static ROOT: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    ROOT.get_or_init(|| env::current_dir().expect("cannot determine working directory"))
}

fn is_verbose() -> bool {
    static VERBOSE: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *VERBOSE.get_or_init(|| env::args().any(|a| a == "--verbose"))
}

// ── Output ──────────────────────────────────────────────────────────

const GREEN: &str = "\x1b[32m";
const RED: &str = "\x1b[31m";
const BLUE: &str = "\x1b[34m";
const DIM: &str = "\x1b[2m";
const RESET: &str = "\x1b[0m";
const BASELINE_FILE: &str = ".harness-baseline";
const SUPPRESSION_BASELINE_PREFIX: &str = "suppressions.";
const ARCH_CONFIG: &str = "arch.toml";
/// lizard thresholds this template gates on. Shared by the gate and the
/// `--update-baseline` measurement so a recorded floor reproduces exactly.
const COMPLEXITY_MAX_CCN: u32 = 15;
const COMPLEXITY_MAX_ARGS: u32 = 8;
const COMPLEXITY_MAX_LENGTH: u32 = 100;
/// `-i N` high enough that lizard never fails on count: how a gate is turned into
/// a measuring tape, both when measuring a floor and when there is no floor yet.
const REPORT_ONLY_LIMIT: u32 = 1_000_000;
/// The CRAP threshold `crap` defaults to, and the one its floor is measured at.
const CRAP_MAX_DEFAULT: f64 = 30.0;
const ARCH_CONFIG_ALLOW_ENV: &str = "HARNESS_ALLOW_ARCH_CONFIG";
const GHERKIN_ALLOW_ENV: &str = "HARNESS_ALLOW_NO_FEATURE";

// ── Runner ──────────────────────────────────────────────────────────

struct RunResult {
    ok: bool,
    #[allow(dead_code)]
    output: String,
}

#[derive(Default)]
struct RunOpts {
    extract: Option<fn(&str) -> Option<String>>,
    no_exit: bool,
    /// Extra environment variables for the child process.
    env: Vec<(String, String)>,
    /// Stream inherits stdio for commands whose live output is part of the contract.
    stream: bool,
}

fn run(description: &str, cmd: &[&str], opts: Option<&RunOpts>) -> RunResult {
    let verbose = is_verbose();
    let stream = opts.is_some_and(|o| o.stream);

    if verbose || stream {
        println!("  {DIM}\u{2192} {}{RESET}", cmd.join(" "));
    }

    let program = cmd[0];
    let args = &cmd[1..];
    let dir = root();
    let env = opts.map_or(&[][..], |o| o.env.as_slice());

    let build = || {
        let mut c = Command::new(program);
        c.args(args).current_dir(dir);
        for (k, v) in env {
            c.env(k, v);
        }
        c
    };

    if verbose || stream {
        let status = build().status();

        match status {
            Ok(s) if s.success() => {
                println!("  {GREEN}\u{2713}{RESET} {description}");
                return RunResult { ok: true, output: String::new() };
            }
            Ok(s) => {
                println!("  {RED}\u{2717}{RESET} {description}");
                if opts.is_none_or(|o| !o.no_exit) {
                    std::process::exit(s.code().unwrap_or(1));
                }
                return RunResult { ok: false, output: String::new() };
            }
            Err(e) => {
                println!("  {RED}\u{2717}{RESET} {description}");
                eprintln!("  Failed to execute {program}: {e}");
                if opts.is_none_or(|o| !o.no_exit) {
                    std::process::exit(1);
                }
                return RunResult { ok: false, output: String::new() };
            }
        }
    }

    // Non-verbose: capture output
    let result = build().stdout(Stdio::piped()).stderr(Stdio::piped()).output();

    match result {
        Ok(output) => {
            let combined = format!(
                "{}{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr),
            );

            if output.status.success() {
                let detail = opts.and_then(|o| o.extract).and_then(|f| f(&combined));
                let suffix = detail.map_or_else(String::new, |d| format!(" {DIM}({d}){RESET}"));
                println!("  {GREEN}\u{2713}{RESET} {description}{suffix}");
                RunResult { ok: true, output: combined }
            } else {
                println!("  {RED}\u{2717}{RESET} {description}");
                if !combined.is_empty() {
                    print!("{combined}");
                }
                if opts.is_none_or(|o| !o.no_exit) {
                    std::process::exit(output.status.code().unwrap_or(1));
                }
                RunResult { ok: false, output: combined }
            }
        }
        Err(e) => {
            println!("  {RED}\u{2717}{RESET} {description}");
            eprintln!("  Failed to execute {program}: {e}");
            if opts.is_none_or(|o| !o.no_exit) {
                std::process::exit(1);
            }
            RunResult { ok: false, output: String::new() }
        }
    }
}

// ── Parallel gate batch ─────────────────────────────────────────────

/// A read-only gate's label + command, shared by the standalone cmd_* and the batch.
struct Gate {
    /// Owned, not `&'static str`: a ratcheted gate names its floor in its own
    /// label (`Complexity (lizard, baseline 3)`), which is only known at runtime.
    description: String,
    cmd: Vec<String>,
    extract: Option<fn(&str) -> Option<String>>,
    hint: Option<String>,
    /// True when the gate has no recorded floor and therefore cannot fail. Its
    /// hint then prints on the passing line too — "record a floor" is the whole
    /// point of the message, and a gate that never fails would never show it.
    report_only: bool,
}

impl Gate {
    fn new(description: impl Into<String>, cmd: &[&str]) -> Self {
        Self {
            description: description.into(),
            cmd: cmd.iter().map(|&s| s.to_string()).collect(),
            extract: None,
            hint: None,
            report_only: false,
        }
    }

    fn with_hint(mut self, hint: impl Into<String>) -> Self {
        self.hint = Some(hint.into());
        self
    }

    const fn report_only(mut self) -> Self {
        self.report_only = true;
        self
    }
}

struct GateResult {
    description: String,
    cmd: Vec<String>,
    ok: bool,
    exit_code: i32,
    output: String,
    detail: Option<String>,
    hint: Option<String>,
    report_only: bool,
}

/// Run a gate's command with output captured (no printing, no exit): the
/// thread-safe unit the parallel batch spawns. Batch gates never need env vars.
fn run_capture(gate: &Gate) -> GateResult {
    let program = &gate.cmd[0];
    let args: Vec<&str> = gate.cmd[1..].iter().map(String::as_str).collect();
    let result = Command::new(program)
        .args(&args)
        .current_dir(root())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();
    match result {
        Ok(output) => {
            let combined = format!(
                "{}{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr),
            );
            let ok = output.status.success();
            let detail = if ok { gate.extract.and_then(|f| f(&combined)) } else { None };
            GateResult {
                description: gate.description.clone(),
                cmd: gate.cmd.clone(),
                ok,
                exit_code: output.status.code().unwrap_or(1),
                output: combined,
                detail,
                hint: gate.hint.clone(),
                report_only: gate.report_only,
            }
        }
        Err(e) => GateResult {
            description: gate.description.clone(),
            cmd: gate.cmd.clone(),
            ok: false,
            exit_code: 1,
            output: format!("Failed to execute {program}: {e}"),
            detail: None,
            hint: gate.hint.clone(),
            report_only: gate.report_only,
        },
    }
}

/// Print a gate's ✓/✗ line (with the failure body); exit on failure unless `no_exit`.
fn print_gate_result(result: &GateResult, no_exit: bool) -> bool {
    if is_verbose() {
        println!("  {DIM}\u{2192} {}{RESET}", result.cmd.join(" "));
        if !result.output.is_empty() {
            print!("{}", result.output);
        }
    }
    if result.ok {
        let suffix =
            result.detail.as_ref().map_or_else(String::new, |d| format!(" {DIM}({d}){RESET}"));
        println!("  {GREEN}\u{2713}{RESET} {}{suffix}", result.description);
        if let Some(hint) = result.report_only.then_some(result.hint.as_ref()).flatten() {
            println!("  ↳ fix: {hint}");
        }
        return true;
    }
    println!("  {RED}\u{2717}{RESET} {}", result.description);
    if !is_verbose() && !result.output.is_empty() {
        print!("{}", result.output);
    }
    if let Some(hint) = &result.hint {
        println!("  ↳ fix: {hint}");
    }
    if !no_exit {
        std::process::exit(result.exit_code);
    }
    false
}

/// Run read-only gates concurrently, then print each result in submission order.
///
/// Returns true when every gate passed. Unlike the fail-fast standalone gates, this
/// runs all gates to completion so one pass surfaces every failure; the caller exits
/// non-zero afterward. Each gate captures on its own scoped thread; results collect
/// into a Vec by submission order (not as they finish) so a parallel run reads the
/// same every time — matching the monorepo Makefile's buffered, deterministic dump.
/// Run gates concurrently, print each result (submission order), and return
/// the descriptions of any that failed — empty means every gate passed.
/// Split out from `run_gates_parallel` so `cmd_stop_hook` can name failed
/// gates in its stderr summary without duplicating the execution/printing.
fn run_gates_parallel_detailed(gates: &[Gate]) -> Vec<String> {
    if gates.is_empty() {
        return Vec::new();
    }
    let results: Vec<GateResult> = std::thread::scope(|scope| {
        // Spawn every gate first (collect handles), then join — so the gates run
        // concurrently rather than spawn-then-immediately-join one at a time.
        let mut handles = Vec::with_capacity(gates.len());
        for gate in gates {
            handles.push(scope.spawn(move || run_capture(gate)));
        }
        handles.into_iter().map(|handle| handle.join().expect("gate thread panicked")).collect()
    });
    let mut failed = Vec::new();
    for result in &results {
        if !print_gate_result(result, true) {
            failed.push(result.description.clone());
        }
    }
    failed
}

fn run_gates_parallel(gates: &[Gate]) -> bool {
    run_gates_parallel_detailed(gates).is_empty()
}

/// Run gates one at a time, in submission order, printing each result as it
/// finishes; never short-circuits on failure. Used for gates that invoke
/// `cargo build`/`cargo test`/etc.: each takes cargo's exclusive lock on
/// `target/`, so handing them to `run_gates_parallel` does not parallelize
/// anything — every builder queues behind whichever one holds the lock — it
/// just interleaves captured output with "Blocking waiting for file lock on
/// build directory" noise. Sequential keeps the same "run everything to
/// completion, then report" semantics as the parallel batch, honestly.
fn run_gates_sequential(gates: &[Gate]) -> bool {
    let mut all_ok = true;
    for gate in gates {
        if !print_gate_result(&run_capture(gate), true) {
            all_ok = false;
        }
    }
    all_ok
}

// ── Extractors ──────────────────────────────────────────────────────

fn extract_test_summary(output: &str) -> Option<String> {
    // cargo test runs multiple binaries, each producing a "test result:" line.
    // Aggregate passed counts and take the max duration.
    let mut total_passed: u32 = 0;
    let mut max_duration = 0.0_f64;
    let mut found = false;

    for line in output.lines().filter(|l| l.contains("test result:")) {
        found = true;
        if let Some(p) = extract_between(line, "ok. ", " passed")
            .or_else(|| extract_between(line, "FAILED. ", " passed"))
        {
            total_passed += p.parse::<u32>().unwrap_or(0);
        }
        if let Some(d) = extract_after(line, "finished in ") {
            let d = d.trim().trim_end_matches('s');
            if let Ok(secs) = d.parse::<f64>() {
                max_duration = max_duration.max(secs);
            }
        }
    }

    if found { Some(format!("{total_passed} passed, {max_duration:.2}s")) } else { None }
}

fn extract_between<'a>(s: &'a str, start: &str, end: &str) -> Option<&'a str> {
    let start_idx = s.find(start)? + start.len();
    let end_idx = s[start_idx..].find(end)? + start_idx;
    Some(&s[start_idx..end_idx])
}

fn extract_after<'a>(s: &'a str, marker: &str) -> Option<&'a str> {
    let idx = s.find(marker)? + marker.len();
    Some(&s[idx..])
}

// ── Suppressions ────────────────────────────────────────────────────

const SUPPRESSION_PREFIXES: &[(&str, &str)] =
    &[("allow", "#[allow("), ("allow_crate", "#![allow(")];

type SuppressionCounts = BTreeMap<String, Vec<Vec<String>>>;

#[derive(Debug, Clone, PartialEq, Eq)]
struct SuppressionFinding {
    kind: String,
    rules: Vec<String>,
    location: String,
}

fn parse_line_for_suppressions(line: &str) -> Vec<(String, Vec<String>)> {
    let mut out = Vec::new();
    for (kind, prefix) in SUPPRESSION_PREFIXES {
        let mut rest = line;
        while let Some(idx) = rest.find(prefix) {
            let after = &rest[idx + prefix.len()..];
            let Some(end) = after.find(')') else { break };
            let rules: Vec<String> = after[..end]
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            out.push(((*kind).to_string(), rules));
            rest = &after[end + 1..];
        }
    }
    out
}

fn scan_rs_file(path: &Path, findings: &mut Vec<SuppressionFinding>) {
    if path.extension().is_none_or(|e| e != "rs") {
        return;
    }
    let Ok(text) = fs::read_to_string(path) else {
        return;
    };
    for (index, line) in text.lines().enumerate() {
        for (kind, rules) in parse_line_for_suppressions(line) {
            findings.push(SuppressionFinding {
                kind,
                rules,
                location: format!("{}:{}", path.display(), index + 1),
            });
        }
    }
}

fn scan_suppression_findings(roots: &[PathBuf]) -> Vec<SuppressionFinding> {
    let mut findings = Vec::new();
    for root_path in roots {
        if root_path.is_file() {
            scan_rs_file(root_path, &mut findings);
            continue;
        }
        let mut stack = vec![root_path.clone()];
        while let Some(p) = stack.pop() {
            let Ok(entries) = fs::read_dir(&p) else {
                continue;
            };
            for entry in entries.flatten() {
                let path = entry.path();
                let Ok(ft) = entry.file_type() else { continue };
                if ft.is_dir() {
                    stack.push(path);
                } else {
                    scan_rs_file(&path, &mut findings);
                }
            }
        }
    }
    findings
}

fn bucket_suppressions(findings: &[SuppressionFinding]) -> SuppressionCounts {
    let mut results: SuppressionCounts = BTreeMap::new();
    for finding in findings {
        results.entry(finding.kind.clone()).or_default().push(finding.rules.clone());
    }
    results
}

#[cfg(test)]
fn scan_suppressions(roots: &[PathBuf]) -> SuppressionCounts {
    bucket_suppressions(&scan_suppression_findings(roots))
}

fn default_suppression_roots() -> Vec<PathBuf> {
    vec![root().join("src"), root().join("tests"), root().join("harness.rs")]
}

fn suppression_counts(results: &SuppressionCounts) -> BaselineMap {
    results
        .iter()
        .map(|(kind, entries)| {
            (
                format!("{SUPPRESSION_BASELINE_PREFIX}{kind}"),
                u32::try_from(entries.len()).unwrap_or(u32::MAX),
            )
        })
        .collect()
}

/// The ratcheted floors `.harness-baseline` carries, keyed by metric name.
type BaselineMap = BTreeMap<String, u32>;

/// How one ratcheted metric is measured. Named so `RATCHETED_KEYS` stays readable.
type MeasureFn = fn() -> Measurement;

/// A merged baseline plus the "dropped this key" warnings, or the metrics that
/// failed to measure as (key, reason) pairs.
type MergeOutcome = Result<(BaselineMap, Vec<String>), Vec<(String, String)>>;

fn parse_baseline_str(text: &str) -> BaselineMap {
    let mut values = BaselineMap::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let mut parts = trimmed.split_whitespace();
        let (Some(key), Some(value), None) = (parts.next(), parts.next(), parts.next()) else {
            continue;
        };
        let Ok(parsed) = value.parse::<u32>() else { continue };
        values.insert(key.to_string(), parsed);
    }
    values
}

fn read_baseline() -> Option<BaselineMap> {
    let text = fs::read_to_string(root().join(BASELINE_FILE)).ok()?;
    Some(parse_baseline_str(&text))
}

/// Parse a `--min=N` value into a coverage threshold, or an error message to
/// print before exiting 1. Extracted so the invalid-value path is testable
/// without calling `std::process::exit` from a test — mirrors python's
/// `_coverage_min_default` try/except and bun's `parseMinArg`.
fn parse_coverage_min(raw: &str) -> Result<u32, String> {
    raw.parse::<u32>().map_err(|_| format!("Coverage: --min={raw} is not an integer"))
}

fn coverage_min_default() -> u32 {
    if let Some(raw) = arg_value("--min") {
        return match parse_coverage_min(&raw) {
            Ok(value) => value,
            Err(msg) => {
                println!("  {RED}\u{2717}{RESET} {msg}");
                std::process::exit(1);
            }
        };
    }
    read_baseline().and_then(|b| b.get("coverage.min").copied()).unwrap_or(0)
}

/// The committed floor for a ratcheted metric, or `None` when there is none.
///
/// `None` means "never measured here" — no `.harness-baseline` at all, or a file
/// that does not carry this key. A gate reading a floor then runs report-only:
/// retrofitting the harness into an existing repo has to be green on day one, and
/// a floor of 0 inferred from a missing number is not a floor, it is a demand that
/// the repo already be perfect.
fn baseline_floor(key: &str) -> Option<u32> {
    read_baseline()?.get(key).copied()
}

/// A metric's value, or the reason there isn't one.
///
/// Three states, deliberately not collapsed into `Option<u32>`:
///   * `Value` — measured, including a legitimate 0.
///   * `Unavailable` — the metric does not apply to this repo (no sources, tool not
///     installed). The baseline key is dropped, and the gate goes report-only.
///   * `Error` — the measuring tool ran and failed. `--update-baseline` aborts and
///     writes nothing: a floor recorded from a broken run is worse than no floor,
///     because every downstream gate trusts it.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Measurement {
    Value(u32),
    Unavailable(String),
    Error(String),
}

/// Every metric `suppressions --update-baseline` measures, paired with how to
/// measure it. Add a line here to ratchet a new metric: the writer, the
/// abort-on-error rule and the drop-when-unavailable rule are all generic over
/// this table.
///
/// `coverage.min` is measured first so the instrumented run it needs is the one
/// CRAP then reuses. `mutation.min` is deliberately absent: it is carried through
/// untouched (see `merge_baseline`), because measuring it costs a mutation run the
/// automatic pass must not silently pay for.
const RATCHETED_KEYS: &[(&str, MeasureFn)] = &[
    ("coverage.min", measured_coverage_min),
    ("complexity.max_violations", measured_complexity_violations),
    ("crap.max_violations", measured_crap_violations),
    ("arch.max_violations", measured_arch_violations),
];

/// Measure every ratcheted metric, stopping at the first one that failed.
///
/// Sequential and short-circuiting on purpose: a broken tool usually breaks the
/// metrics after it too (CRAP re-runs the coverage suite), so the first failure is
/// the one worth reporting and the later ones would only be noise.
fn measure_ratcheted() -> Vec<(&'static str, Measurement)> {
    let mut measured = Vec::with_capacity(RATCHETED_KEYS.len());
    for (key, measure) in RATCHETED_KEYS {
        let value = measure();
        let failed = matches!(value, Measurement::Error(_));
        measured.push((*key, value));
        if failed {
            break;
        }
    }
    measured
}

/// Merge measured floors over the existing baseline; unknown keys are preserved.
///
/// Every key the harness measures is rewritten (so a metric that improved ratchets
/// down); every key it does not recognise — `coverage.min`, `mutation.min`, a key a
/// newer harness wrote — is carried through untouched.
///
/// A metric that does not apply here has its key *removed*, never carried forward:
/// the shipped template's own numbers must not survive into an adopting repo's
/// first baseline. A metric that could not be measured aborts the whole write, so
/// this returns `Err` before building anything — see `Measurement`.
///
/// Pure on purpose: `write_baseline` owns the I/O and the printing, this owns the
/// merge rules, and the rules are unit-tested without touching the filesystem.
fn merge_baseline(
    existing: &BaselineMap,
    suppressions: &BaselineMap,
    measurements: &[(&str, Measurement)],
) -> MergeOutcome {
    let broken: Vec<(String, String)> = measurements
        .iter()
        .filter_map(|(key, measurement)| match measurement {
            Measurement::Error(reason) => Some(((*key).to_string(), reason.clone())),
            Measurement::Value(_) | Measurement::Unavailable(_) => None,
        })
        .collect();
    if !broken.is_empty() {
        return Err(broken);
    }

    let mut merged = existing.clone();
    // Seed every known suppression kind at 0 before applying the scan: a kind that
    // ratcheted to zero stops appearing in the scan entirely, and without the seed
    // its stale count would survive as an unrecognised key, tolerating suppressions
    // that are already gone.
    for (kind, _) in SUPPRESSION_PREFIXES {
        merged.insert(format!("{SUPPRESSION_BASELINE_PREFIX}{kind}"), 0);
    }
    for (key, count) in suppressions {
        merged.insert(key.clone(), *count);
    }

    let mut dropped = Vec::new();
    for (key, measurement) in measurements {
        match measurement {
            Measurement::Value(value) => {
                merged.insert((*key).to_string(), *value);
            }
            Measurement::Unavailable(reason) => {
                if merged.remove(*key).is_some() {
                    dropped.push(format!("{key}: dropped — {reason}"));
                }
            }
            // Filtered and returned above; reaching here would mean the two
            // passes disagree about what an error is.
            Measurement::Error(_) => unreachable!("measurement errors short-circuit above"),
        }
    }
    Ok((merged, dropped))
}

/// Render a baseline map as the `key value` lines the file stores, sorted.
fn serialize_baseline(baseline: &BaselineMap) -> String {
    let lines: Vec<String> = baseline.iter().map(|(key, value)| format!("{key} {value}")).collect();
    if lines.is_empty() { String::new() } else { format!("{}\n", lines.join("\n")) }
}

/// Measure, merge and write `.harness-baseline`.
///
/// All-or-nothing: when any ratcheted metric fails to measure, nothing is written
/// and the process exits 1. The write is the last statement for exactly that
/// reason — an abort can never leave a half-updated floor behind.
fn write_baseline(results: &SuppressionCounts) -> std::io::Result<()> {
    let existing = read_baseline().unwrap_or_default();
    let measurements = measure_ratcheted();
    let (merged, dropped) =
        match merge_baseline(&existing, &suppression_counts(results), &measurements) {
            Ok(outcome) => outcome,
            Err(broken) => {
                println!(
                    "  {RED}\u{2717}{RESET} {BASELINE_FILE} not written \u{2014} could not measure:"
                );
                for (key, reason) in &broken {
                    println!("    {key}: {reason}");
                }
                println!(
                    "  ↳ fix: make the measurement pass, then rerun \
                     `cargo harness suppressions --update-baseline`"
                );
                std::process::exit(1);
            }
        };
    for line in &dropped {
        println!("  {GREEN}\u{26a0}{RESET} {line}");
    }
    fs::write(root().join(BASELINE_FILE), serialize_baseline(&merged))
}

fn print_suppressions_breakdown(results: &SuppressionCounts) {
    let total: usize = results.values().map(Vec::len).sum();
    println!("\n=== Suppressions ===\n");
    println!("Suppressions: {total} total");
    if total == 0 {
        return;
    }
    for (kind, entries) in results {
        println!("  {}: {}", kind, entries.len());
        let mut rule_counts: HashMap<String, u32> = HashMap::new();
        for rules in entries {
            for r in rules {
                *rule_counts.entry(r.clone()).or_insert(0) += 1;
            }
        }
        let mut sorted: Vec<(String, u32)> = rule_counts.into_iter().collect();
        sorted.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        for (rule, count) in sorted.into_iter().take(10) {
            println!("    {rule}: {count}");
        }
    }
}

fn check_suppressions_baseline(no_exit: bool) -> bool {
    let findings = scan_suppression_findings(&default_suppression_roots());
    let results = bucket_suppressions(&findings);
    let current = suppression_counts(&results);
    let Some(baseline) = read_baseline() else {
        print_suppressions_breakdown(&results);
        println!("  {GREEN}\u{26a0}{RESET} Suppressions are report-only: no {BASELINE_FILE} found");
        println!("  ↳ fix: run `cargo harness suppressions --update-baseline` to start ratcheting");
        return true;
    };

    let total: u32 = current.values().sum();
    let baseline_total: u32 = baseline
        .iter()
        .filter(|(key, _)| key.starts_with(SUPPRESSION_BASELINE_PREFIX))
        .map(|(_, count)| count)
        .sum();
    let grown: Vec<(&String, &u32)> = current
        .iter()
        .filter(|(key, count)| **count > baseline.get(*key).copied().unwrap_or(0))
        .collect();
    if grown.is_empty() {
        let suffix = if total < baseline_total {
            " — run `cargo harness suppressions --update-baseline` to ratchet down"
        } else {
            ""
        };
        println!(
            "  {GREEN}\u{2713}{RESET} Suppressions: {total} (baseline {baseline_total}){suffix}"
        );
        return true;
    }

    let mut locations: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for finding in &findings {
        locations.entry(&finding.kind).or_default().push(&finding.location);
    }
    println!("  {RED}\u{2717}{RESET} Suppressions grew: {total} (baseline {baseline_total})");
    for (key, count) in grown {
        let kind = key.trim_start_matches(SUPPRESSION_BASELINE_PREFIX);
        println!("    {kind}: {count} > {}", baseline.get(key).copied().unwrap_or(0));
        if let Some(kind_locations) = locations.get(kind) {
            for location in kind_locations.iter().take(10) {
                println!("      {location}");
            }
        }
    }
    println!(
        "  ↳ fix: fix it, or with human sign-off: `cargo harness suppressions --update-baseline`"
    );
    if !no_exit {
        std::process::exit(1);
    }
    false
}

fn cmd_suppressions() {
    let findings = scan_suppression_findings(&default_suppression_roots());
    let results = bucket_suppressions(&findings);
    if arg_flag("--update-baseline") {
        if let Err(e) = write_baseline(&results) {
            println!("  {RED}\u{2717}{RESET} {BASELINE_FILE}: {e}");
            std::process::exit(1);
        }
        let total: usize = results.values().map(Vec::len).sum();
        println!("  {GREEN}\u{2713}{RESET} {BASELINE_FILE}: suppressions baseline set to {total}");
        return;
    }
    print_suppressions_breakdown(&results);
    if !check_suppressions_baseline(true) {
        std::process::exit(1);
    }
}

// ── Git helpers ─────────────────────────────────────────────────────

fn staged_rs_files() -> Vec<String> {
    let output = Command::new("git")
        .args(["diff", "--cached", "--name-only", "--diff-filter=d", "--relative"])
        .current_dir(root())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();

    let Ok(output) = output else {
        return Vec::new();
    };

    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter(|f| {
            !f.is_empty()
                && Path::new(f).extension().is_some_and(|ext| ext.eq_ignore_ascii_case("rs"))
        })
        .map(String::from)
        .collect()
}

/// Parse one `git status --porcelain` line into the path it refers to, or
/// `None` for malformed/too-short lines and deletions (`D` in either status
/// column — matching python/bun, which also skip deletes here). Resolves
/// rename lines (`R  old.rs -> new.rs`) to the new path.
///
/// Status columns are always exactly one ASCII byte each (space or a
/// letter), so byte index 3 is always a valid char boundary once `line`
/// clears the length check — but `line.get(3..)` (rather than `&line[3..]`)
/// makes that safe by construction instead of by argument, so a multibyte
/// path can never panic here even if that invariant ever changes upstream.
fn parse_porcelain_line(line: &str) -> Option<&str> {
    let bytes = line.as_bytes();
    if bytes.len() < 4 {
        return None;
    }
    if bytes[0] == b'D' || bytes[1] == b'D' {
        return None;
    }
    let path = line.get(3..)?;
    Some(path.find(" -> ").map_or(path, |idx| &path[idx + 4..]))
}

/// Convert `git status --porcelain` output into changed `.rs` paths relative
/// to the current template's subtree. `prefix` (from `git_prefix()`) is the
/// repo-root-relative path of `root()`; porcelain paths are always
/// repo-root-relative regardless of cwd, so a path outside `prefix`'s
/// subtree belongs to a sibling template (or another crate in a monorepo)
/// and must be dropped rather than normalized — normalizing it would strip
/// nothing and let it pass the extension filter unchanged.
fn parse_changed_rs_files(porcelain: &str, prefix: &str) -> Vec<String> {
    porcelain
        .lines()
        .filter_map(parse_porcelain_line)
        .filter(|p| prefix.is_empty() || p.starts_with(&format!("{prefix}/")))
        .map(|p| normalize_changed_path(p, prefix))
        .filter(|f| Path::new(f).extension().is_some_and(|ext| ext.eq_ignore_ascii_case("rs")))
        .collect()
}

fn changed_rs_files() -> Vec<String> {
    // `-- .` scopes the porcelain output to this template's subtree, same
    // idiom the arch-config-guard helpers below already use — without it, a
    // sibling template's change (or another crate in a monorepo) reads as
    // "this template changed" and triggers a repo-wide reformat.
    let output = Command::new("git")
        .args(["status", "--porcelain", "--", "."])
        .current_dir(root())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();

    let Ok(output) = output else {
        return Vec::new();
    };

    parse_changed_rs_files(&String::from_utf8_lossy(&output.stdout), &git_prefix())
}

// ── Commands ────────────────────────────────────────────────────────

fn cmd_fix() {
    // clippy --fix requires --allow-dirty --allow-staged for uncommitted changes
    run("Clippy fix", &["cargo", "clippy", "--fix", "--allow-dirty", "--allow-staged"], None);
    run("Format", &["cargo", "fmt"], None);
}

fn cmd_lint() {
    run("Clippy", &["cargo", "clippy"], None);
    run("Format check", &["cargo", "fmt", "--check"], None);
}

/// The strict clippy gate used by ci/pre-push: warnings are errors. (The dev-facing
/// `cmd_lint` stays lenient so a warning does not block an in-progress edit loop.)
///
/// `--locked` doubles as the lockfile-freshness gate ci/pre-push otherwise
/// lack (mirroring bun's `bun install --frozen-lockfile`): it fails instead
/// of silently rewriting `Cargo.lock` when a dependency edit left it stale,
/// without adding a separate command just for that one check.
fn lint_gate() -> Gate {
    Gate::new("Clippy (strict)", &["cargo", "clippy", "--locked", "--", "-D", "warnings"])
        .with_hint("run `cargo harness fix`")
}

fn format_check_gate() -> Gate {
    Gate::new("Format check", &["cargo", "fmt", "--check"]).with_hint("run `cargo fmt`")
}

fn cmd_test() {
    run("Tests", &["cargo", "test"], None);
}

fn cmd_audit() {
    cmd_audit_inner(false);
}

/// Run cargo-audit; returns whether the audit passed. cargo-audit requires separate
/// installation: in strict mode (ci) a missing tool is a failure, otherwise it is a
/// non-blocking skip. Strict callers run with `no_exit` so a vuln folds into the batch
/// result instead of short-circuiting the rest of ci.
fn cmd_audit_inner(strict: bool) -> bool {
    let installed = Command::new("cargo")
        .args(["audit", "--version"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .status()
        .is_ok_and(|s| s.success());

    if installed {
        let result = run(
            "Dep audit",
            &["cargo", "audit"],
            Some(&RunOpts { no_exit: strict, ..RunOpts::default() }),
        )
        .ok;
        if !result {
            println!("  ↳ fix: bump the vulnerable dependency or escalate");
        }
        return result;
    }
    if strict {
        println!("  {RED}\u{2717}{RESET} Dep audit (cargo-audit not installed)");
        return false;
    }
    println!("  {DIM}\u{2298} Dep audit skipped (install: cargo install cargo-audit){RESET}");
    true
}

fn cmd_post_edit() {
    if changed_rs_files().is_empty() {
        return;
    }
    run("Format", &["cargo", "fmt"], Some(&RunOpts { no_exit: true, ..RunOpts::default() }));
}

/// Claude Code's Stop hook treats exit code 2 as blocking and feeds stderr
/// back to the model; any other non-zero exit is a non-blocking error the
/// model never sees. (Codex's wrapper, `.codex/hooks/codex-stop-hook.sh`,
/// turns any non-zero exit into a JSON block regardless, so this only
/// matters for Claude.) So on failure this keeps the usual per-gate detail
/// on stdout, then writes a short summary of which gates failed to stderr
/// and exits 2 — the one command in this file where the exit code carries
/// meaning beyond "ok or not".
fn cmd_stop_hook() {
    println!("\n=== Stop Hook Checks ===\n");
    cmd_post_edit(); // mutating — sequential, first
    check_arch_config_guard(true, false, false); // warn-only in stop-hook, never fails it
    check_gherkin_guard(true, false, false); // warn-only in stop-hook, never fails it
    let failed = run_gates_parallel_detailed(&[complexity_gate()]); // read-only batch
    if !failed.is_empty() {
        eprintln!("Stop hook failed: {}", failed.join(", "));
        std::process::exit(2);
    }
}

/// Run Gherkin/BDD acceptance scenarios via cucumber.
///
/// The `acceptance` integration test (Cargo.toml `[[test]]`, `harness = false`)
/// executes every `.feature` file under `tests/features/`. An empty features
/// directory is not a failure — it warns and exits 0, mirroring python's
/// `cmd_acceptance`, so adopting the template never blocks on missing scenarios.
fn acceptance_gates_or_warn() -> Vec<Gate> {
    let features_dir = root().join("tests").join("features");
    if !has_feature_files(&features_dir) {
        println!(
            "  {GREEN}\u{26a0}{RESET} Acceptance: no .feature files in \
             tests/features/ (add one to enable this gate)"
        );
        return Vec::new();
    }
    vec![
        Gate::new("Acceptance (cucumber)", &["cargo", "test", "--test", "acceptance", "--quiet"])
            .with_hint("align implementation with the `.feature`, not vice versa"),
    ]
}

fn cmd_acceptance() {
    for gate in acceptance_gates_or_warn() {
        print_gate_result(&run_capture(&gate), false);
    }
}

/// True when `dir` contains at least one `.feature` file (recursively).
fn has_feature_files(dir: &Path) -> bool {
    let mut stack = vec![dir.to_path_buf()];
    while let Some(p) = stack.pop() {
        let Ok(entries) = fs::read_dir(&p) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            match entry.file_type() {
                Ok(ft) if ft.is_dir() => stack.push(path),
                Ok(_) if path.extension().is_some_and(|e| e == "feature") => return true,
                _ => {}
            }
        }
    }
    false
}

/// Run tests under llvm-cov with a line-coverage threshold (`--min=N`, default 0).
///
/// Thresholds start at 0 so adopting the template never fails an existing
/// project — ratchet up as the suite matures. Requires cargo-llvm-cov:
/// `cargo install cargo-llvm-cov`. Absent → warn + skip (advisory-friendly),
/// matching the audit gate's install-aware behavior.
///
/// Runs the test suite ONCE under llvm-cov (`--no-report`), then renders two
/// reports from the cached profdata: an LCOV file (consumed by `cmd_crap` to
/// avoid a second test run) and a console summary with the threshold check.
fn cmd_coverage() {
    let min_pct = coverage_min_default();

    if !tool_installed("llvm-cov") {
        println!("  {DIM}\u{2298} Coverage skipped (install: cargo install cargo-llvm-cov){RESET}");
        return;
    }

    // cargo-llvm-cov needs llvm-cov/llvm-profdata. rustup ships them via the
    // `llvm-tools-preview` component; toolchains installed another way (e.g.
    // Homebrew) may not. When they are absent, fall back to a system LLVM
    // install via the documented LLVM_COV / LLVM_PROFDATA env vars.
    let env = llvm_tools_env();
    let lcov_path = root().join("target").join("llvm-cov").join("lcov.info");
    if let Some(parent) = lcov_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let lcov_str = lcov_path.to_string_lossy().into_owned();
    let threshold = format!("{min_pct}");

    run(
        "Coverage (run)",
        &["cargo", "llvm-cov", "--no-report"],
        Some(&RunOpts { env: env.clone(), ..RunOpts::default() }),
    );
    run(
        "Coverage: LCOV report",
        &["cargo", "llvm-cov", "report", "--lcov", "--output-path", &lcov_str],
        Some(&RunOpts { env: env.clone(), ..RunOpts::default() }),
    );
    run(
        &format!("Coverage >= {min_pct}%"),
        &["cargo", "llvm-cov", "report", "--summary-only", "--fail-under-lines", &threshold],
        Some(&RunOpts { env, ..RunOpts::default() }),
    );
}

/// Locate a system LLVM for cargo-llvm-cov when the rustup component is absent.
///
/// Returns `LLVM_COV` / `LLVM_PROFDATA` pairs to pass to the child process, or
/// an empty vec when the rustup `llvm-tools` are present (cargo-llvm-cov finds
/// them itself) or no system LLVM is found.
fn llvm_tools_env() -> Vec<(String, String)> {
    // rustup install: the tools sit in the toolchain sysroot.
    if let Ok(out) = Command::new("rustc").arg("--print").arg("sysroot").output() {
        let sysroot = String::from_utf8_lossy(&out.stdout).trim().to_string();
        let host = Command::new("rustc")
            .arg("-vV")
            .output()
            .ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
            .and_then(|s| s.lines().find_map(|l| l.strip_prefix("host: ").map(str::to_string)));
        if let Some(host) = host {
            let bin = Path::new(&sysroot).join("lib/rustlib").join(&host).join("bin");
            if bin.join("llvm-cov").exists() {
                return Vec::new(); // rustup component present.
            }
        }
    }
    // System LLVM fallback (Homebrew, Linux package managers).
    for prefix in ["/opt/homebrew/opt/llvm/bin", "/usr/local/opt/llvm/bin", "/usr/bin"] {
        let cov = Path::new(prefix).join("llvm-cov");
        let profdata = Path::new(prefix).join("llvm-profdata");
        if cov.exists() && profdata.exists() {
            return vec![
                ("LLVM_COV".to_string(), cov.to_string_lossy().into_owned()),
                ("LLVM_PROFDATA".to_string(), profdata.to_string_lossy().into_owned()),
            ];
        }
    }
    Vec::new()
}

/// Run cargo-mutants. Advisory — NOT wired into `ci`.
///
/// Mutation testing injects small bugs and checks whether the test suite
/// catches them. It is slow and noisy by nature, so it stays an explicit
/// opt-in rather than a blocking gate. Absent → warn + skip.
fn cmd_mutation() {
    if !tool_installed("mutants") {
        println!("  {DIM}\u{2298} Mutation skipped (install: cargo install cargo-mutants){RESET}");
        return;
    }
    run(
        "Mutation (cargo-mutants)",
        &["cargo", "mutants", "--no-shuffle"],
        Some(&RunOpts { no_exit: true, ..RunOpts::default() }),
    );
}

/// The two cargo-modules passes that make up the arch gate.
///
/// Kept as constants because the gate, the baseline measurement and the
/// `--verbose` echo must all name the same commands: a floor measured from a
/// different invocation does not reproduce.
const ARCH_CYCLES_CMD: &[&str] =
    &["cargo", "modules", "dependencies", "--lib", "--no-externs", "--acyclic"];
const ARCH_ORPHANS_CMD: &[&str] = &["cargo", "modules", "orphans", "--lib"];

const ARCH_HINT: &str =
    "boundary crossed; surface the design decision to the human; don't edit arch config";

/// The orphan count cargo-modules reports, or `None` when its output does not say.
///
/// cargo-modules prints a plain `N orphans found:` header before the per-orphan
/// blocks (the blocks themselves are ANSI-colored, the header is not), and a
/// colored `No orphans found.` line when there are none — matched by substring
/// because the phrase itself is contiguous between the escape sequences.
fn parse_orphan_count(stdout: &str) -> Option<u32> {
    let counted = stdout
        .lines()
        .find_map(|line| line.trim().strip_suffix(" orphans found:")?.parse::<u32>().ok());
    counted.or_else(|| stdout.contains("No orphans found.").then_some(0))
}

/// Architectural violations as one number: `orphan_count + cycle_flag`.
///
/// cargo-modules 0.26.0 exposes no JSON and no aggregate count — `dependencies
/// --acyclic` is pass/fail and `orphans` only adds `--deny` — so the count is
/// *defined* here, and this definition is what the `arch.max_violations` floor
/// ratchets:
///
/// * `cycle_flag` is 1 when `ARCH_CYCLES_CMD` exits non-zero. A cycle exists but
///   the tool never says how many, so the whole condition counts once.
/// * `orphan_count` is the `N orphans found:` header. When that line is missing
///   and the command still failed, the run counts as 1 rather than 0: an output
///   format change then degrades to "something is wrong" instead of a silently
///   green gate.
///
/// The dot graph is deliberately not parsed — it renders the module tree, not the
/// violations, and reconstructing cycles from it would put a graph algorithm in a
/// task runner that must stay dependency-free.
///
/// Pure so the arithmetic is unit-tested without invoking cargo.
fn arch_violation_count(cycles_ok: bool, orphans_ok: bool, orphans_stdout: &str) -> u32 {
    let cycle_flag = u32::from(!cycles_ok);
    let orphans = parse_orphan_count(orphans_stdout).unwrap_or_else(|| u32::from(!orphans_ok));
    cycle_flag + orphans
}

/// Run both cargo-modules passes: the violation count, plus the output to show
/// when the gate fails (only the passes that actually failed contribute).
fn arch_scan() -> (u32, String) {
    let cycles = run_capture(&Gate::new("Arch: no module cycles", ARCH_CYCLES_CMD));
    let orphans = run_capture(&Gate::new("Arch: no orphan files", ARCH_ORPHANS_CMD));
    let count = arch_violation_count(cycles.ok, orphans.ok, &orphans.output);
    let mut detail = String::new();
    for failed in [&cycles, &orphans].into_iter().filter(|result| !result.ok) {
        detail.push_str(&failed.output);
    }
    (count, detail)
}

/// The arch gate's label: names the floor it is held to, or says there is none.
fn arch_description(floor: Option<u32>, count: u32) -> String {
    match floor {
        None => format!("Arch (cargo-modules, report-only: no {BASELINE_FILE} floor)"),
        Some(floor) if count > floor => {
            format!("Arch (cargo-modules): {count} violations exceed baseline {floor}")
        }
        Some(0) => "Arch (cargo-modules)".to_string(),
        Some(floor) => format!("Arch (cargo-modules, baseline {floor})"),
    }
}

/// Check architecture via cargo-modules against `arch.toml`; `false` when the
/// violation count is over the recorded floor.
///
/// Rust's compiler enforces visibility and crate layering but NOT freedom from
/// circular dependencies between modules of one crate, nor the absence of
/// orphaned (unlinked) source files. Those are the invariants this gate counts —
/// see `arch_violation_count` for the definition. `arch.toml` is guarded by
/// `arch-config-guard`, which blocks integration until the change is reviewed.
/// Absent config, or cargo-modules not installed → skip.
///
/// With no `arch.max_violations` floor recorded the gate is report-only: it
/// prints the count and passes. A legacy crate full of orphans has to be green on
/// day one, and a floor of 0 inferred from a missing number is not a floor.
fn arch_check(no_exit: bool) -> bool {
    if !root().join("arch.toml").exists() {
        println!("  {GREEN}\u{26a0}{RESET} Arch: no arch.toml \u{2014} skipped");
        return true;
    }
    if !tool_installed("modules") {
        println!("  {DIM}\u{2298} Arch skipped (install: cargo install cargo-modules){RESET}");
        return true;
    }
    let floor = baseline_floor("arch.max_violations");
    let (count, output) = arch_scan();
    let ok = floor.is_none_or(|floor| count <= floor);
    let hint = if floor.is_none() {
        "run `cargo harness suppressions --update-baseline` to record a floor".to_string()
    } else {
        ARCH_HINT.to_string()
    };
    let result = GateResult {
        description: arch_description(floor, count),
        cmd: vec![ARCH_CYCLES_CMD.join(" "), "&&".to_string(), ARCH_ORPHANS_CMD.join(" ")],
        ok,
        exit_code: 1,
        output,
        detail: (count > 0).then(|| format!("{count} violations")),
        hint: Some(hint),
        report_only: floor.is_none(),
    };
    print_gate_result(&result, no_exit)
}

/// Count of architectural violations, for the baseline writer.
fn measured_arch_violations() -> Measurement {
    if !root().join("arch.toml").exists() {
        return Measurement::Unavailable("no arch.toml".to_string());
    }
    if !tool_installed("modules") {
        return Measurement::Unavailable("cargo-modules is not installed".to_string());
    }
    Measurement::Value(arch_scan().0)
}

fn cmd_arch() {
    arch_check(false);
}

fn git_lines(args: &[&str]) -> Vec<String> {
    let Ok(output) = Command::new("git").args(args).current_dir(root()).output() else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(String::from)
        .collect()
}

fn git_prefix() -> String {
    let Some(prefix) = git_lines(&["rev-parse", "--show-prefix"]).into_iter().next() else {
        return String::new();
    };
    let normalized = prefix.trim_start_matches("./").replace('\\', "/");
    normalized.trim_matches('/').to_string()
}

fn normalize_changed_path(path: &str, prefix: &str) -> String {
    let normalized = path.trim().trim_start_matches("./").replace('\\', "/");
    if !prefix.is_empty() && normalized.starts_with(&format!("{prefix}/")) {
        return normalized[prefix.len() + 1..].to_string();
    }
    normalized
}

/// Ordered fallback refs consulted when neither `HARNESS_ARCH_BASE` nor
/// `GITHUB_BASE_REF` is set. GitHub only populates `GITHUB_BASE_REF` for
/// `pull_request` events — a direct `push` to main gets neither, so without
/// a fallback `changed_paths_from_base()` silently returns nothing and an
/// arch-config change arriving by direct push passes `ci` clean.
const DEFAULT_BASE_REF_CANDIDATES: &[&str] = &["origin/HEAD", "origin/main", "main"];

/// First candidate in `DEFAULT_BASE_REF_CANDIDATES` for which `resolves`
/// returns true, or `None` if none do. Pure and dependency-injected so the
/// fallback ordering is testable without shelling out to git.
fn pick_default_base_ref(resolves: impl Fn(&str) -> bool) -> Option<&'static str> {
    DEFAULT_BASE_REF_CANDIDATES.iter().copied().find(|candidate| resolves(candidate))
}

/// `pick_default_base_ref` wired to `git rev-parse --verify`.
fn default_base_ref() -> Option<String> {
    pick_default_base_ref(|candidate| !git_lines(&["rev-parse", "--verify", candidate]).is_empty())
        .map(String::from)
}

fn changed_paths_from_base() -> Vec<String> {
    let mut bases: Vec<String> = Vec::new();
    if let Ok(base) = env::var("HARNESS_ARCH_BASE")
        && !base.is_empty()
    {
        bases.push(base);
    }
    if let Ok(github_base) = env::var("GITHUB_BASE_REF")
        && !github_base.is_empty()
    {
        bases.push(format!("origin/{github_base}"));
    }
    if bases.is_empty() {
        // Local runs and CI `push` events land here — fall back to whichever
        // default ref resolves; skip silently (empty `paths`) if none do.
        bases.extend(default_base_ref());
    }

    let mut paths = Vec::new();
    for base in bases {
        if git_lines(&["rev-parse", "--verify", &base]).is_empty() {
            continue;
        }
        paths.extend(git_lines(&[
            "diff",
            "--name-only",
            "--diff-filter=d",
            &format!("{base}...HEAD"),
            "--",
            ".",
        ]));
    }
    paths
}

fn changed_paths_from_pre_push_stdin() -> Vec<String> {
    let mut stdin = io::stdin();
    if stdin.is_terminal() {
        return Vec::new();
    }
    let mut text = String::new();
    if stdin.read_to_string(&mut text).is_err() || text.trim().is_empty() {
        return Vec::new();
    }
    let zero = "0".repeat(40);
    let mut paths = Vec::new();
    for line in text.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 4 {
            continue;
        }
        let local_sha = parts[1];
        let remote_sha = parts[3];
        if local_sha == zero {
            continue;
        }
        if remote_sha == zero {
            paths.extend(git_lines(&[
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                local_sha,
                "--",
                ".",
            ]));
        } else {
            paths.extend(git_lines(&[
                "diff",
                "--name-only",
                "--diff-filter=d",
                remote_sha,
                local_sha,
                "--",
                ".",
            ]));
        }
    }
    paths
}

/// Every changed path — working tree + staged + untracked, or staged-only
/// when `staged`, plus the base-branch diff and (optionally) pre-push stdin
/// refs — normalized relative to this template's subtree. Shared collection
/// step behind `changed_arch_configs` and the Gherkin-first guard; each
/// filters this raw set down to what it cares about instead of re-deriving it.
fn changed_paths(staged: bool, include_pre_push_stdin: bool) -> Vec<String> {
    let mut paths = Vec::new();
    if staged {
        paths.extend(git_lines(&["diff", "--cached", "--name-only", "--diff-filter=d", "--", "."]));
    } else {
        paths.extend(git_lines(&["diff", "--name-only", "--diff-filter=d", "--", "."]));
        paths.extend(git_lines(&["diff", "--cached", "--name-only", "--diff-filter=d", "--", "."]));
        paths.extend(git_lines(&["ls-files", "--others", "--exclude-standard", "--", "."]));
        paths.extend(changed_paths_from_base());
    }
    if include_pre_push_stdin {
        paths.extend(changed_paths_from_pre_push_stdin());
    }

    let prefix = git_prefix();
    let changed: BTreeSet<String> =
        paths.into_iter().map(|p| normalize_changed_path(&p, &prefix)).collect();
    changed.into_iter().collect()
}

fn changed_arch_configs(staged: bool, include_pre_push_stdin: bool) -> Vec<String> {
    changed_paths(staged, include_pre_push_stdin)
        .into_iter()
        .filter(|p| p.as_str() == ARCH_CONFIG)
        .collect()
}

fn check_arch_config_guard(warn_only: bool, staged: bool, include_pre_push_stdin: bool) -> bool {
    let changed = changed_arch_configs(staged, include_pre_push_stdin);
    if changed.is_empty() {
        println!("  {GREEN}\u{2713}{RESET} Arch config guard");
        return true;
    }
    let joined = changed.join(", ");
    if env::var(ARCH_CONFIG_ALLOW_ENV).as_deref() == Ok("1") {
        println!("  {GREEN}\u{26a0}{RESET} Arch config guard override: {joined}");
        return true;
    }
    if warn_only {
        println!("  {GREEN}\u{26a0}{RESET} Arch config changed: {joined}");
        println!(
            "  \u{21b3} fix: review intentionally, then use {ARCH_CONFIG_ALLOW_ENV}=1 for commit/push/CI"
        );
        return true;
    }
    println!("  {RED}\u{2717}{RESET} Arch config changed: {joined}");
    println!("  \u{21b3} fix: review intentionally, then rerun with {ARCH_CONFIG_ALLOW_ENV}=1");
    false
}

fn cmd_arch_config_guard() {
    if !check_arch_config_guard(arg_flag("--warn"), arg_flag("--staged"), false) {
        std::process::exit(1);
    }
}

/// True when `path` (already normalized relative to this template's subtree)
/// is "production source" for the Gherkin-first rule: a `.rs` file under
/// `src/`, excluding anything under `tests/` and excluding `harness.rs`
/// itself — the harness's own tooling code is not product behavior. See root
/// CLAUDE.md's Gherkin-first workflow.
fn is_production_source(path: &str) -> bool {
    path.starts_with("src/")
        && path != "harness.rs"
        && Path::new(path).extension().is_some_and(|e| e.eq_ignore_ascii_case("rs"))
}

/// True when `changed` contains at least one production-source file and no
/// `.feature` file — the Gherkin-first trigger condition. Pure so it is
/// testable without shelling out to git.
fn gherkin_guard_triggered(changed: &[String]) -> bool {
    changed.iter().any(|p| is_production_source(p))
        && !changed.iter().any(|p| p.ends_with(".feature"))
}

/// Pure Gherkin-first decision: `None` means the rule does not apply yet (no
/// `.feature` files exist anywhere in the template — retrofitting into a repo
/// without an acceptance suite must never block); `Some(true)` means the
/// guard should block; `Some(false)` means it passed.
fn gherkin_guard_decision(has_features: bool, changed: &[String]) -> Option<bool> {
    has_features.then(|| gherkin_guard_triggered(changed))
}

/// Pure override check for the `HARNESS_ALLOW_NO_FEATURE` escape hatch,
/// dependency-injected so a test does not need to touch a process-global env var.
fn gherkin_guard_overridden(env_value: Option<&str>) -> bool {
    env_value == Some("1")
}

/// Gherkin-first guard: blocks (or warns) when production source changed
/// without a matching `.feature` scenario. Mirrors `check_arch_config_guard`
/// in shape, modes, and override style.
fn check_gherkin_guard(warn_only: bool, staged: bool, include_pre_push_stdin: bool) -> bool {
    let has_features = has_feature_files(&root().join("tests").join("features"));
    let changed =
        if has_features { changed_paths(staged, include_pre_push_stdin) } else { Vec::new() };
    let Some(triggered) = gherkin_guard_decision(has_features, &changed) else {
        return true; // no acceptance suite yet — retrofitting must never block (silent)
    };
    if !triggered {
        println!("  {GREEN}\u{2713}{RESET} Gherkin-first");
        return true;
    }
    let joined =
        changed.iter().filter(|p| is_production_source(p)).cloned().collect::<Vec<_>>().join(", ");
    if gherkin_guard_overridden(env::var(GHERKIN_ALLOW_ENV).ok().as_deref()) {
        println!("  {GREEN}\u{26a0}{RESET} Gherkin-first override: {joined}");
        return true;
    }
    if warn_only {
        println!(
            "  {GREEN}\u{26a0}{RESET} Gherkin-first: production source changed with no .feature: {joined}"
        );
        println!(
            "  \u{21b3} fix: add a scenario under tests/features/, or set {GHERKIN_ALLOW_ENV}=1 after review"
        );
        return true;
    }
    println!(
        "  {RED}\u{2717}{RESET} Gherkin-first: production source changed with no .feature: {joined}"
    );
    println!(
        "  \u{21b3} fix: add a scenario under tests/features/, or set {GHERKIN_ALLOW_ENV}=1 after review"
    );
    false
}

fn cmd_gherkin_guard() {
    if !check_gherkin_guard(arg_flag("--warn"), arg_flag("--staged"), false) {
        std::process::exit(1);
    }
}

/// lizard argv at this template's thresholds, tolerating `max_violations` warnings.
///
/// lizard's own `-i N` is the count ratchet: it exits 0 while the number of flagged
/// functions stays at or below N, so lizard does the counting. Single-sourced
/// because a floor measured against a different target set does not reproduce —
/// the gate and the measurement must pass byte-identical arguments.
fn complexity_argv(max_violations: u32) -> Vec<String> {
    [
        "uvx",
        "lizard@1.22.2",
        "-l",
        "rust",
        "src",
        "tests",
        "-C",
        &COMPLEXITY_MAX_CCN.to_string(),
        "-a",
        &COMPLEXITY_MAX_ARGS.to_string(),
        "-L",
        &COMPLEXITY_MAX_LENGTH.to_string(),
        "-i",
        &max_violations.to_string(),
    ]
    .iter()
    .map(|s| (*s).to_string())
    .collect()
}

/// The lizard gate at the committed floor, or report-only when there is none.
///
/// With no floor recorded, `-i 0` would demand a legacy tree already be perfect —
/// exactly the day-one red that stops the harness being adopted. Measure instead:
/// `-i` goes high enough that lizard always exits 0, and the label says why.
fn complexity_gate_for(floor: Option<u32>) -> Gate {
    let argv = complexity_argv(floor.unwrap_or(REPORT_ONLY_LIMIT));
    let cmd: Vec<&str> = argv.iter().map(String::as_str).collect();
    let Some(floor) = floor else {
        return Gate::new(
            format!("Complexity (lizard, report-only: no {BASELINE_FILE} floor)"),
            &cmd,
        )
        .with_hint("run `cargo harness suppressions --update-baseline` to record a floor")
        .report_only();
    };
    let description = if floor == 0 {
        "Complexity (lizard)".to_string()
    } else {
        format!("Complexity (lizard, baseline {floor})")
    };
    Gate::new(description, &cmd).with_hint(format!(
        "extract helpers or flatten branches until CCN <= {COMPLEXITY_MAX_CCN}; \
         do not raise the threshold"
    ))
}

fn complexity_gate() -> Gate {
    complexity_gate_for(baseline_floor("complexity.max_violations"))
}

fn cmd_complexity() {
    print_gate_result(&run_capture(&complexity_gate()), false);
}

/// Read the `Warning cnt` column out of lizard's final summary row.
///
/// The summary is the only place lizard states the count as a number; every other
/// rendering would have to be counted line by line, which changes shape with the
/// `-w`/`--csv` flags.
fn lizard_warning_count(stdout: &str) -> Option<u32> {
    let lines: Vec<&str> = stdout.lines().collect();
    let header = lines.iter().position(|line| line.starts_with("Total nloc"))?;
    for row in &lines[header + 1..] {
        let trimmed = row.trim();
        if trimmed.is_empty() || trimmed.chars().all(|c| c == '-') {
            continue;
        }
        let fields: Vec<&str> = row.split_whitespace().collect();
        if fields.len() < 6 {
            continue;
        }
        return fields[5].parse::<u32>().ok();
    }
    None
}

/// Count of functions lizard flags at the template's thresholds.
fn measured_complexity_violations() -> Measurement {
    if !root().join("src").is_dir() {
        return Measurement::Unavailable("no src/ sources".to_string());
    }
    let argv = complexity_argv(REPORT_ONLY_LIMIT);
    let output = Command::new(&argv[0]).args(&argv[1..]).current_dir(root()).output();
    let output = match output {
        Ok(output) => output,
        Err(e) => return Measurement::Error(format!("lizard failed to run: {e}")),
    };
    if !output.status.success() {
        return Measurement::Error(format!(
            "lizard failed to run (exit {:?})",
            output.status.code()
        ));
    }
    lizard_warning_count(&String::from_utf8_lossy(&output.stdout)).map_or_else(
        || Measurement::Error("lizard printed no summary row to count warnings from".to_string()),
        Measurement::Value,
    )
}

/// Selects the CRAP gate's status glyph/color and the `(advisory)` suffix.
/// `✗`/red is reserved for `--enforce` runs, which actually exit 1; the
/// default advisory mode always exits 0, so it gets `⚠`/green even when it
/// lists offenders or a lizard failure — a red ✗ that still exits 0 tells the
/// human the build failed when it did not.
const fn crap_status_glyph(enforce: bool) -> (&'static str, &'static str, &'static str) {
    if enforce { (RED, "\u{2717}", "") } else { (GREEN, "\u{26a0}", " (advisory)") }
}

/// What scoring CRAP produced: the offenders, or why there are none to report.
enum CrapOutcome {
    /// cargo-llvm-cov is not installed. Nothing to score, and not a failure —
    /// the measurement is `unavailable`, not an error.
    Skipped(String),
    /// Coverage data could not be produced or read. Fatal even in advisory mode:
    /// scoring against absent data would print a green ✓ that means nothing.
    Fatal(String),
    /// lizard itself failed. Advisory mode degrades this to a warning; reporting
    /// "all functions below max" instead would be a silent false pass.
    ToolFailure { message: String, detail: String, code: i32 },
    /// Functions whose CRAP exceeds the threshold, worst first.
    Scored(Vec<CrapFn>),
}

/// Compute CRAP = CCN² × (1-cov)³ + CCN per function. Advisory by default.
///
/// Joins `lizard --csv` (per-function CCN + line range) with the LCOV file
/// produced by `cargo llvm-cov`. Functions with CRAP above `--max=N`
/// (default 30) are listed and the gate exits 1 only when `--enforce` is set.
///
/// LCOV reuse: when invoked from `cmd_ci`, `cmd_coverage` has already produced
/// `target/llvm-cov/lcov.info`; this command reuses it. Standalone runs (or
/// runs where `src/` is newer than the existing LCOV) trigger a full test
/// re-execution to avoid scoring against stale coverage.
///
/// Score every function's CRAP against `max_crap`, refreshing coverage if stale.
///
/// Split out from `cmd_crap` so `--update-baseline` can measure the same number
/// the gate reports without the printing or the `exit()` calls — a floor recorded
/// from a different code path is a floor that does not reproduce.
/// Whether `target/llvm-cov/lcov.info` is usable, and why not when it is not.
///
/// The reasons are deliberately gate-neutral: they surface both as CRAP's own
/// failure line and as the `coverage.min` measurement's reason, and a coverage
/// key dropped "because CRAP skipped" reads like a bug.
enum LcovStatus {
    Ready(PathBuf),
    /// cargo-llvm-cov is not installed. Not a failure — nothing can be measured.
    Skipped(String),
    /// The run happened and could not produce readable coverage data.
    Fatal(String),
}

/// Ensure the LCOV artifact exists and is newer than the sources it scores.
///
/// Shared by `crap_measure` and the `coverage.min` measurement so both read one
/// instrumented run: regenerating it twice would double the slowest step of
/// `suppressions --update-baseline` for no new information.
fn ensure_lcov() -> LcovStatus {
    if !tool_installed("llvm-cov") {
        return LcovStatus::Skipped("cargo-llvm-cov is not installed".to_string());
    }
    let lcov_path = root().join("target").join("llvm-cov").join("lcov.info");
    if lcov_path.exists() && lcov_is_fresh(&lcov_path, &["src", "tests", "harness.rs"]) {
        return LcovStatus::Ready(lcov_path);
    }
    if let Some(parent) = lcov_path.parent()
        && let Err(e) = fs::create_dir_all(parent)
    {
        return LcovStatus::Fatal(format!("cannot create {}: {e}", parent.display()));
    }
    let env = llvm_tools_env();
    let lcov_str = lcov_path.to_string_lossy().into_owned();
    let run_result = run(
        "CRAP: running tests under llvm-cov",
        &["cargo", "llvm-cov", "--no-report"],
        Some(&RunOpts { env: env.clone(), no_exit: true, ..RunOpts::default() }),
    );
    let report_result = if run_result.ok {
        run(
            "CRAP: emit LCOV",
            &["cargo", "llvm-cov", "report", "--lcov", "--output-path", &lcov_str],
            Some(&RunOpts { env, no_exit: true, ..RunOpts::default() }),
        )
    } else {
        run_result
    };
    if !report_result.ok || !lcov_path.exists() {
        return LcovStatus::Fatal(format!("could not produce {}", lcov_path.display()));
    }
    LcovStatus::Ready(lcov_path)
}

/// Total line coverage from the LCOV artifact, truncated to an integer.
///
/// Truncated, never rounded: 53.9% recorded as 54 would fail the very gate that
/// measured it. LCOV `DA:` records are the same lines cargo-llvm-cov counts as its
/// line coverage, so the floor written here is the number
/// `coverage --fail-under-lines` compares against.
fn measured_coverage_min() -> Measurement {
    let lcov_path = match ensure_lcov() {
        LcovStatus::Ready(path) => path,
        LcovStatus::Skipped(reason) => return Measurement::Unavailable(reason),
        LcovStatus::Fatal(reason) => return Measurement::Error(reason),
    };
    let Some(cov_map) = parse_lcov(&lcov_path) else {
        return Measurement::Error(format!("failed to read {}", lcov_path.display()));
    };
    lcov_line_coverage(&cov_map).map_or_else(
        || Measurement::Unavailable("no line coverage data".to_string()),
        Measurement::Value,
    )
}

/// Percent of tracked lines that were hit, truncated toward zero.
///
/// `None` when nothing is tracked at all — a repo with no instrumented lines has
/// no coverage to floor, which is different from 0% coverage.
fn lcov_line_coverage(cov_map: &HashMap<String, HashMap<u32, u32>>) -> Option<u32> {
    let mut tracked: u64 = 0;
    let mut covered: u64 = 0;
    for lines in cov_map.values() {
        tracked += lines.len() as u64;
        covered += lines.values().filter(|&&hits| hits > 0).count() as u64;
    }
    if tracked == 0 {
        return None;
    }
    u32::try_from(covered * 100 / tracked).ok()
}

fn crap_measure(max_crap: f64) -> CrapOutcome {
    let lcov_path = match ensure_lcov() {
        LcovStatus::Ready(path) => path,
        LcovStatus::Skipped(reason) => return CrapOutcome::Skipped(reason),
        LcovStatus::Fatal(reason) => return CrapOutcome::Fatal(reason),
    };

    let Some(cov_map) = parse_lcov(&lcov_path) else {
        return CrapOutcome::Fatal(format!("failed to read {}", lcov_path.display()));
    };

    // `-i` high: here lizard is a measuring tape, not a gate. Left at its default
    // it exits 1 on any function over CCN 15, and CRAP would report "lizard
    // exited" for exactly the repos that most need scoring.
    let lz_output = Command::new("uvx")
        .args(["lizard@1.22.2", "-l", "rust", "src", "--csv", "-i", &REPORT_ONLY_LIMIT.to_string()])
        .current_dir(root())
        .output();

    let lz_stdout = match lz_output {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).into_owned(),
        Ok(o) => {
            return CrapOutcome::ToolFailure {
                message: format!("lizard exited {:?}", o.status.code()),
                detail: String::from_utf8_lossy(&o.stderr).into_owned(),
                code: o.status.code().unwrap_or(1),
            };
        }
        Err(e) => {
            return CrapOutcome::ToolFailure {
                message: format!("failed to run lizard: {e}"),
                detail: String::new(),
                code: 1,
            };
        }
    };

    // cargo-llvm-cov writes absolute paths in `SF:` lines while lizard emits
    // paths relative to cwd. Build both key shapes for the join — using only
    // the relative form would silently score every function as cov=0.
    let abs_root = root().to_string_lossy().into_owned();
    let mut offenders: Vec<CrapFn> = Vec::new();
    for row in lz_stdout.lines() {
        let Some(parsed) = parse_lizard_csv_row(row) else { continue };
        let (ccn, name, start, end, path) = parsed;
        let normalized = path.trim_start_matches("./").to_string();
        let abs_key = format!("{abs_root}/{normalized}");
        let lines = cov_map
            .get(&abs_key)
            .or_else(|| cov_map.get(&normalized))
            .or_else(|| cov_map.get(&path));
        let cov = lines.map_or(0.0, |map| {
            let in_range: Vec<u32> = (start..=end).filter_map(|n| map.get(&n).copied()).collect();
            if in_range.is_empty() {
                0.0
            } else {
                let covered =
                    u32::try_from(in_range.iter().filter(|&&h| h > 0).count()).unwrap_or(u32::MAX);
                let tracked = u32::try_from(in_range.len()).unwrap_or(u32::MAX);
                f64::from(covered) / f64::from(tracked)
            }
        });
        let crap = crap_score(ccn, cov);
        if crap > max_crap {
            offenders.push(CrapFn {
                crap,
                ccn,
                cov,
                location: format!("{name}@{start}-{end}@{path}"),
            });
        }
    }
    offenders.sort_by(|a, b| b.crap.partial_cmp(&a.crap).unwrap_or(std::cmp::Ordering::Equal));
    CrapOutcome::Scored(offenders)
}

/// Count of functions above the default CRAP threshold, for the baseline writer.
fn measured_crap_violations() -> Measurement {
    match crap_measure(CRAP_MAX_DEFAULT) {
        CrapOutcome::Skipped(reason) => Measurement::Unavailable(reason),
        CrapOutcome::Fatal(message) | CrapOutcome::ToolFailure { message, .. } => {
            Measurement::Error(message)
        }
        CrapOutcome::Scored(offenders) => {
            Measurement::Value(u32::try_from(offenders.len()).unwrap_or(u32::MAX))
        }
    }
}

fn cmd_crap() {
    let max_crap: f64 =
        arg_value("--max").and_then(|v| v.parse::<f64>().ok()).unwrap_or(CRAP_MAX_DEFAULT);
    let enforce = arg_flag("--enforce");

    let offenders = match crap_measure(max_crap) {
        CrapOutcome::Skipped(_) => {
            println!("  {DIM}\u{2298} CRAP skipped (install: cargo install cargo-llvm-cov){RESET}");
            return;
        }
        CrapOutcome::Fatal(message) => {
            println!("  {RED}\u{2717}{RESET} CRAP: {message}");
            std::process::exit(1);
        }
        CrapOutcome::ToolFailure { message, detail, code } => {
            let (color, symbol, suffix) = crap_status_glyph(enforce);
            println!("  {color}{symbol}{RESET} CRAP: {message}{suffix}");
            if !detail.is_empty() {
                print!("{detail}");
            }
            if enforce {
                std::process::exit(code);
            }
            return;
        }
        CrapOutcome::Scored(offenders) => offenders,
    };

    if offenders.is_empty() {
        println!("  {GREEN}\u{2713}{RESET} CRAP: all functions below {max_crap:.0}");
        return;
    }

    // The baseline is a count floor: a repo adopting the harness starts wherever it
    // already is, and that number may only come down.
    let count = u32::try_from(offenders.len()).unwrap_or(u32::MAX);
    let Some(floor) = baseline_floor("crap.max_violations") else {
        // Nothing recorded is not a floor of 0; it is a repo that has never been
        // measured. Report what is there and pass — `--enforce` included — so
        // retrofitting the harness into a legacy tree is green on day one.
        println!(
            "  {GREEN}\u{26a0}{RESET} CRAP: {count} function(s) exceed \
             {max_crap:.0} (report-only: no {BASELINE_FILE} floor)"
        );
        print_crap_offenders(&offenders);
        println!("  ↳ fix: run `cargo harness suppressions --update-baseline` to record a floor");
        return;
    };
    if count <= floor {
        println!(
            "  {GREEN}\u{2713}{RESET} CRAP: {count} function(s) exceed \
             {max_crap:.0} (baseline {floor})"
        );
        return;
    }

    let (color, symbol, suffix) = crap_status_glyph(enforce);
    println!(
        "  {color}{symbol}{RESET} CRAP: {count} function(s) exceed \
         {max_crap:.0} (baseline {floor}){suffix}"
    );
    print_crap_offenders(&offenders);
    if enforce {
        std::process::exit(1);
    }
}

/// List the worst offenders under a CRAP verdict line. Capped at 20: past that the
/// list stops being something anyone reads and starts being a wall.
fn print_crap_offenders(offenders: &[CrapFn]) {
    for o in offenders.iter().take(20) {
        println!(
            "    CRAP={:6.1}  CCN={:3}  cov={:5.1}%  {}",
            o.crap,
            o.ccn,
            o.cov * 100.0,
            o.location
        );
    }
}

/// True when `lcov_path` is at least as new as every `.rs` file under `src_dirs`.
///
/// Used by `cmd_crap` to detect "I edited source but did not re-run coverage"
/// staleness: scoring fresh complexity data against an old LCOV silently
/// misattributes coverage. Returns false (force regeneration) on any I/O or
/// metadata error so the safe path is to re-run, not to trust stale data.
fn lcov_is_fresh(lcov_path: &Path, targets: &[&str]) -> bool {
    let Ok(lcov_meta) = fs::metadata(lcov_path) else { return false };
    let Ok(lcov_mtime) = lcov_meta.modified() else { return false };
    for target in targets {
        let target_path = root().join(target);
        // A target may be a single file (`harness.rs`): `read_dir` silently
        // skips those, which would make every harness-only edit look fresh —
        // and harness.rs is nearly every tracked line in this crate.
        if target_path.is_file() {
            if newer_than(&target_path, lcov_mtime) {
                return false;
            }
            continue;
        }
        let mut stack = vec![target_path];
        while let Some(p) = stack.pop() {
            let Ok(entries) = fs::read_dir(&p) else { continue };
            for entry in entries.flatten() {
                let path = entry.path();
                let Ok(ft) = entry.file_type() else { continue };
                if ft.is_dir() {
                    stack.push(path);
                    continue;
                }
                if path.extension().is_some_and(|e| e == "rs") && newer_than(&path, lcov_mtime) {
                    return false;
                }
            }
        }
    }
    true
}

/// True when `path` was modified after `mtime`. An unreadable mtime counts as
/// "not newer": the caller's other targets still decide, and the fallback for a
/// path we cannot stat is to keep looking rather than to force a rebuild.
fn newer_than(path: &Path, mtime: std::time::SystemTime) -> bool {
    path.metadata().and_then(|meta| meta.modified()).is_ok_and(|m| m > mtime)
}

/// CRAP score = CCN² × (1-cov)³ + CCN. `cov` is in [0,1].
fn crap_score(ccn: u32, cov: f64) -> f64 {
    let ccn_f = f64::from(ccn);
    (ccn_f * ccn_f).mul_add((1.0 - cov).powi(3), ccn_f)
}

struct CrapFn {
    crap: f64,
    ccn: u32,
    cov: f64,
    location: String,
}

/// Parse LCOV into `{file: {lineNumber: hits}}`. Recognizes `SF:`, `DA:`, `end_of_record`.
fn parse_lcov(path: &Path) -> Option<HashMap<String, HashMap<u32, u32>>> {
    let text = fs::read_to_string(path).ok()?;
    Some(parse_lcov_str(&text))
}

/// In-memory variant of `parse_lcov` — same grammar, but operates on a string.
/// Exists so tests can exercise the parser without touching the filesystem.
fn parse_lcov_str(text: &str) -> HashMap<String, HashMap<u32, u32>> {
    let mut map: HashMap<String, HashMap<u32, u32>> = HashMap::new();
    let mut cur_file: Option<String> = None;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("SF:") {
            let file = rest.trim().to_string();
            map.entry(file.clone()).or_default();
            cur_file = Some(file);
        } else if line == "end_of_record" {
            cur_file = None;
        } else if let (Some(rest), Some(file)) = (line.strip_prefix("DA:"), cur_file.as_ref()) {
            let mut parts = rest.split(',');
            let (Some(n), Some(h)) = (parts.next(), parts.next()) else { continue };
            let (Ok(ln), Ok(hits)) = (n.parse::<u32>(), h.parse::<u32>()) else { continue };
            if let Some(file_map) = map.get_mut(file) {
                file_map.insert(ln, hits);
            }
        }
    }
    map
}

/// Parse one `lizard --csv` row into (ccn, name, start, end, path).
///
/// Lizard columns: nloc,ccn,token,param,length,location,file,name,sig,start,end.
/// The location column is the only one whose value is self-contained:
/// `"name@start-end@path"`. Signatures can contain commas, so we extract
/// the location field directly rather than splitting the whole row.
fn parse_lizard_csv_row(row: &str) -> Option<(u32, String, u32, u32, String)> {
    let mut iter = row.splitn(6, ',');
    let _nloc = iter.next()?;
    let ccn: u32 = iter.next()?.parse().ok()?;
    let _token = iter.next()?;
    let _param = iter.next()?;
    let _length = iter.next()?;
    let rest = iter.next()?;
    let after_q = rest.strip_prefix('"')?;
    let end_q = after_q.find('"')?;
    let location = &after_q[..end_q];
    // location format: name@start-end@path. name may be empty (anonymous).
    let at1 = location.find('@')?;
    let after_at1 = &location[at1 + 1..];
    let dash = after_at1.find('-')?;
    let after_dash = &after_at1[dash + 1..];
    let at2 = after_dash.find('@')?;
    let name = location[..at1].to_string();
    let start: u32 = after_at1[..dash].parse().ok()?;
    let end: u32 = after_dash[..at2].parse().ok()?;
    let path = after_dash[at2 + 1..].to_string();
    Some((ccn, name, start, end, path))
}

/// True when `cargo <subcommand> --version` succeeds (the subcommand is installed).
fn tool_installed(subcommand: &str) -> bool {
    Command::new("cargo")
        .args([subcommand, "--version"])
        .current_dir(root())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

/// Return the value of a `--name=value` CLI argument, if present.
fn arg_value(name: &str) -> Option<String> {
    let prefix = format!("{name}=");
    env::args().skip(1).find_map(|a| a.strip_prefix(&prefix).map(String::from))
}

/// True when a bare `--name` flag appears anywhere in the CLI args.
fn arg_flag(name: &str) -> bool {
    env::args().skip(1).any(|a| a == name)
}

// ── Stages ──────────────────────────────────────────────────────────

/// Warn when Claude/Codex Stop hook wiring is missing.
fn check_stop_hooks_present() {
    for rel in [".claude/settings.json", ".codex/hooks.json"] {
        let text = fs::read_to_string(root().join(rel)).unwrap_or_default();
        if text.contains("Stop") && text.contains("stop-hook") {
            println!("  {GREEN}\u{2713}{RESET} Stop hook wiring ({rel})");
        } else {
            println!("  {RED}\u{26a0}{RESET} Missing Stop hook wiring: {rel}");
        }
    }
}

/// 1-based line number of the first divergence between `a` and `b`.
fn first_diff_line(left: &str, right: &str) -> usize {
    let mut left_lines = left.lines();
    let mut right_lines = right.lines();
    let mut line_number = 0usize;
    loop {
        match (left_lines.next(), right_lines.next()) {
            (Some(left_line), Some(right_line)) => {
                line_number += 1;
                if left_line != right_line {
                    return line_number;
                }
            }
            _ => return line_number + 1,
        }
    }
}

/// Fail if AGENTS.md differs byte-for-byte from CLAUDE.md.
/// Returns ok=true on identity. With `no_exit=false`, exits 1 on mismatch.
fn check_agents_md_drift(no_exit: bool) -> RunResult {
    let claude_path = root().join("CLAUDE.md");
    let agents_path = root().join("AGENTS.md");
    let fail = |msg: String| -> RunResult {
        println!("  {RED}\u{2717}{RESET} agents-md-drift: {msg}");
        if !no_exit {
            std::process::exit(1);
        }
        RunResult { ok: false, output: msg }
    };
    let Ok(a) = fs::read(&claude_path) else {
        return fail("CLAUDE.md not found".into());
    };
    let Ok(b) = fs::read(&agents_path) else {
        return fail("AGENTS.md missing \u{2014} run `cargo harness sync-agents-md`".into());
    };
    if a == b {
        println!("  {GREEN}\u{2713}{RESET} agents-md-drift");
        return RunResult { ok: true, output: String::new() };
    }
    let line = first_diff_line(&String::from_utf8_lossy(&a), &String::from_utf8_lossy(&b));
    fail(format!(
        "AGENTS.md differs from CLAUDE.md (first diff at line {line}) \u{2014} run `cargo harness sync-agents-md`"
    ))
}

fn cmd_agents_md_drift() {
    check_agents_md_drift(false);
}

/// Overwrite AGENTS.md with CLAUDE.md contents.
fn cmd_sync_agents_md() {
    let claude_path = root().join("CLAUDE.md");
    let Ok(bytes) = fs::read(&claude_path) else {
        println!("  {RED}\u{2717}{RESET} sync-agents-md: CLAUDE.md not found");
        std::process::exit(1);
    };
    if let Err(e) = fs::write(root().join("AGENTS.md"), &bytes) {
        println!("  {RED}\u{2717}{RESET} sync-agents-md: {e}");
        std::process::exit(1);
    }
    println!("  {GREEN}\u{2713}{RESET} sync-agents-md: AGENTS.md \u{2190} CLAUDE.md");
}

fn cmd_check() {
    let start = Instant::now();
    println!("\n{BLUE}[check]{RESET} Running pre-flight checks...\n");

    let mut results = vec![
        run(
            "Clippy fix",
            &["cargo", "clippy", "--fix", "--allow-dirty", "--allow-staged"],
            Some(&RunOpts { no_exit: true, ..RunOpts::default() }),
        ),
        run("Format", &["cargo", "fmt"], Some(&RunOpts { no_exit: true, ..RunOpts::default() })),
        run(
            // `[[test]]` targets (Cargo.toml) always run under plain `cargo
            // test`, including the `acceptance` binary (harness = false,
            // tests/acceptance.rs) — so this one line already exercises the
            // Gherkin/cucumber scenarios too. No separate acceptance gate
            // here: see the `ci` minus `check` invariant documented in
            // CLAUDE.md.
            "Tests (incl. acceptance)",
            &["cargo", "test"],
            Some(&RunOpts {
                extract: Some(extract_test_summary),
                no_exit: true,
                ..RunOpts::default()
            }),
        ),
        check_agents_md_drift(true),
    ];

    let complexity_failed = run_gates_parallel_detailed(&[complexity_gate()]);
    results.push(RunResult { ok: complexity_failed.is_empty(), output: String::new() });

    check_stop_hooks_present();
    check_arch_config_guard(true, false, false);
    check_gherkin_guard(true, false, false);
    results.push(RunResult { ok: check_suppressions_baseline(true), output: String::new() });

    let elapsed = start.elapsed().as_secs_f64();
    let passed = results.iter().filter(|r| r.ok).count();
    let failed = results.len() - passed;

    println!();
    if failed > 0 {
        println!("{RED}FAIL{RESET} {passed} passed, {failed} failed {DIM}({elapsed:.1}s){RESET}");
        std::process::exit(1);
    }
    println!("{GREEN}OK{RESET} {passed} passed {DIM}({elapsed:.1}s){RESET}");
}

/// The arch-config and gherkin-first guards run before the staged-files early
/// return: both are staged-mode and cheap, and a commit that stages only a
/// non-Rust file (an arch config edit alone, a `.md`, a lockfile) must not
/// bypass them.
fn cmd_pre_commit() {
    println!("\n{BLUE}[pre-commit]{RESET}\n");

    if !check_arch_config_guard(false, true, false) {
        std::process::exit(1);
    }
    if !check_gherkin_guard(false, true, false) {
        std::process::exit(1);
    }

    let files = staged_rs_files();
    if files.is_empty() {
        println!("No staged Rust files \u{2014} skipping checks");
        return;
    }

    cmd_fix();
    restage_fixed_files(&files);
    check_agents_md_drift(false);
    cmd_test();
}

/// Paths from `files` that still exist under `base` — the cheap, testable
/// guard `restage_fixed_files` uses to skip re-staging anything `cmd_fix()`
/// might have deleted (clippy --fix / cargo fmt don't delete files, but
/// checking costs nothing). Pure: takes `base` explicitly so tests don't
/// need to touch `root()` or the real repo layout.
fn existing_paths<'a>(base: &Path, files: &'a [String]) -> Vec<&'a str> {
    files.iter().map(String::as_str).filter(|f| base.join(f).exists()).collect()
}

/// Re-stage files `cmd_fix()` may have rewritten. `cmd_fix()` (clippy --fix,
/// cargo fmt) mutates the working tree, but a commit records the INDEX — so
/// without this, a fixed file's pre-fix blob is what actually gets
/// committed. Caveat: if a file was only partially staged, `git add` also
/// stages its remaining unstaged hunks (documented in CLAUDE.md).
fn restage_fixed_files(files: &[String]) {
    let existing = existing_paths(root(), files);
    if existing.is_empty() {
        return;
    }
    let mut args = vec!["add", "--"];
    args.extend(existing.iter().copied());
    let status = Command::new("git").args(&args).current_dir(root()).status();
    if status.is_ok_and(|s| s.success()) {
        println!("  {GREEN}\u{2713}{RESET} Re-staged {} fixed file(s)", existing.len());
    } else {
        println!("  {RED}\u{2717}{RESET} Failed to re-stage fixed files");
        std::process::exit(1);
    }
}

fn cmd_ci() {
    println!("\n{BLUE}[ci]{RESET}\n");
    // Only gates that never shell out to cargo run as a true parallel batch —
    // captured, printed in submission order, run to completion. Clippy,
    // acceptance, and arch all invoke cargo, which takes an exclusive lock on
    // target/ per invocation, so they run sequentially below instead of
    // queuing behind each other under the illusion of concurrency — see
    // run_gates_sequential.
    let parallel_ok = run_gates_parallel(&[format_check_gate(), complexity_gate()]);

    let mut cargo_gates = vec![lint_gate()];
    cargo_gates.extend(acceptance_gates_or_warn());
    let cargo_ok = run_gates_sequential(&cargo_gates);
    // Arch runs after the batch, not inside it: it is two cargo-modules passes
    // summed into one count and compared to a floor, which no single-command
    // Gate can express. It still takes cargo's build lock, so it stays here.
    let arch_ok = arch_check(true);

    // Bind each result before combining: every step must run (no &&-short-circuit)
    // so one pass surfaces every failure. Audit is install-aware and strict in ci.
    let audit_ok = cmd_audit_inner(true);
    // cmd_coverage() below already runs the full suite once under
    // `cargo llvm-cov --no-report` when the tool is installed — a standalone
    // `cargo test` here would be a second, redundant full build+test pass.
    // Only run it when llvm-cov is absent, so ci still exercises the suite
    // either way (cmd_coverage's own run already surfaces test failures —
    // see its `run()` calls, which are not `no_exit`).
    let tests_ok = if tool_installed("llvm-cov") {
        true
    } else {
        run("Tests", &["cargo", "test"], Some(&RunOpts { no_exit: true, ..RunOpts::default() })).ok
    };
    cmd_coverage();
    cmd_crap();
    let arch_config_ok = check_arch_config_guard(false, false, false);
    let gherkin_ok = check_gherkin_guard(false, false, false);
    let suppressions_ok = check_suppressions_baseline(true);
    if !parallel_ok
        || !cargo_ok
        || !arch_ok
        || !audit_ok
        || !tests_ok
        || !arch_config_ok
        || !gherkin_ok
        || !suppressions_ok
    {
        std::process::exit(1);
    }
}

/// Read-only push gate: the offline checks pre-commit and stop-hook do not run.
/// pre-commit covers fix/format/test on staged files; stop-hook adds complexity.
/// This fills the gap with the deterministic, offline gates none of them run —
/// clippy (strict), format check, acceptance, arch — validating the whole pushed
/// tree (after merges/rebases/--no-verify) before it leaves the machine. Network
/// (audit) and advisory (coverage/CRAP) gates stay in ci.
///
/// Format check is the only gate here that doesn't shell out to cargo, so it
/// runs on its own; clippy, acceptance, and arch all take cargo's exclusive
/// target/ lock and run sequentially instead — see `run_gates_sequential`.
fn cmd_pre_push() {
    println!("\n{BLUE}[pre-push]{RESET}\n");
    let arch_config_ok = check_arch_config_guard(false, false, true);
    let gherkin_ok = check_gherkin_guard(false, false, true);
    let format_ok = print_gate_result(&run_capture(&format_check_gate()), true);

    let mut cargo_gates = vec![lint_gate()];
    cargo_gates.extend(acceptance_gates_or_warn());
    let cargo_ok = run_gates_sequential(&cargo_gates);
    // Arch runs after the batch, not inside it: it is two cargo-modules passes
    // summed into one count and compared to a floor, which no single-command
    // Gate can express. It still takes cargo's build lock, so it stays here.
    let arch_ok = arch_check(true);

    if !format_ok || !cargo_ok || !arch_ok || !arch_config_ok || !gherkin_ok {
        std::process::exit(1);
    }
}

/// Resolve a git hook path via `git rev-parse` so worktrees / `core.hooksPath` land
/// in the right place. `GIT_*` env is stripped so an ambient `GIT_DIR` from a
/// parent process can't redirect us. Falls back to `.git/hooks/<name>` if git is absent.
fn git_hook_path(name: &str) -> PathBuf {
    let fallback = || root().join(".git").join("hooks").join(name);
    let mut cmd = Command::new("git");
    cmd.args(["rev-parse", "--git-path", &format!("hooks/{name}")]).current_dir(root());
    cmd.env_clear();
    for (key, value) in env::vars() {
        if !key.starts_with("GIT_") {
            cmd.env(key, value);
        }
    }
    let Ok(out) = cmd.output() else { return fallback() };
    if !out.status.success() {
        return fallback();
    }
    let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if path.is_empty() {
        return fallback();
    }
    let candidate = Path::new(&path);
    if candidate.is_absolute() { candidate.to_path_buf() } else { root().join(candidate) }
}

/// Install a git hook shim that runs the matching `cargo harness <name>`.
fn install_git_hook(name: &str) {
    let path = git_hook_path(name);
    if let Some(parent) = path.parent()
        && let Err(e) = fs::create_dir_all(parent)
    {
        eprintln!("Failed to create hooks directory: {e}");
        std::process::exit(1);
    }
    if fs::write(&path, format!("#!/bin/sh\ncargo harness {name}\n")).is_err() {
        eprintln!("Failed to write {name} hook");
        std::process::exit(1);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o755));
    }
}

fn cmd_hooks() {
    install_git_hook("pre-commit");
    install_git_hook("pre-push");
    println!("Installed pre-commit and pre-push git hooks");
    // The runner is std-only (no JSON parser), so it verifies the Stop wiring
    // rather than injecting into settings that may carry other hooks. The template
    // ships .claude/settings.json and .codex/hooks.json already wired; copy them in
    // (cp -r the template's .claude / .codex) if this warns.
    check_stop_hook_present();
}

fn check_stop_hook_present() {
    let root = root();
    for rel in [".claude/settings.json", ".codex/hooks.json"] {
        let content = fs::read_to_string(root.join(rel)).unwrap_or_default();
        if content.contains("Stop") && content.contains("stop-hook") {
            println!("  {GREEN}\u{2713}{RESET} Stop hook wiring ({rel})");
        } else {
            println!("  {RED}\u{26a0}{RESET} Missing Stop hook wiring: {rel}");
        }
    }
}

fn cmd_clean() {
    println!("\n{BLUE}[clean]{RESET}\n");
    run("Clean build artifacts", &["cargo", "clean"], None);

    for name in ["lcov.info", "tarpaulin-report.html"] {
        let path = root().join(name);
        if path.exists() {
            let _ = fs::remove_file(&path);
            println!("  {GREEN}\u{2713}{RESET} Removed {name}");
        }
    }

    // cargo-mutants writes its build sandbox here.
    let mutants = root().join("mutants.out");
    if mutants.is_dir() {
        let _ = fs::remove_dir_all(&mutants);
        println!("  {GREEN}\u{2713}{RESET} Removed mutants.out");
    }
}

// ── CLI dispatch ────────────────────────────────────────────────────

const COMMANDS: &[(&str, fn())] = &[
    ("check", cmd_check),
    ("fix", cmd_fix),
    ("lint", cmd_lint),
    ("test", cmd_test),
    ("audit", cmd_audit),
    ("acceptance", cmd_acceptance),
    ("coverage", cmd_coverage),
    ("mutation", cmd_mutation),
    ("arch", cmd_arch),
    ("arch-config-guard", cmd_arch_config_guard),
    ("gherkin-guard", cmd_gherkin_guard),
    ("complexity", cmd_complexity),
    ("crap", cmd_crap),
    ("suppressions", cmd_suppressions),
    ("pre-commit", cmd_pre_commit),
    ("pre-push", cmd_pre_push),
    ("ci", cmd_ci),
    ("setup-hooks", cmd_hooks),
    ("post-edit", cmd_post_edit),
    ("stop-hook", cmd_stop_hook),
    ("agents-md-drift", cmd_agents_md_drift),
    ("sync-agents-md", cmd_sync_agents_md),
    ("clean", cmd_clean),
];

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).filter(|a| !a.starts_with('-')).collect();

    if args.is_empty() {
        cmd_check();
        return ExitCode::SUCCESS;
    }

    let command = args[0].as_str();
    for (name, fun) in COMMANDS {
        if *name == command {
            fun();
            return ExitCode::SUCCESS;
        }
    }

    eprintln!("Unknown command: {command}");
    ExitCode::FAILURE
}

#[cfg(test)]
mod tests {
    use std::io::Write as _;

    use super::*;

    #[test]
    fn plain_code_no_match() {
        assert!(parse_line_for_suppressions("let x = 1;").is_empty());
    }

    #[test]
    fn single_allow_with_one_rule() {
        let result = parse_line_for_suppressions("#[allow(dead_code)]");
        assert_eq!(result, vec![("allow".to_string(), vec!["dead_code".to_string()])]);
    }

    #[test]
    fn allow_with_multiple_rules_and_namespaces() {
        let result = parse_line_for_suppressions("#[allow(unused, clippy::bool_to_int_with_if)]");
        assert_eq!(
            result,
            vec![(
                "allow".to_string(),
                vec!["unused".to_string(), "clippy::bool_to_int_with_if".to_string()]
            )],
        );
    }

    #[test]
    fn multiple_allows_on_one_line() {
        let result = parse_line_for_suppressions("#[allow(a)] fn f() {} #[allow(b)]");
        assert_eq!(
            result,
            vec![
                ("allow".to_string(), vec!["a".to_string()]),
                ("allow".to_string(), vec!["b".to_string()]),
            ],
        );
    }

    #[test]
    fn crate_level_allow() {
        let result = parse_line_for_suppressions("#![allow(dead_code)]");
        assert!(
            result.iter().any(|(k, r)| k == "allow_crate" && r == &vec!["dead_code".to_string()])
        );
    }

    #[test]
    fn feature_files_detected_recursively() {
        let tmp = std::env::temp_dir().join(format!("rust-feat-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        let nested = tmp.join("nested");
        fs::create_dir_all(&nested).unwrap();

        assert!(!has_feature_files(&tmp), "empty dir has no features");

        fs::write(nested.join("smoke.feature"), "Feature: x").unwrap();
        assert!(has_feature_files(&tmp), "nested .feature file is found");

        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn feature_files_missing_dir_is_false() {
        let missing = std::env::temp_dir().join(format!("rust-nodir-{}", std::process::id()));
        assert!(!has_feature_files(&missing));
    }

    #[test]
    fn crap_score_full_coverage_returns_ccn() {
        // (1-1.0)^3 = 0, so CRAP collapses to CCN.
        assert!((crap_score(10, 1.0) - 10.0).abs() < 1e-9);
    }

    #[test]
    fn crap_score_zero_coverage_is_ccn_squared_plus_ccn() {
        // 10*10*1 + 10 = 110.
        assert!((crap_score(10, 0.0) - 110.0).abs() < 1e-9);
    }

    #[test]
    fn crap_score_half_coverage_uses_cubed_gap() {
        // 100 * 0.125 + 10 = 22.5.
        assert!((crap_score(10, 0.5) - 22.5).abs() < 1e-9);
    }

    #[test]
    fn crap_score_ccn_one_zero_coverage() {
        // 1*1*1 + 1 = 2.
        assert!((crap_score(1, 0.0) - 2.0).abs() < 1e-9);
    }

    #[test]
    fn parse_lcov_str_multi_file() {
        let input = "TN:\n\
                     SF:src/foo.rs\n\
                     DA:1,3\n\
                     DA:2,0\n\
                     DA:5,1\n\
                     end_of_record\n\
                     SF:src/bar.rs\n\
                     DA:10,7\n\
                     end_of_record\n";
        let map = parse_lcov_str(input);
        assert_eq!(map.get("src/foo.rs"), Some(&HashMap::from([(1, 3), (2, 0), (5, 1)])));
        assert_eq!(map.get("src/bar.rs"), Some(&HashMap::from([(10, 7)])));
    }

    #[test]
    fn parse_lizard_csv_row_extracts_location_field() {
        let row =
            r#"7,16,45,2,20,"risky@12-31@src/lib.rs",src/lib.rs,risky,"fn risky(a, b)",12,31"#;
        assert_eq!(
            parse_lizard_csv_row(row),
            Some((16, "risky".to_string(), 12, 31, "src/lib.rs".to_string())),
        );
    }

    #[test]
    fn parse_lizard_csv_row_skips_malformed_rows() {
        assert_eq!(parse_lizard_csv_row("not,csv"), None);
    }

    #[test]
    fn parse_baseline_str_reads_key_values() {
        let parsed = parse_baseline_str(
            "\n# comment\nsuppressions.allow 2\ncoverage.min 50\nmalformed\nbad nope\n",
        );
        assert_eq!(parsed.get("suppressions.allow"), Some(&2));
        assert_eq!(parsed.get("coverage.min"), Some(&50));
        assert!(!parsed.contains_key("bad"));
    }

    // ── Ratcheted baseline merge ─────────────────────────────────────────

    fn baseline_of(pairs: &[(&str, u32)]) -> BaselineMap {
        pairs.iter().map(|(key, value)| ((*key).to_string(), *value)).collect()
    }

    #[test]
    fn merge_baseline_preserves_unknown_keys() {
        let existing = baseline_of(&[("coverage.min", 80), ("mutation.min", 42)]);
        let (merged, dropped) = merge_baseline(
            &existing,
            &baseline_of(&[("suppressions.allow", 3)]),
            &[("complexity.max_violations", Measurement::Value(7))],
        )
        .expect("measurable");
        assert_eq!(merged.get("coverage.min"), Some(&80));
        assert_eq!(merged.get("mutation.min"), Some(&42));
        assert_eq!(merged.get("complexity.max_violations"), Some(&7));
        assert!(dropped.is_empty());
    }

    #[test]
    fn merge_baseline_records_ratcheted_to_zero_kinds_as_zero() {
        // `allow_crate` was 3 and is now gone from the scan: it must be recorded
        // as 0, not carried forward as a stale tolerance.
        let existing = baseline_of(&[("suppressions.allow", 5), ("suppressions.allow_crate", 3)]);
        let (merged, _) =
            merge_baseline(&existing, &baseline_of(&[("suppressions.allow", 2)]), &[])
                .expect("measurable");
        assert_eq!(merged.get("suppressions.allow"), Some(&2));
        assert_eq!(merged.get("suppressions.allow_crate"), Some(&0));
    }

    #[test]
    fn merge_baseline_drops_unavailable_keys() {
        let existing = baseline_of(&[("crap.max_violations", 4)]);
        let (merged, dropped) = merge_baseline(
            &existing,
            &BaselineMap::new(),
            &[("crap.max_violations", Measurement::Unavailable("no coverage tool".to_string()))],
        )
        .expect("measurable");
        assert!(!merged.contains_key("crap.max_violations"));
        assert_eq!(dropped.len(), 1);
        assert!(dropped[0].contains("crap.max_violations"), "{dropped:?}");
        assert!(dropped[0].contains("no coverage tool"), "{dropped:?}");
    }

    #[test]
    fn merge_baseline_is_all_or_nothing_on_a_measurement_error() {
        let existing = baseline_of(&[("coverage.min", 80)]);
        let broken = merge_baseline(
            &existing,
            &baseline_of(&[("suppressions.allow", 9)]),
            &[
                ("complexity.max_violations", Measurement::Value(1)),
                ("crap.max_violations", Measurement::Error("lizard exited 2".to_string())),
            ],
        )
        .expect_err("a measurement error must abort the merge");
        assert_eq!(broken.len(), 1);
        assert_eq!(broken[0].0, "crap.max_violations");
        assert!(broken[0].1.contains("lizard exited 2"));
    }

    #[test]
    fn serialize_baseline_writes_sorted_key_value_lines() {
        let text = serialize_baseline(&baseline_of(&[("coverage.min", 80), ("arch.max", 1)]));
        assert_eq!(text, "arch.max 1\ncoverage.min 80\n");
    }

    // ── Arch violation count ─────────────────────────────────────────────

    const ORPHANS_HEADER: &str = "\n2 orphans found:\n\nwarning: orphaned module `a` at src/a.rs\n";

    #[test]
    fn arch_counts_orphans_from_the_header_line() {
        assert_eq!(parse_orphan_count(ORPHANS_HEADER), Some(2));
    }

    #[test]
    fn arch_counts_zero_orphans_from_the_empty_line() {
        // The phrase is contiguous between cargo-modules' ANSI escapes.
        assert_eq!(parse_orphan_count("\n\u{1b}[1mNo orphans found.\u{1b}[0m\n"), Some(0));
    }

    #[test]
    fn arch_count_is_orphans_plus_one_per_cycle_condition() {
        // Clean tree.
        assert_eq!(arch_violation_count(true, true, "No orphans found."), 0);
        // A cycle counts once: cargo-modules never says how many there are.
        assert_eq!(arch_violation_count(false, true, "No orphans found."), 1);
        // Orphans come from the header, and both conditions add up.
        assert_eq!(arch_violation_count(true, false, ORPHANS_HEADER), 2);
        assert_eq!(arch_violation_count(false, false, ORPHANS_HEADER), 3);
    }

    #[test]
    fn arch_count_falls_back_to_one_when_the_orphan_output_is_unparseable() {
        // A tool failure that prints no count must not read as zero violations.
        assert_eq!(arch_violation_count(true, false, "Error: no projects"), 1);
        // ...but an unparseable success is still zero — nothing was reported.
        assert_eq!(arch_violation_count(true, true, "Error: no projects"), 0);
    }

    #[test]
    fn arch_gate_without_a_floor_is_report_only() {
        let label = arch_description(None, 4);
        assert!(label.contains("report-only: no .harness-baseline floor"), "{label}");
    }

    #[test]
    fn arch_gate_names_the_floor_it_exceeded() {
        assert_eq!(arch_description(Some(0), 0), "Arch (cargo-modules)");
        assert_eq!(arch_description(Some(3), 2), "Arch (cargo-modules, baseline 3)");
        assert_eq!(
            arch_description(Some(1), 4),
            "Arch (cargo-modules): 4 violations exceed baseline 1"
        );
    }

    #[test]
    fn ratcheted_keys_are_unique() {
        let mut keys: Vec<&str> = RATCHETED_KEYS.iter().map(|(key, _)| *key).collect();
        keys.sort_unstable();
        let count = keys.len();
        keys.dedup();
        assert_eq!(keys.len(), count, "duplicate key in RATCHETED_KEYS");
    }

    // ── Coverage floor ───────────────────────────────────────────────────

    #[test]
    fn lcov_line_coverage_truncates_never_rounds() {
        // 2 of 3 lines hit is 66.6%: recorded as 66, because a floor of 67 would
        // fail the very gate that measured it.
        let map =
            HashMap::from([("src/a.rs".to_string(), HashMap::from([(1, 1), (2, 4), (3, 0)]))]);
        assert_eq!(lcov_line_coverage(&map), Some(66));
    }

    #[test]
    fn lcov_line_coverage_without_tracked_lines_is_none() {
        assert_eq!(lcov_line_coverage(&HashMap::new()), None);
    }

    // ── Complexity floor ─────────────────────────────────────────────────

    #[test]
    fn lizard_warning_count_reads_the_summary_row() {
        let stdout = "Total nloc   Avg.NLOC  AvgCCN  Avg.token   Fun Cnt  Warning cnt   Fun Rt   nloc Rt\n                      ---------------------------------------------------------------\n\
                      133       5.9     1.2       47.8       14            3      0.00    0.00\n";
        assert_eq!(lizard_warning_count(stdout), Some(3));
    }

    #[test]
    fn lizard_warning_count_without_a_summary_row_is_none() {
        assert_eq!(lizard_warning_count("nothing to see here\n"), None);
    }

    #[test]
    fn complexity_gate_without_a_floor_is_report_only() {
        let gate = complexity_gate_for(None);
        assert!(gate.description.contains("report-only"), "{}", gate.description);
        assert!(gate.cmd.iter().any(|a| a == &REPORT_ONLY_LIMIT.to_string()));
    }

    #[test]
    fn complexity_gate_passes_the_floor_to_lizard() {
        let gate = complexity_gate_for(Some(4));
        assert!(gate.description.contains("baseline 4"), "{}", gate.description);
        let i_index = gate.cmd.iter().position(|a| a == "-i").expect("-i flag");
        assert_eq!(gate.cmd[i_index + 1], "4");
    }

    #[test]
    fn complexity_gate_at_zero_keeps_the_plain_label() {
        let gate = complexity_gate_for(Some(0));
        assert_eq!(gate.description, "Complexity (lizard)");
    }

    #[test]
    fn complexity_argv_is_identical_apart_from_the_tolerance() {
        let floor = complexity_argv(3);
        let report_only = complexity_argv(REPORT_ONLY_LIMIT);
        assert_eq!(floor.len(), report_only.len());
        let differing: Vec<usize> =
            (0..floor.len()).filter(|&i| floor[i] != report_only[i]).collect();
        assert_eq!(differing.len(), 1, "only `-i N` may differ: {floor:?} vs {report_only:?}");
    }

    #[test]
    fn scan_fixture_dir() {
        let tmp = std::env::temp_dir().join(format!("rust-suppr-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let file = tmp.join("a.rs");
        let mut f = fs::File::create(&file).unwrap();
        writeln!(f, "#[allow(dead_code)]").unwrap();
        writeln!(f, "fn f() {{}}").unwrap();
        writeln!(f, "#![allow(unused_imports)]").unwrap();
        drop(f);
        fs::write(tmp.join("skip.txt"), "#[allow(ignored)]").unwrap();

        let results = scan_suppressions(&[tmp.clone()]);

        assert_eq!(results.get("allow"), Some(&vec![vec!["dead_code".to_string()]]));
        assert_eq!(results.get("allow_crate"), Some(&vec![vec!["unused_imports".to_string()]]));

        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn scan_file_root() {
        let tmp = std::env::temp_dir().join(format!("rust-suppr-file-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let file = tmp.join("single.rs");
        let mut f = fs::File::create(&file).unwrap();
        writeln!(f, "#[allow(dead_code)]").unwrap();
        writeln!(f, "fn f() {{}}").unwrap();
        drop(f);

        let results = scan_suppressions(&[file]);

        assert_eq!(results.get("allow"), Some(&vec![vec!["dead_code".to_string()]]));

        fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn default_suppression_roots_include_harness() {
        let roots = default_suppression_roots();
        assert!(roots.iter().any(|path| path.ends_with("harness.rs")));
    }

    #[test]
    fn parallel_gates_run_all_on_seeded_failure() {
        // Each gate touches the filesystem so we can prove every gate ran even
        // though one fails: a short-circuit would leave a marker missing. The
        // overall result must be false.
        let dir = std::env::temp_dir().join(format!("rust-gates-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let first = dir.join("first");
        let last = dir.join("last");
        let first_s = first.to_string_lossy().into_owned();
        let last_s = last.to_string_lossy().into_owned();
        let gates = vec![
            Gate::new("first", &["touch", first_s.as_str()]),
            Gate::new("seeded fail", &["false"]),
            Gate::new("last", &["touch", last_s.as_str()]),
        ];

        let all_ok = run_gates_parallel(&gates);

        assert!(!all_ok, "a seeded failure makes the whole batch fail");
        assert!(first.exists(), "the gate before the failure ran");
        assert!(last.exists(), "the gate after the failure still ran (no short-circuit)");

        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn parallel_gates_empty_batch_passes() {
        assert!(run_gates_parallel(&[]));
    }

    #[test]
    fn parallel_gates_detailed_reports_failed_descriptions() {
        let gates = vec![Gate::new("ok-gate", &["true"]), Gate::new("fail-gate", &["false"])];
        let failed = run_gates_parallel_detailed(&gates);
        assert_eq!(failed, vec!["fail-gate"]);
    }

    #[test]
    fn sequential_gates_run_all_on_seeded_failure() {
        // Same no-short-circuit contract as the parallel batch (see
        // parallel_gates_run_all_on_seeded_failure above), just executed
        // one gate at a time.
        let dir = std::env::temp_dir().join(format!("rust-seq-gates-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let first = dir.join("first");
        let last = dir.join("last");
        let first_s = first.to_string_lossy().into_owned();
        let last_s = last.to_string_lossy().into_owned();
        let gates = vec![
            Gate::new("first", &["touch", first_s.as_str()]),
            Gate::new("seeded fail", &["false"]),
            Gate::new("last", &["touch", last_s.as_str()]),
        ];

        let all_ok = run_gates_sequential(&gates);

        assert!(!all_ok, "a seeded failure makes the whole batch fail");
        assert!(first.exists(), "the gate before the failure ran");
        assert!(last.exists(), "the gate after the failure still ran (no short-circuit)");

        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn sequential_gates_empty_batch_passes() {
        assert!(run_gates_sequential(&[]));
    }

    // ── changed_rs_files / git porcelain parsing (Fix 1) ────────────────

    #[test]
    fn parse_porcelain_line_skips_deleted_worktree_file() {
        assert_eq!(parse_porcelain_line(" D removed.rs"), None);
    }

    #[test]
    fn parse_porcelain_line_skips_deleted_staged_file() {
        assert_eq!(parse_porcelain_line("D  removed.rs"), None);
    }

    #[test]
    fn parse_porcelain_line_resolves_rename_arrow() {
        assert_eq!(parse_porcelain_line("R  old.rs -> new.rs"), Some("new.rs"));
    }

    #[test]
    fn parse_porcelain_line_plain_modified_path() {
        assert_eq!(parse_porcelain_line(" M src/foo.rs"), Some("src/foo.rs"));
    }

    #[test]
    fn parse_porcelain_line_too_short_is_none() {
        assert_eq!(parse_porcelain_line("M"), None);
        assert_eq!(parse_porcelain_line(""), None);
    }

    #[test]
    fn parse_porcelain_line_multibyte_path_does_not_panic() {
        assert_eq!(parse_porcelain_line("?? caf\u{e9}.rs"), Some("caf\u{e9}.rs"));
    }

    #[test]
    fn parse_changed_rs_files_normalizes_repo_root_relative_path() {
        let porcelain = " M rust/src/foo.rs\n";
        assert_eq!(parse_changed_rs_files(porcelain, "rust"), vec!["src/foo.rs".to_string()]);
    }

    #[test]
    fn parse_changed_rs_files_rejects_sibling_template() {
        // A change in a sibling template (or another crate in a monorepo)
        // must not be mistaken for a change in this one.
        let porcelain = " M python/x.rs\n";
        assert!(parse_changed_rs_files(porcelain, "rust").is_empty());
    }

    #[test]
    fn parse_changed_rs_files_resolves_rename_and_filters_extension() {
        let porcelain = "R  rust/old.rs -> rust/new.rs\nM  rust/README.md\n";
        assert_eq!(parse_changed_rs_files(porcelain, "rust"), vec!["new.rs".to_string()]);
    }

    #[test]
    fn parse_changed_rs_files_skips_deleted_entries() {
        let porcelain = " D rust/gone.rs\n";
        assert!(parse_changed_rs_files(porcelain, "rust").is_empty());
    }

    #[test]
    fn parse_changed_rs_files_empty_prefix_keeps_repo_root_relative_path() {
        // Empty prefix means root() IS the repo root (e.g. the template
        // copied out as its own standalone repo) — nothing to strip.
        let porcelain = " M src/foo.rs\n";
        assert_eq!(parse_changed_rs_files(porcelain, ""), vec!["src/foo.rs".to_string()]);
    }

    // ── restage_fixed_files (Fix 2) ──────────────────────────────────────

    #[test]
    fn existing_paths_filters_out_missing_files() {
        let dir = std::env::temp_dir().join(format!("rust-existing-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("present.rs"), "").unwrap();

        let files = vec!["present.rs".to_string(), "missing.rs".to_string()];
        assert_eq!(existing_paths(&dir, &files), vec!["present.rs"]);

        fs::remove_dir_all(&dir).unwrap();
    }

    // ── lint_gate --locked (Fix 6) ───────────────────────────────────────

    #[test]
    fn lint_gate_uses_locked_flag_for_lockfile_freshness() {
        let gate = lint_gate();
        assert!(gate.cmd.iter().any(|a| a == "--locked"));
    }

    // ── --min= parsing (Fix 7) ───────────────────────────────────────────

    #[test]
    fn parse_coverage_min_accepts_valid_integer() {
        assert_eq!(parse_coverage_min("50"), Ok(50));
    }

    #[test]
    fn parse_coverage_min_rejects_non_integer() {
        assert!(parse_coverage_min("abc").is_err());
    }

    #[test]
    fn parse_coverage_min_rejects_negative() {
        assert!(parse_coverage_min("-5").is_err());
    }

    // ── arch-config-guard base-ref fallback (Fix 5) ──────────────────────

    #[test]
    fn pick_default_base_ref_prefers_origin_head() {
        assert_eq!(
            pick_default_base_ref(|c| matches!(c, "origin/HEAD" | "origin/main" | "main")),
            Some("origin/HEAD")
        );
    }

    #[test]
    fn pick_default_base_ref_falls_back_to_origin_main() {
        assert_eq!(
            pick_default_base_ref(|c| matches!(c, "origin/main" | "main")),
            Some("origin/main")
        );
    }

    #[test]
    fn pick_default_base_ref_falls_back_to_main() {
        assert_eq!(pick_default_base_ref(|c| c == "main"), Some("main"));
    }

    #[test]
    fn pick_default_base_ref_none_when_nothing_resolves() {
        assert_eq!(pick_default_base_ref(|_| false), None);
    }

    // ── Gherkin-first guard (Change 2) ───────────────────────────────────

    #[test]
    fn is_production_source_recognizes_src_rs_files() {
        assert!(is_production_source("src/lib.rs"));
        assert!(is_production_source("src/main.rs"));
        assert!(is_production_source("src/nested/mod.rs"));
    }

    #[test]
    fn is_production_source_excludes_harness_rs() {
        assert!(!is_production_source("harness.rs"));
    }

    #[test]
    fn is_production_source_excludes_tests_dir() {
        assert!(!is_production_source("tests/acceptance.rs"));
    }

    #[test]
    fn is_production_source_excludes_non_rust_files_under_src() {
        assert!(!is_production_source("src/data.json"));
    }

    #[test]
    fn gherkin_guard_triggers_on_production_source_without_feature() {
        let changed = vec!["src/lib.rs".to_string()];
        assert_eq!(gherkin_guard_decision(true, &changed), Some(true));
    }

    #[test]
    fn gherkin_guard_passes_when_a_feature_file_changed_too() {
        let changed = vec!["src/lib.rs".to_string(), "tests/features/x.feature".to_string()];
        assert_eq!(gherkin_guard_decision(true, &changed), Some(false));
    }

    #[test]
    fn gherkin_guard_passes_when_no_production_source_changed() {
        let changed = vec!["README.md".to_string(), "tests/acceptance.rs".to_string()];
        assert_eq!(gherkin_guard_decision(true, &changed), Some(false));
    }

    #[test]
    fn gherkin_guard_skips_when_no_feature_files_exist_anywhere() {
        // has_features=false models a repo with zero .feature files under
        // tests/features/ — the rule must not apply yet, regardless of what
        // else changed.
        let changed = vec!["src/lib.rs".to_string()];
        assert_eq!(gherkin_guard_decision(false, &changed), None);
    }

    #[test]
    fn gherkin_guard_override_env_value_one_passes() {
        assert!(gherkin_guard_overridden(Some("1")));
    }

    #[test]
    fn gherkin_guard_override_rejects_other_values() {
        assert!(!gherkin_guard_overridden(Some("0")));
        assert!(!gherkin_guard_overridden(Some("true")));
        assert!(!gherkin_guard_overridden(None));
    }

    // ── CRAP advisory glyph (Change 3) ───────────────────────────────────

    #[test]
    fn crap_status_glyph_advisory_uses_green_warn_glyph() {
        assert_eq!(crap_status_glyph(false), (GREEN, "\u{26a0}", " (advisory)"));
    }

    #[test]
    fn crap_status_glyph_enforce_uses_red_cross_glyph() {
        assert_eq!(crap_status_glyph(true), (RED, "\u{2717}", ""));
    }
}

// Property-based tests for the pure helpers above.
//
// Worked example for the template's PBT convention: law-like behavior
// (formulas, parsers, round-trips) gets a property, not just examples.
// Examples pin known cases; properties pin the law.
#[cfg(test)]
mod property_tests {
    use std::fmt::Write as _;

    use proptest::prelude::*;

    use super::*;

    proptest! {
        #[test]
        fn crap_score_full_coverage_collapses_to_ccn(ccn in 1u32..=100) {
            prop_assert!((crap_score(ccn, 1.0) - f64::from(ccn)).abs() < 1e-9);
        }

        #[test]
        fn crap_score_bounded_by_ccn_and_zero_coverage(ccn in 1u32..=100, cov in 0.0f64..=1.0) {
            let score = crap_score(ccn, cov);
            prop_assert!(score >= f64::from(ccn) - 1e-9);
            prop_assert!(score <= f64::from(ccn * ccn + ccn) + 1e-9);
        }

        #[test]
        fn crap_score_more_coverage_never_raises(
            ccn in 1u32..=100,
            a in 0.0f64..=1.0,
            b in 0.0f64..=1.0,
        ) {
            let (lo, hi) = if a <= b { (a, b) } else { (b, a) };
            prop_assert!(crap_score(ccn, lo) >= crap_score(ccn, hi) - 1e-9);
        }

        #[test]
        fn crap_score_more_complexity_never_lowers(
            a in 1u32..=100,
            b in 1u32..=100,
            cov in 0.0f64..=1.0,
        ) {
            let (lo, hi) = if a <= b { (a, b) } else { (b, a) };
            prop_assert!(crap_score(lo, cov) <= crap_score(hi, cov) + 1e-9);
        }

        #[test]
        fn suppressions_total_on_arbitrary_text(line in ".*") {
            for (kind, _rules) in parse_line_for_suppressions(&line) {
                prop_assert!(kind == "allow" || kind == "allow_crate");
            }
        }

        #[test]
        fn suppressions_no_hash_means_no_match(line in "[^#]*") {
            prop_assert!(parse_line_for_suppressions(&line).is_empty());
        }

        #[test]
        fn suppressions_allow_rules_round_trip(
            rules in prop::collection::vec("[a-z][a-z0-9_]{0,8}", 1..4),
        ) {
            let line = format!("#[allow({})]", rules.join(", "));
            let parsed = parse_line_for_suppressions(&line);
            prop_assert_eq!(parsed, vec![("allow".to_string(), rules)]);
        }

        #[test]
        fn lcov_generated_input_round_trips(
            cov in prop::collection::hash_map(
                "[a-z][a-z0-9_/.-]{0,12}",
                prop::collection::hash_map(1u32..10_000, 0u32..1_000, 1..10),
                1..5,
            ),
        ) {
            let mut text = String::from("TN:\n");
            for (file, lines) in &cov {
                writeln!(text, "SF:{file}").unwrap();
                for (ln, hits) in lines {
                    writeln!(text, "DA:{ln},{hits}").unwrap();
                }
                text.push_str("end_of_record\n");
            }
            prop_assert_eq!(parse_lcov_str(&text), cov);
        }

        #[test]
        fn lcov_total_on_arbitrary_text(text in ".*") {
            // Never panics; every file entry has a line map.
            let _map = parse_lcov_str(&text);
        }

        #[test]
        fn porcelain_line_never_panics_on_arbitrary_text(line in ".*") {
            // Guards the byte-index-3 slice: must never panic on a
            // multibyte path, a too-short line, or any other garbage.
            let _ = parse_porcelain_line(&line);
        }

        #[test]
        fn changed_rs_files_never_panics_on_arbitrary_porcelain(
            porcelain in ".*",
            prefix in "[a-z][a-z0-9_/-]{0,12}",
        ) {
            let _ = parse_changed_rs_files(&porcelain, &prefix);
        }

        #[test]
        fn is_production_source_never_panics_on_arbitrary_text(path in ".*") {
            let _ = is_production_source(&path);
        }

        #[test]
        fn is_production_source_recognizes_generated_src_rs_paths(
            name in "[a-z][a-z0-9_]{0,12}",
        ) {
            let path = format!("src/{name}.rs");
            prop_assert!(is_production_source(&path));
        }

        #[test]
        fn is_production_source_rejects_paths_outside_src(
            dir in "[a-z][a-z0-9_]{0,12}",
            name in "[a-z][a-z0-9_]{0,12}",
        ) {
            prop_assume!(dir != "src");
            let path = format!("{dir}/{name}.rs");
            prop_assert!(!is_production_source(&path));
        }

        #[test]
        fn gherkin_guard_triggered_never_panics_on_arbitrary_paths(
            paths in prop::collection::vec(".*", 0..6),
        ) {
            let _ = gherkin_guard_triggered(&paths);
        }
    }
}
