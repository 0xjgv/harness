//! Project development tasks. Zero dependencies — std only.
//!
//! Usage: cargo harness <command> [--verbose]

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::env;
use std::fs;
use std::io::{self, IsTerminal, Read, Write as _};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Output, Stdio};
use std::time::{Duration, Instant};

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
const ARCH_CONFIG_ALLOW_ENV: &str = "HARNESS_ALLOW_ARCH_CONFIG";
const PROTECTED_BRANCHES: [&str; 2] = ["main", "master"];
const PROTECTED_PUSH_ALLOW_ENV: &str = "HARNESS_ALLOW_PROTECTED_PUSH";
const PRE_PUSH_REFS_ENV: &str = "HARNESS_PRE_PUSH_REFS";
const ARCH_BASE_ENV: &str = "HARNESS_ARCH_BASE";
const LIZARD: &str = "lizard@1.22.2";
const COMPLEXITY_TARGETS: [&str; 2] = ["src", "tests"];
/// lizard's limits as (finding label, flag, max): CCN, parameters, function length.
const COMPLEXITY_LIMITS: [(&str, &str, u32); 3] =
    [("CCN", "-C", 15), ("args", "-a", 8), ("length", "-L", 100)];
/// Finding lines a stop-hook payload carries; the rest are one command away.
const HOOK_FINDING_LIMIT: usize = 20;

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
    description: &'static str,
    cmd: Vec<String>,
    extract: Option<fn(&str) -> Option<String>>,
    hint: Option<&'static str>,
    /// Run without the `GIT_*` variables git exports to hooks.
    without_git_env: bool,
}

impl Gate {
    fn new(description: &'static str, cmd: &[&str]) -> Self {
        Self {
            description,
            cmd: cmd.iter().map(|&s| s.to_string()).collect(),
            extract: None,
            hint: None,
            without_git_env: false,
        }
    }

    const fn with_hint(mut self, hint: &'static str) -> Self {
        self.hint = Some(hint);
        self
    }
}

/// Clear the command's environment down to this one minus every `GIT_*` variable.
///
/// git exports `GIT_DIR` (and, for commits, `GIT_INDEX_FILE`) to hooks; a child that
/// runs git elsewhere — a test that `git init`s a temp dir — would otherwise write
/// into this repository.
fn strip_git_env(cmd: &mut Command) -> &mut Command {
    cmd.env_clear();
    for (key, value) in env::vars_os() {
        if !key.to_string_lossy().starts_with("GIT_") {
            cmd.env(key, value);
        }
    }
    cmd
}

struct GateResult {
    description: &'static str,
    cmd: Vec<String>,
    ok: bool,
    exit_code: i32,
    output: String,
    detail: Option<String>,
    hint: Option<&'static str>,
}

/// Run a gate's command with output captured (no printing, no exit): the
/// thread-safe unit the parallel batch spawns.
fn run_capture(gate: &Gate) -> GateResult {
    let program = &gate.cmd[0];
    let args: Vec<&str> = gate.cmd[1..].iter().map(String::as_str).collect();
    let mut cmd = Command::new(program);
    if gate.without_git_env {
        strip_git_env(&mut cmd);
    }
    let result =
        cmd.args(&args).current_dir(root()).stdout(Stdio::piped()).stderr(Stdio::piped()).output();
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
                description: gate.description,
                cmd: gate.cmd.clone(),
                ok,
                exit_code: output.status.code().unwrap_or(1),
                output: combined,
                detail,
                hint: gate.hint,
            }
        }
        Err(e) => GateResult {
            description: gate.description,
            cmd: gate.cmd.clone(),
            ok: false,
            exit_code: 1,
            output: format!("Failed to execute {program}: {e}"),
            detail: None,
            hint: gate.hint,
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
        return true;
    }
    println!("  {RED}\u{2717}{RESET} {}", result.description);
    if !is_verbose() && !result.output.is_empty() {
        print!("{}", result.output);
    }
    if let Some(hint) = result.hint {
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
fn run_gates_parallel(gates: &[Gate]) -> bool {
    if gates.is_empty() {
        return true;
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
    let mut all_ok = true;
    for result in &results {
        if !print_gate_result(result, true) {
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

fn suppression_counts(results: &SuppressionCounts) -> BTreeMap<String, u32> {
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

fn parse_baseline_str(text: &str) -> BTreeMap<String, u32> {
    let mut values = BTreeMap::new();
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

fn read_baseline() -> Option<BTreeMap<String, u32>> {
    let text = fs::read_to_string(root().join(BASELINE_FILE)).ok()?;
    Some(parse_baseline_str(&text))
}

fn coverage_min_default() -> u32 {
    if let Some(value) = arg_value("--min").and_then(|v| v.parse::<u32>().ok()) {
        return value;
    }
    read_baseline().and_then(|b| b.get("coverage.min").copied()).unwrap_or(0)
}

fn write_baseline(results: &SuppressionCounts) -> std::io::Result<()> {
    let coverage_min = read_baseline().and_then(|b| b.get("coverage.min").copied()).unwrap_or(0);
    let counts = suppression_counts(results);
    let mut lines: Vec<String> =
        counts.iter().map(|(key, count)| format!("{key} {count}")).collect();
    lines.push(format!("coverage.min {coverage_min}"));
    fs::write(root().join(BASELINE_FILE), format!("{}\n", lines.join("\n")))
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

fn is_rs_path(path: &str) -> bool {
    Path::new(path).extension().is_some_and(|ext| ext.eq_ignore_ascii_case("rs"))
}

/// A `.rs` file of this crate: anything but build output.
fn is_project_rs_file(path: &str) -> bool {
    is_rs_path(path) && path != "target" && !path.starts_with("target/")
}

/// Project `.rs` files with uncommitted changes (untracked included), relative to this crate.
fn changed_rs_files() -> Vec<String> {
    let scope = changed_scope(head_commit().as_deref()).unwrap_or_default();
    scope.into_keys().filter(|path| is_project_rs_file(path)).collect()
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
fn lint_gate() -> Gate {
    Gate::new("Clippy (strict)", &["cargo", "clippy", "--", "-D", "warnings"])
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

/// Format `.rs` files with uncommitted changes; `--hook`: the file a hook event names.
///
/// rustfmt only: `cargo clippy --fix` rewrites the whole crate, so it stays in
/// `check` / `fix` / `pre-commit`.
fn cmd_post_edit() {
    if arg_flag("--hook") {
        post_edit_hook();
        return;
    }
    let files = changed_rs_files();
    if files.is_empty() {
        return;
    }
    let failed = format_files(&files);
    if failed.is_empty() {
        println!("  {GREEN}\u{2713}{RESET} Format ({} changed file(s))", files.len());
    } else {
        println!("  {RED}\u{26a0}{RESET} rustfmt could not format: {}", failed.join(", "));
    }
}

/// Post-edit, then changed-lines lint and touched over-limit functions.
///
/// Silent on success. Findings exit 2 with a capped stderr payload; a tool that could
/// not run exits 1; findings on a stop the agent is already continuing from exit 1
/// (loop guard). `check` and `ci` keep the whole-tree gates.
fn cmd_stop_hook() {
    let event = hook_event(); // stdin belongs to the hook event; read it before anything else
    format_files(&changed_rs_files());
    sync_agents_md_after_edit();
    let base = delta_base();
    let scope = changed_scope(base.as_deref()).unwrap_or_else(|reason| {
        eprintln!("stop-hook: changed lines could not run: {reason}");
        std::process::exit(1);
    });
    let code = report_stop_hook(&run_delta_gates(&scope), &event);
    if is_verbose() && code == 0 {
        let against = base.as_deref().unwrap_or("no commits");
        println!("stop-hook: clean ({} changed path(s) vs {against})", scope.len());
    }
    if code != 0 {
        std::process::exit(code);
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

/// Run architecture checks via cargo-modules against `arch.toml`.
///
/// Rust's compiler enforces visibility and crate layering but NOT freedom
/// from circular dependencies between modules of one crate, nor the absence
/// of orphaned (unlinked) source files. Those are the invariants this gate
/// checks. `arch.toml` is guarded by `arch-config-guard`, which blocks
/// integration until the change is reviewed. Absent config → skip.
fn arch_gates_or_warn() -> Vec<Gate> {
    if !root().join("arch.toml").exists() {
        println!("  {GREEN}\u{26a0}{RESET} Arch: no arch.toml \u{2014} skipped");
        return Vec::new();
    }
    if !tool_installed("modules") {
        println!("  {DIM}\u{2298} Arch skipped (install: cargo install cargo-modules){RESET}");
        return Vec::new();
    }
    vec![
        Gate::new(
            "Arch: no module cycles",
            &["cargo", "modules", "dependencies", "--lib", "--no-externs", "--acyclic"],
        )
        .with_hint(
            "boundary crossed; surface the design decision to the human; don't edit arch config",
        ),
        Gate::new("Arch: no orphan files", &["cargo", "modules", "orphans", "--lib"]).with_hint(
            "boundary crossed; surface the design decision to the human; don't edit arch config",
        ),
    ]
}

fn cmd_arch() {
    for gate in arch_gates_or_warn() {
        print_gate_result(&run_capture(&gate), false);
    }
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

fn changed_paths_from_base() -> Vec<String> {
    let mut bases: Vec<String> = Vec::new();
    if let Ok(base) = env::var(ARCH_BASE_ENV)
        && !base.is_empty()
    {
        bases.push(base);
    }
    if let Ok(github_base) = env::var("GITHUB_BASE_REF")
        && !github_base.is_empty()
    {
        bases.push(format!("origin/{github_base}"));
    }

    let mut paths = Vec::new();
    for base in bases {
        if git_lines(&["rev-parse", "--verify", &base]).is_empty() {
            continue;
        }
        paths.extend(git_lines(&["diff", "--name-only", &format!("{base}...HEAD"), "--", "."]));
    }
    paths
}

/// What the push destinations are, resolved once per process.
///
/// `Refs` carries `<local ref> <local sha> <remote ref> <remote sha>` lines.
/// `Incomplete` means data arrived but the read never reached EOF within the
/// deadline: guessing the destinations from a truncated list would be worse
/// than refusing, so both guards fail on it.
enum PrePushRefs {
    None,
    Refs(String),
    Incomplete,
}

/// Push destinations, read at most once: `HARNESS_PRE_PUSH_REFS` first (set by
/// dispatchers whose children never see the hook's stdin), then git pre-push
/// stdin, which only the first reader could consume.
fn pre_push_refs() -> &'static PrePushRefs {
    static REFS: std::sync::OnceLock<PrePushRefs> = std::sync::OnceLock::new();
    REFS.get_or_init(|| {
        if let Ok(text) = env::var(PRE_PUSH_REFS_ENV)
            && !text.trim().is_empty()
        {
            return PrePushRefs::Refs(text);
        }
        if io::stdin().is_terminal() {
            return PrePushRefs::None;
        }
        read_until(io::stdin(), Duration::from_secs(1))
    })
}

/// Read `source` to EOF on a detached thread, bounding the whole read by `deadline`.
/// Returns what arrived and whether EOF did.
///
/// Run from a CI job or an agent tool, stdin is a pipe nobody ever writes to
/// and a blocking read would hang the command; the deadline bounds that. Chunks
/// are forwarded as they arrive so a deadline hit keeps what was read.
fn read_with_deadline<R: Read + Send + 'static>(
    mut source: R,
    deadline: Duration,
) -> (Vec<u8>, bool) {
    let (sender, receiver) = std::sync::mpsc::channel::<Vec<u8>>();
    std::thread::spawn(move || {
        let mut buffer = [0u8; 4096];
        while let Ok(read) = source.read(&mut buffer) {
            if read == 0 || sender.send(buffer[..read].to_vec()).is_err() {
                break;
            }
        }
    });

    let start = Instant::now();
    let mut input = Vec::new();
    loop {
        let left = deadline.checked_sub(start.elapsed()).unwrap_or_default();
        match receiver.recv_timeout(left) {
            Ok(chunk) => input.extend_from_slice(&chunk),
            // Sender dropped: the reader hit EOF, so the input is whole.
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => return (input, true),
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => return (input, false),
        }
    }
}

/// The pre-push refs in `source`: a deadline hit tells "nothing came" (no refs)
/// from "some came" (incomplete) instead of guessing from a truncated list.
fn read_until<R: Read + Send + 'static>(source: R, deadline: Duration) -> PrePushRefs {
    let (received, complete) = read_with_deadline(source, deadline);
    if !complete {
        return if received.is_empty() { PrePushRefs::None } else { PrePushRefs::Incomplete };
    }
    let text = String::from_utf8_lossy(&received).into_owned();
    if text.trim().is_empty() { PrePushRefs::None } else { PrePushRefs::Refs(text) }
}

/// Base commit for a push that creates a branch on the remote (zero remote sha):
/// the merge-base with the first upstream ref that exists, so the guard sees the
/// whole branch instead of only its tip commit.
fn new_branch_base(local_sha: &str) -> Option<String> {
    let mut candidates = vec!["origin/main".to_string(), "origin/master".to_string()];
    if let Ok(base) = env::var(ARCH_BASE_ENV)
        && !base.is_empty()
    {
        candidates.push(base);
    }
    for candidate in candidates {
        if git_lines(&["rev-parse", "--verify", &candidate]).is_empty() {
            continue;
        }
        if let Some(merge_base) =
            git_lines(&["merge-base", &candidate, local_sha]).into_iter().next()
        {
            return Some(merge_base);
        }
    }
    None
}

fn changed_paths_from_pre_push_refs(text: &str) -> Vec<String> {
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
            if let Some(base) = new_branch_base(local_sha) {
                paths.extend(git_lines(&[
                    "diff",
                    "--name-only",
                    &format!("{base}..{local_sha}"),
                    "--",
                    ".",
                ]));
            } else {
                paths.extend(git_lines(&[
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    local_sha,
                    "--",
                    ".",
                ]));
            }
        } else {
            paths.extend(git_lines(&["diff", "--name-only", remote_sha, local_sha, "--", "."]));
        }
    }
    paths
}

fn changed_arch_configs(staged: bool, refs: Option<&str>) -> Vec<String> {
    let mut paths = Vec::new();
    if staged {
        paths.extend(git_lines(&["diff", "--cached", "--name-only", "--", "."]));
    } else {
        paths.extend(git_lines(&["diff", "--name-only", "--", "."]));
        paths.extend(git_lines(&["diff", "--cached", "--name-only", "--", "."]));
        paths.extend(git_lines(&["ls-files", "--others", "--exclude-standard", "--", "."]));
        paths.extend(changed_paths_from_base());
    }
    if let Some(text) = refs {
        paths.extend(changed_paths_from_pre_push_refs(text));
    }

    let prefix = git_prefix();
    let changed: BTreeSet<String> = paths
        .into_iter()
        .map(|p| normalize_changed_path(&p, &prefix))
        .filter(|p| p.as_str() == ARCH_CONFIG)
        .collect();
    changed.into_iter().collect()
}

fn check_arch_config_guard(warn_only: bool, staged: bool, include_pre_push_refs: bool) -> bool {
    let refs = if include_pre_push_refs { pre_push_refs() } else { &PrePushRefs::None };
    if matches!(refs, PrePushRefs::Incomplete) {
        print_incomplete_refs();
        return false;
    }
    let text = match refs {
        PrePushRefs::Refs(text) => Some(text.as_str()),
        _ => None,
    };
    let changed = changed_arch_configs(staged, text);
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
    if !check_arch_config_guard(arg_flag("--warn"), arg_flag("--staged"), arg_flag("--pre-push")) {
        std::process::exit(1);
    }
}

/// Branch a push would land on, when that branch is protected.
///
/// `refs` is the pre-push ref list; empty when there is none. Deleting a
/// protected branch counts as targeting it. Only `refs/heads/` matches, so tag
/// pushes never trip the guard. Well-formed records (any line with 4+ fields,
/// tags included) take precedence once present; `current_branch` decides only
/// when no line reaches that threshold at all (missing or malformed refs).
fn protected_push_target(refs: &str, current_branch: &str) -> Option<String> {
    for line in refs.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 4 {
            continue;
        }
        let Some(branch) = parts[2].strip_prefix("refs/heads/") else { continue };
        if PROTECTED_BRANCHES.contains(&branch) {
            return Some(branch.to_string());
        }
    }
    if refs.lines().any(|line| line.split_whitespace().count() >= 4) {
        return None;
    }
    PROTECTED_BRANCHES.contains(&current_branch).then(|| current_branch.to_string())
}

fn current_branch() -> String {
    git_lines(&["rev-parse", "--abbrev-ref", "HEAD"]).into_iter().next().unwrap_or_default()
}

fn protected_push_allowed() -> bool {
    env::var(PROTECTED_PUSH_ALLOW_ENV).as_deref() == Ok("1")
}

fn print_incomplete_refs() {
    // Both guards consume the same refs; the operator needs the reason once.
    static PRINTED: std::sync::OnceLock<()> = std::sync::OnceLock::new();
    if PRINTED.set(()).is_err() {
        return;
    }
    println!("  {RED}\u{2717}{RESET} Pre-push refs incomplete after 1s");
    println!("  \u{21b3} fix: feed the hook's refs on stdin or set {PRE_PUSH_REFS_ENV}");
}

fn check_branch_guard(refs: &PrePushRefs, allow_protected: bool) -> bool {
    let (text, fallback) = match refs {
        PrePushRefs::Incomplete => {
            print_incomplete_refs();
            return false;
        }
        PrePushRefs::Refs(text) => (text.as_str(), current_branch()),
        PrePushRefs::None => ("", current_branch()),
    };
    let Some(branch) = protected_push_target(text, &fallback) else {
        println!("  {GREEN}\u{2713}{RESET} Branch guard");
        return true;
    };
    if allow_protected {
        println!("  {GREEN}\u{26a0}{RESET} Branch guard override: {branch}");
        return true;
    }
    println!("  {RED}\u{2717}{RESET} Push targets protected branch: {branch}");
    println!(
        "  \u{21b3} fix: push a feature branch and open a PR; humans may set {PROTECTED_PUSH_ALLOW_ENV}=1"
    );
    false
}

fn cmd_branch_guard() {
    if !check_branch_guard(pre_push_refs(), protected_push_allowed()) {
        std::process::exit(1);
    }
}

/// Run lizard as a cyclomatic-complexity gate. Mirrors bun/python invocation.
fn complexity_gate() -> Gate {
    let limits = COMPLEXITY_LIMITS.map(|(_, flag, max)| format!("{flag}{max}"));
    let mut cmd = vec!["uvx", LIZARD, "-l", "rust"];
    cmd.extend(COMPLEXITY_TARGETS);
    cmd.extend(limits.iter().map(String::as_str));
    cmd.extend(["-i", "0"]);
    Gate::new("Complexity (lizard)", &cmd).with_hint(
        "extract helpers or flatten branches until CCN <= 15; do not raise the threshold",
    )
}

fn cmd_complexity() {
    print_gate_result(&run_capture(&complexity_gate()), false);
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
fn cmd_crap() {
    let max_crap: f64 = arg_value("--max").and_then(|v| v.parse::<f64>().ok()).unwrap_or(30.0);
    let enforce = arg_flag("--enforce");

    if !tool_installed("llvm-cov") {
        println!("  {DIM}\u{2298} CRAP skipped (install: cargo install cargo-llvm-cov){RESET}");
        return;
    }

    let lcov_path = root().join("target").join("llvm-cov").join("lcov.info");
    if !lcov_path.exists() || !lcov_is_fresh(&lcov_path, &["src", "tests"]) {
        if let Some(parent) = lcov_path.parent()
            && let Err(e) = fs::create_dir_all(parent)
        {
            println!("  {RED}\u{2717}{RESET} CRAP: cannot create {}: {e}", parent.display());
            std::process::exit(1);
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
            println!("  {RED}\u{2717}{RESET} CRAP: could not produce {}", lcov_path.display());
            std::process::exit(1);
        }
    }

    let Some(cov_map) = parse_lcov(&lcov_path) else {
        println!("  {RED}\u{2717}{RESET} CRAP: failed to read {}", lcov_path.display());
        std::process::exit(1);
    };

    let lz_output = Command::new("uvx")
        .args([LIZARD, "-l", "rust", "src", "--csv"])
        .current_dir(root())
        .output();

    let lz_stdout = match lz_output {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).into_owned(),
        Ok(o) => {
            // Lizard ran but exited non-zero. Trusting partial output would
            // print a green ✓ while leaving high-CCN functions unscored;
            // surface the failure and degrade to advisory unless --enforce.
            let suffix = if enforce { "" } else { " (advisory)" };
            println!("  {RED}\u{2717}{RESET} CRAP: lizard exited {:?}{suffix}", o.status.code());
            if !o.stderr.is_empty() {
                print!("{}", String::from_utf8_lossy(&o.stderr));
            }
            if enforce {
                std::process::exit(o.status.code().unwrap_or(1));
            }
            return;
        }
        Err(e) => {
            let suffix = if enforce { "" } else { " (advisory)" };
            println!("  {RED}\u{2717}{RESET} CRAP: failed to run lizard: {e}{suffix}");
            if enforce {
                std::process::exit(1);
            }
            return;
        }
    };

    // cargo-llvm-cov writes absolute paths in `SF:` lines while lizard emits
    // paths relative to cwd. Build both key shapes for the join — using only
    // the relative form would silently score every function as cov=0.
    let abs_root = root().to_string_lossy().into_owned();
    let mut offenders: Vec<CrapFn> = Vec::new();
    for row in lz_stdout.lines() {
        let Some(parsed) = parse_lizard_csv_row(row) else { continue };
        let ([ccn, ..], name, start, end, path) = parsed;
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

    if offenders.is_empty() {
        println!("  {GREEN}\u{2713}{RESET} CRAP: all functions below {max_crap:.0}");
        return;
    }
    offenders.sort_by(|a, b| b.crap.partial_cmp(&a.crap).unwrap_or(std::cmp::Ordering::Equal));
    let suffix = if enforce { "" } else { " (advisory)" };
    println!(
        "  {RED}\u{2717}{RESET} CRAP: {} function(s) exceed {max_crap:.0}{suffix}",
        offenders.len()
    );
    for o in offenders.iter().take(20) {
        println!(
            "    CRAP={:6.1}  CCN={:3}  cov={:5.1}%  {}",
            o.crap,
            o.ccn,
            o.cov * 100.0,
            o.location
        );
    }
    if enforce {
        std::process::exit(1);
    }
}

/// True when `lcov_path` is at least as new as every `.rs` file under `src_dirs`.
///
/// Used by `cmd_crap` to detect "I edited source but did not re-run coverage"
/// staleness: scoring fresh complexity data against an old LCOV silently
/// misattributes coverage. Returns false (force regeneration) on any I/O or
/// metadata error so the safe path is to re-run, not to trust stale data.
fn lcov_is_fresh(lcov_path: &Path, src_dirs: &[&str]) -> bool {
    let Ok(lcov_meta) = fs::metadata(lcov_path) else { return false };
    let Ok(lcov_mtime) = lcov_meta.modified() else { return false };
    for dir in src_dirs {
        let dir_path = root().join(dir);
        let mut stack = vec![dir_path];
        while let Some(p) = stack.pop() {
            let Ok(entries) = fs::read_dir(&p) else { continue };
            for entry in entries.flatten() {
                let path = entry.path();
                let Ok(ft) = entry.file_type() else { continue };
                if ft.is_dir() {
                    stack.push(path);
                    continue;
                }
                if path.extension().is_some_and(|e| e == "rs")
                    && let Ok(meta) = path.metadata()
                    && let Ok(mtime) = meta.modified()
                    && mtime > lcov_mtime
                {
                    return false;
                }
            }
        }
    }
    true
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

/// Parse one `lizard --csv` row into ([ccn, params, length], name, start, end, path).
///
/// Lizard columns: nloc,ccn,token,param,length,location,file,name,sig,start,end.
/// The location column is the only one whose value is self-contained:
/// `"name@start-end@path"`. Signatures can contain commas, so we extract
/// the location field directly rather than splitting the whole row.
fn parse_lizard_csv_row(row: &str) -> Option<([u32; 3], String, u32, u32, String)> {
    let mut iter = row.splitn(6, ',');
    let _nloc = iter.next()?;
    let ccn: u32 = iter.next()?.parse().ok()?;
    let _token = iter.next()?;
    let args: u32 = iter.next()?.parse().ok()?;
    let length: u32 = iter.next()?.parse().ok()?;
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
    Some(([ccn, args, length], name, start, end, path))
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

// ── Agent hooks ─────────────────────────────────────────────────────
// The stop hook runs after every agent turn and judges the change, not the tree:
// lint left on changed lines, and over-limit functions the change touched. There is
// no dead-code gate: rustc's `dead_code` reaches the agent through the lint. The
// whole-tree gates stay in check / ci / pre-push. Exit contract: silent 0 when clean,
// 2 with a stderr payload the agent reads, 1 when a tool could not run.

/// Changed lines per path relative to this crate: inclusive (start, end) spans.
type Scope = BTreeMap<String, Vec<(usize, usize)>>;

const WHOLE_FILE: (usize, usize) = (1, usize::MAX);

/// One delta gate: findings block the stop; an error means its tool failed.
struct DeltaResult {
    gate: &'static str,
    outcome: Result<Vec<String>, String>,
}

/// A tool's captured output; Err only when it cannot start.
fn tool_output(tool: &str, cmd: &[&str]) -> Result<Output, String> {
    Command::new(cmd[0])
        .args(&cmd[1..])
        .current_dir(root())
        .output()
        .map_err(|e| format!("{tool} not runnable: {e}"))
}

/// The tool's stdout when it succeeded; otherwise why not, from its first `error`
/// line or else its last line.
fn expect_success(tool: &str, output: &Output) -> Result<String, String> {
    if output.status.success() {
        return Ok(String::from_utf8_lossy(&output.stdout).into_owned());
    }
    let stderr = String::from_utf8_lossy(&output.stderr);
    let detail =
        if stderr.trim().is_empty() { String::from_utf8_lossy(&output.stdout) } else { stderr };
    let lines: Vec<&str> = detail.lines().map(str::trim).filter(|line| !line.is_empty()).collect();
    let reason = format!("{tool} exited {}", output.status.code().unwrap_or(-1));
    Err(match lines.iter().find(|line| line.starts_with("error")).or_else(|| lines.last()) {
        Some(line) => format!("{reason}: {line}"),
        None => reason,
    })
}

fn run_tool(tool: &str, cmd: &[&str]) -> Result<String, String> {
    expect_success(tool, &tool_output(tool, cmd)?)
}

fn git_output(args: &[&str]) -> Result<String, String> {
    let mut cmd = vec!["git", "-c", "core.quotePath=false"];
    cmd.extend(args);
    run_tool("git", &cmd)
}

// ── Changed lines ──

/// The first base ref that resolves: env overrides, then the usual default branches.
/// Never fetched — a hook must not touch the network.
fn base_ref() -> Option<String> {
    let mut candidates = vec![env::var(ARCH_BASE_ENV).unwrap_or_default()];
    if let Ok(github_base) = env::var("GITHUB_BASE_REF")
        && !github_base.is_empty()
    {
        candidates.push(format!("origin/{github_base}"));
    }
    candidates.extend(
        ["origin/HEAD", "origin/main", "origin/master", "main", "master"].map(String::from),
    );
    candidates.into_iter().find(|candidate| {
        !candidate.is_empty()
            && !git_lines(&["rev-parse", "--verify", "--quiet", &format!("{candidate}^{{commit}}")])
                .is_empty()
    })
}

fn head_commit() -> Option<String> {
    git_lines(&["rev-parse", "--verify", "--quiet", "HEAD"]).into_iter().next()
}

/// merge-base(base ref, HEAD); HEAD without a base ref; None before the first commit.
fn delta_base() -> Option<String> {
    let head = head_commit()?;
    let merge_base =
        base_ref().and_then(|base| git_lines(&["merge-base", &base, "HEAD"]).into_iter().next());
    Some(merge_base.unwrap_or(head))
}

/// The new-side path of a `+++ b/<path>` header; None for a deleted file.
fn diff_path(header: &str) -> Option<String> {
    let name = header.strip_prefix("+++ ").unwrap_or(header).trim_end_matches('\t');
    if name == "/dev/null" {
        return None;
    }
    let name = if name.len() > 1 && name.starts_with('"') && name.ends_with('"') {
        &name[1..name.len() - 1]
    } else {
        name
    };
    Some(name.strip_prefix("b/").unwrap_or(name).to_string())
}

/// (start, count) of a `@@ -a[,b] +c[,d] @@` hunk header's new side; count defaults to 1.
fn parse_hunk(line: &str) -> Option<(usize, usize)> {
    let (_, rest) = line.strip_prefix("@@ -")?.split_once(" +")?;
    let (new, _) = rest.split_once(" @@")?;
    let (start, count) = new.split_once(',').unwrap_or((new, "1"));
    Some((start.parse().ok()?, count.parse().ok()?))
}

/// `{path: [(start, end)]}` of the new-side lines in a `git diff -U0` (a/ b/ prefixes).
///
/// File headers are read only between `diff --git` and the first hunk, so an added line
/// whose text starts with `++ ` is never taken for one. A pure deletion (`+N,0`) adds no
/// range, but its file is still listed.
fn parse_diff_ranges(diff: &str) -> Scope {
    let mut ranges = Scope::new();
    let mut path: Option<String> = None;
    let mut in_header = false;
    for line in diff.lines() {
        if line.starts_with("diff --git ") {
            (path, in_header) = (None, true);
        } else if in_header && line.starts_with("+++ ") {
            path = diff_path(line);
            if let Some(path) = &path {
                ranges.insert(path.clone(), Vec::new());
            }
        } else if line.starts_with("@@") {
            in_header = false;
            if let (Some((start, count)), Some(path)) = (parse_hunk(line), &path)
                && count > 0
            {
                ranges.entry(path.clone()).or_default().push((start, start + count - 1));
            }
        }
    }
    ranges
}

/// Changed lines per path relative to this crate: `git diff <base>` plus untracked files.
///
/// Covers work committed on the branch and uncommitted work alike. Untracked files, and
/// every file before the first commit, are in scope whole. Renames count as new files.
fn changed_scope(base: Option<&str>) -> Result<Scope, String> {
    let mut listing = vec!["ls-files", "-z", "--others", "--exclude-standard", "--", "."];
    let mut scope = Scope::new();
    match base {
        None => listing.insert(1, "--cached"),
        Some(base) => {
            scope = parse_diff_ranges(&git_output(&[
                "diff",
                "-U0",
                "--no-color",
                "--no-ext-diff",
                "--no-renames",
                "--relative",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                base,
                "--",
                ".",
            ])?);
        }
    }
    for path in git_output(&listing)?.split('\0').filter(|path| !path.is_empty()) {
        scope.insert(path.to_string(), vec![WHOLE_FILE]);
    }
    Ok(scope)
}

/// True when lines `start..=end` share a line with any of `ranges`.
fn overlaps(start: usize, end: usize, ranges: &[(usize, usize)]) -> bool {
    ranges.iter().any(|&(from, to)| from <= end && start <= to)
}

/// Changed `.rs` files under `targets` (directories) that still exist.
fn scoped_files(scope: &Scope, targets: &[&str]) -> Vec<String> {
    let under = |path: &str| {
        targets
            .iter()
            .any(|target| path.strip_prefix(target).is_some_and(|rest| rest.starts_with('/')))
    };
    scope
        .keys()
        .filter(|path| is_rs_path(path) && under(path) && root().join(path).is_file())
        .cloned()
        .collect()
}

// ── Lint residue ──

fn is_diagnostic(text: &str) -> bool {
    ["warning:", "warning[", "error:", "error["].iter().any(|level| text.starts_with(level))
}

/// `path:line: level: message` for each clippy warning or error on a changed line.
///
/// cargo replays a cached diagnostic in the format that first rendered it, so both
/// forms are read: long (`warning: m`, then ` --> path:line:col`) and short
/// (`path:line:col: warning: m`).
fn clippy_findings(report: &str, scope: &Scope) -> Vec<String> {
    let mut findings = Vec::new();
    let mut header = None; // a long-form diagnostic waiting for its ` --> ` line
    for line in report.lines() {
        let located = if let Some(location) = line.trim_start().strip_prefix("--> ") {
            header.take().map(|message| (location, message))
        } else if line.starts_with(char::is_whitespace) {
            None
        } else {
            header = is_diagnostic(line).then_some(line);
            line.split_once(": ").filter(|(_, message)| is_diagnostic(message))
        };
        let Some((location, message)) = located else { continue };
        let mut parts = location.rsplitn(3, ':').skip(1);
        let (Some(line_no), Some(path)) = (parts.next(), parts.next()) else { continue };
        // Only `.rs` lines: a broken Cargo.toml means clippy could not run.
        if let (Ok(line_no), Some(ranges)) = (line_no.parse(), scope.get(path))
            && is_rs_path(path)
            && overlaps(line_no, line_no, ranges)
        {
            findings.push(format!("{path}:{line_no}: {message}"));
        }
    }
    findings
}

/// Lint on changed lines. clippy checks the whole crate either way.
fn lint_residue(scope: &Scope) -> Result<Vec<String>, String> {
    if !scope.keys().any(|path| is_project_rs_file(path)) {
        return Ok(Vec::new());
    }
    let output = tool_output("clippy", &["cargo", "clippy"])?;
    let findings = clippy_findings(&String::from_utf8_lossy(&output.stderr), scope);
    if findings.is_empty() {
        // A build that failed away from the changed lines is a tool failure, not a pass.
        expect_success("clippy", &output)?;
    }
    Ok(findings)
}

// ── Complexity ──

/// `path:start: name CCN 19 (limit 15)` for each limit a function in `lizard --csv`
/// output exceeds, when the function overlaps a changed line.
///
/// Touching an over-limit function blocks until it is back under; an untouched one
/// never does.
fn touched_over_limit(csv: &str, scope: &Scope) -> Vec<String> {
    let mut findings = Vec::new();
    for (measured, name, start, end, path) in csv.lines().filter_map(parse_lizard_csv_row) {
        let span = (start as usize, end as usize);
        if !scope.get(&path).is_some_and(|ranges| overlaps(span.0, span.1, ranges)) {
            continue;
        }
        for ((label, _, limit), value) in COMPLEXITY_LIMITS.into_iter().zip(measured) {
            if value > limit {
                findings.push(format!("{path}:{start}: {name} {label} {value} (limit {limit})"));
            }
        }
    }
    findings
}

/// Over-limit functions this change touched, over the complexity gate's targets.
fn complexity_residue(scope: &Scope) -> Result<Vec<String>, String> {
    let files = scoped_files(scope, &COMPLEXITY_TARGETS);
    // lizard with no file arguments walks the working directory; never let it.
    if files.is_empty() {
        return Ok(Vec::new());
    }
    let mut cmd = vec!["uvx", LIZARD, "-l", "rust", "--csv"];
    cmd.extend(files.iter().map(String::as_str));
    Ok(touched_over_limit(&run_tool("lizard", &cmd)?, scope))
}

// ── Stop-hook verdict ──

/// Lint residue and complexity; read-only, in parallel.
fn run_delta_gates(scope: &Scope) -> Vec<DeltaResult> {
    let panicked = |_| Err("the gate panicked".to_string());
    std::thread::scope(|threads| {
        let lint = threads.spawn(|| lint_residue(scope));
        let complexity = threads.spawn(|| complexity_residue(scope));
        vec![
            DeltaResult { gate: "Lint", outcome: lint.join().unwrap_or_else(panicked) },
            DeltaResult { gate: "Complexity", outcome: complexity.join().unwrap_or_else(panicked) },
        ]
    })
}

/// The stderr block an agent reads: failed gates, then their findings; "" when clean.
///
/// At most `HOOK_FINDING_LIMIT` findings, then one line counting the rest; `verbose`
/// lifts the cap, which is what that line tells the reader to run.
fn stop_hook_payload(results: &[DeltaResult], verbose: bool) -> String {
    let failed: Vec<(&str, &Vec<String>)> = results
        .iter()
        .filter_map(|r| r.outcome.as_ref().ok().filter(|f| !f.is_empty()).map(|f| (r.gate, f)))
        .collect();
    if failed.is_empty() {
        return String::new();
    }
    let gates: Vec<&str> = failed.iter().map(|(gate, _)| *gate).collect();
    let findings: Vec<&String> = failed.iter().flat_map(|(_, findings)| *findings).collect();
    let shown = if verbose { findings.len() } else { findings.len().min(HOOK_FINDING_LIMIT) };
    let mut lines = vec![format!("stop-hook failed: {}", gates.join(", "))];
    lines.extend(findings[..shown].iter().map(ToString::to_string));
    if shown < findings.len() {
        let rest = findings.len() - shown;
        lines.push(format!(
            "\u{2026} +{rest} more \u{2014} run `cargo harness stop-hook --verbose`"
        ));
    }
    lines.join("\n")
}

/// 2 blocks on findings; 1 for a tool failure; 0 when clean.
///
/// Findings on a stop the agent is already continuing from (`"stop_hook_active": true`
/// in the hook `event`) exit 1: the hook blocks once per stop, never in a loop.
fn stop_hook_exit(payload: &str, failed_tools: usize, event: &str) -> i32 {
    if payload.is_empty() {
        i32::from(failed_tools > 0)
    } else if json_value(event, "stop_hook_active").is_some_and(|v| v.starts_with("true")) {
        1
    } else {
        2
    }
}

/// Print the verdict to stderr (nothing when clean) and return the exit code.
fn report_stop_hook(results: &[DeltaResult], event: &str) -> i32 {
    let mut failed_tools = 0;
    for result in results {
        if let Err(problem) = &result.outcome {
            eprintln!("stop-hook: {} could not run: {problem}", result.gate);
            failed_tools += 1;
        }
    }
    let payload = stop_hook_payload(results, is_verbose());
    let code = stop_hook_exit(&payload, failed_tools, event);
    if !payload.is_empty() {
        eprintln!("{payload}");
        if code == 1 {
            eprintln!("harness: already blocked once on this stop; not blocking again");
        }
    }
    code
}

// ── Hook input ──

/// The agent's hook JSON from stdin, read under a deadline; empty for a terminal.
fn hook_event() -> String {
    if io::stdin().is_terminal() {
        return String::new();
    }
    let (input, _) = read_with_deadline(io::stdin(), Duration::from_secs(1));
    String::from_utf8_lossy(&input).into_owned()
}

/// What follows `"key":` in a hook's JSON (first occurrence); None without the key.
fn json_value<'a>(event: &'a str, key: &str) -> Option<&'a str> {
    let (_, rest) = event.split_once(&format!("\"{key}\""))?;
    rest.trim_start().strip_prefix(':').map(str::trim_start)
}

/// The first `"file_path"` string of a hook event; None for any escape but `\\ \" \/`.
fn hook_file_path(event: &str) -> Option<String> {
    let mut chars = json_value(event, "file_path")?.strip_prefix('"')?.chars();
    let mut path = String::new();
    loop {
        match chars.next()? {
            '"' => return Some(path),
            '\\' => path.push(chars.next().filter(|c| matches!(c, '\\' | '"' | '/'))?),
            c => path.push(c),
        }
    }
}

/// The project `.rs` file a `PostToolUse` event names, relative to `dir`; else None.
fn hook_target(event: &str, dir: &Path) -> Option<String> {
    let resolved = dir.join(hook_file_path(event)?).canonicalize().ok()?;
    // Outside this crate: another harness owns it.
    let relative =
        resolved.strip_prefix(dir.canonicalize().ok()?).ok()?.to_str()?.replace('\\', "/");
    (is_project_rs_file(&relative) && resolved.is_file()).then_some(relative)
}

// ── Formatting ──

/// The crate's edition from Cargo.toml, which `cargo fmt` would pass to rustfmt.
fn crate_edition() -> Option<String> {
    let manifest = fs::read_to_string(root().join("Cargo.toml")).ok()?;
    manifest.lines().find_map(|line| {
        let value = line.trim().strip_prefix("edition")?.trim_start().strip_prefix('=')?;
        Some(value.trim().trim_matches('"').to_string())
    })
}

/// `source` as rustfmt formats it; None when rustfmt fails (a parse error mid-edit).
///
/// Formatting stdin keeps rustfmt from following `mod` declarations into files
/// nobody changed; rustfmt still reads `rustfmt.toml` from the crate root (the cwd).
fn rustfmt_source(source: &[u8]) -> Option<Vec<u8>> {
    let mut cmd = Command::new("rustfmt");
    if let Some(edition) = crate_edition() {
        cmd.args(["--edition", &edition]);
    }
    let mut child = cmd
        .current_dir(root())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let mut stdin = child.stdin.take()?;
    let input = source.to_vec();
    let writer = std::thread::spawn(move || stdin.write_all(&input));
    let output = child.wait_with_output().ok()?;
    let written = writer.join().is_ok_and(|result| result.is_ok());
    let emptied = output.stdout.is_empty() && !source.trim_ascii().is_empty();
    (written && output.status.success() && !emptied).then_some(output.stdout)
}

/// Rewrite one file as rustfmt formats it: Some(true) when its bytes changed, None when
/// it could not be read, formatted, or written.
fn format_file(path: &str) -> Option<bool> {
    let file = root().join(path);
    let source = fs::read(&file).ok()?;
    let formatted = rustfmt_source(&source)?;
    if formatted == source {
        return Some(false);
    }
    fs::write(&file, formatted).ok()?;
    Some(true)
}

/// Format each file in place, silently; returns those rustfmt could not format.
fn format_files(files: &[String]) -> Vec<String> {
    files.iter().filter(|path| format_file(path).is_none()).cloned().collect()
}

/// Format the one file a `PostToolUse` event names. Never blocks.
///
/// Prints one additionalContext line when the file changed, so the agent re-reads it
/// before its next edit; otherwise nothing.
fn post_edit_hook() {
    let Some(target) = hook_target(&hook_event(), root()) else { return };
    if format_file(&target) == Some(true) {
        let path = target.replace('\\', "\\\\").replace('"', "\\\"");
        println!(
            "{{\"hookSpecificOutput\":{{\"hookEventName\":\"PostToolUse\",\"additionalContext\":\"harness: reformatted {path}; re-read it before editing it again\"}}}}"
        );
    }
}

// ── AGENTS.md mirror ──

/// True when CLAUDE.md exists and AGENTS.md is missing or differs from it.
fn agents_md_stale() -> bool {
    let Ok(claude) = fs::read(root().join("CLAUDE.md")) else { return false };
    fs::read(root().join("AGENTS.md")).map_or(true, |agents| agents != claude)
}

fn mirror_claude_md() -> io::Result<()> {
    fs::write(root().join("AGENTS.md"), fs::read(root().join("CLAUDE.md"))?)
}

/// Stop hook: an uncommitted CLAUDE.md edit carries into AGENTS.md, silently.
///
/// CLAUDE.md is canonical. An edit to AGENTS.md alone is left for pre-commit to report.
fn sync_agents_md_after_edit() {
    if !git_lines(&["status", "--porcelain", "--", "CLAUDE.md"]).is_empty() && agents_md_stale() {
        let _ = mirror_claude_md();
    }
}

/// pre-commit: a staged CLAUDE.md carries AGENTS.md into the same commit.
///
/// The `git add` inherits git's hook environment on purpose: `GIT_INDEX_FILE` is the
/// index this commit is being built from.
fn sync_agents_md_staged() {
    if git_lines(&["diff", "--cached", "--name-only", "--", "CLAUDE.md"]).is_empty()
        || !agents_md_stale()
    {
        return;
    }
    let staged = mirror_claude_md()
        .map_err(|e| e.to_string())
        .and_then(|()| run_tool("git", &["git", "add", "--", "AGENTS.md"]));
    if let Err(reason) = staged {
        println!("  {RED}\u{2717}{RESET} sync-agents-md: {reason}");
        std::process::exit(1);
    }
    println!("  {GREEN}\u{2713}{RESET} sync-agents-md: AGENTS.md \u{2190} CLAUDE.md (staged)");
}

// ── Stages ──────────────────────────────────────────────────────────

/// Warn when the Claude/Codex Stop or Claude `PostToolUse` wiring is missing.
fn check_hooks_present() {
    let wirings = [
        (".claude/settings.json", "Stop", "stop-hook"),
        (".claude/settings.json", "PostToolUse", "post-edit --hook"),
        (".codex/hooks.json", "Stop", "stop-hook"),
    ];
    for (rel, event, marker) in wirings {
        let text = fs::read_to_string(root().join(rel)).unwrap_or_default();
        if text.contains(event) && text.contains(marker) {
            println!("  {GREEN}\u{2713}{RESET} {event} hook wiring ({rel})");
        } else {
            println!("  {RED}\u{26a0}{RESET} Missing {event} hook wiring: {rel}");
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
    if !root().join("CLAUDE.md").exists() {
        println!("  {RED}\u{2717}{RESET} sync-agents-md: CLAUDE.md not found");
        std::process::exit(1);
    }
    if let Err(e) = mirror_claude_md() {
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
            "Tests",
            &["cargo", "test"],
            Some(&RunOpts {
                extract: Some(extract_test_summary),
                no_exit: true,
                ..RunOpts::default()
            }),
        ),
        check_agents_md_drift(true),
    ];

    check_hooks_present();
    check_arch_config_guard(true, false, false);
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

/// Fix/format, and mirror a staged CLAUDE.md; tests run at pre-push.
fn cmd_pre_commit() {
    println!("\n{BLUE}[pre-commit]{RESET}\n");
    check_arch_config_guard(true, true, false);
    sync_agents_md_staged();

    let files = staged_rs_files();
    // A commit that touches only a hand-edited AGENTS.md must fail too.
    let docs_staged =
        !git_lines(&["diff", "--cached", "--name-only", "--", "AGENTS.md", "CLAUDE.md"]).is_empty();
    if !files.is_empty() || docs_staged {
        check_agents_md_drift(false);
    }
    if files.is_empty() {
        println!("No staged Rust files \u{2014} skipping checks");
        return;
    }

    cmd_fix();
}

fn cmd_ci() {
    println!("\n{BLUE}[ci]{RESET}\n");
    // Read-only gates run as a parallel batch (captured, printed in submission
    // order, run to completion). Tests + coverage are captured; CRAP is advisory.
    let mut gates = vec![lint_gate(), format_check_gate(), complexity_gate()];
    gates.extend(acceptance_gates_or_warn());
    gates.extend(arch_gates_or_warn());
    // Bind each result before combining: every step must run (no &&-short-circuit)
    // so one pass surfaces every failure. Audit is install-aware and strict in ci.
    let batch_ok = run_gates_parallel(&gates);
    let agents_md_drift_ok = check_agents_md_drift(true).ok;
    let audit_ok = cmd_audit_inner(true);
    let tests_ok =
        run("Tests", &["cargo", "test"], Some(&RunOpts { no_exit: true, ..RunOpts::default() })).ok;
    cmd_coverage();
    cmd_crap();
    let arch_config_ok = check_arch_config_guard(false, false, false);
    let suppressions_ok = check_suppressions_baseline(true);
    if !batch_ok
        || !agents_md_drift_ok
        || !audit_ok
        || !tests_ok
        || !arch_config_ok
        || !suppressions_ok
    {
        std::process::exit(1);
    }
}

/// The test suite, outside git's hook environment (see `strip_git_env`).
fn test_gate() -> Gate {
    Gate {
        extract: Some(extract_test_summary),
        without_git_env: true,
        ..Gate::new("Tests", &["cargo", "test"])
    }
}

/// Push gate: the offline checks pre-commit and stop-hook do not run.
/// pre-commit covers fix/format on staged files; stop-hook covers the change's delta.
/// This fills the gap with the deterministic, offline gates none of them run —
/// tests, clippy (strict), format check, acceptance, arch, agents-md-drift —
/// validating the whole pushed tree (after merges/rebases/--no-verify) before it
/// leaves the machine. Tests run first and alone: they write to `target/`; the rest
/// is a read-only parallel batch. Network (audit) and advisory (coverage/CRAP) gates
/// stay in ci.
fn cmd_pre_push() {
    println!("\n{BLUE}[pre-push]{RESET}\n");
    // Branch guard first, and it short-circuits: on refusal (or incomplete
    // refs) print only that and exit before the arch guard, drift check, and
    // the parallel batch even start.
    if !check_branch_guard(pre_push_refs(), protected_push_allowed()) {
        std::process::exit(1);
    }
    let arch_config_ok = check_arch_config_guard(false, false, true);
    let agents_md_drift_ok = check_agents_md_drift(true).ok;
    let tests_ok = print_gate_result(&run_capture(&test_gate()), true);
    let mut gates = vec![lint_gate(), format_check_gate()];
    gates.extend(acceptance_gates_or_warn());
    gates.extend(arch_gates_or_warn());
    if !run_gates_parallel(&gates) || !arch_config_ok || !agents_md_drift_ok || !tests_ok {
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
    strip_git_env(&mut cmd);
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
    // The runner is std-only (no JSON parser), so it verifies the hook wiring
    // rather than injecting into settings that may carry other hooks. The template
    // ships .claude/settings.json and .codex/hooks.json already wired; copy them in
    // (cp -r the template's .claude / .codex) if this warns.
    check_hooks_present();
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
    ("branch-guard", cmd_branch_guard),
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
            Some(([16, 2, 20], "risky".to_string(), 12, 31, "src/lib.rs".to_string())),
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
    fn stdin_ref_targeting_main_is_protected() {
        let line = "refs/heads/topic abc123 refs/heads/main def456";
        assert_eq!(protected_push_target(line, "topic"), Some("main".to_string()));
    }

    #[test]
    fn stdin_ref_targeting_feature_is_not_protected() {
        let line = "refs/heads/topic abc123 refs/heads/feature def456";
        assert_eq!(protected_push_target(line, "main"), None);
    }

    #[test]
    fn deleting_main_is_protected() {
        let zero = "0".repeat(40);
        let line = format!("(delete) {zero} refs/heads/main def456");
        assert_eq!(protected_push_target(&line, "topic"), Some("main".to_string()));
    }

    #[test]
    fn deleting_a_feature_branch_is_not_protected() {
        let zero = "0".repeat(40);
        let line = format!("(delete) {zero} refs/heads/feature/old def456");
        assert_eq!(protected_push_target(&line, "main"), None);
    }

    #[test]
    fn tag_refs_never_match_a_protected_branch() {
        let line = "refs/tags/main abc123 refs/tags/main def456";
        assert_eq!(protected_push_target(line, "feature/x"), None);
    }

    #[test]
    fn no_stdin_falls_back_to_current_branch() {
        assert_eq!(protected_push_target("", "main"), Some("main".to_string()));
        assert_eq!(protected_push_target("", "master"), Some("master".to_string()));
        assert_eq!(protected_push_target("", "feature/x"), None);
    }

    #[test]
    fn malformed_refs_fall_back_to_current_branch() {
        // "garbage" never reaches the 4-field threshold, so there is no
        // parseable record at all: same as no refs, the current branch decides.
        assert_eq!(protected_push_target("garbage", "main"), Some("main".to_string()));
        assert_eq!(protected_push_target("garbage", "feature/x"), None);
    }

    #[test]
    fn tag_only_refs_pass_even_on_a_main_checkout() {
        // A tag line still parses as a 4-field record, so — like a feature-branch
        // record — it takes precedence over the current branch even though it
        // never names a protected head: `git push origin v1.0` from `main` is a
        // legitimate push and must not be refused. Matches python/go.
        let line = "refs/tags/v1 abc123 refs/tags/v1 def456";
        assert_eq!(protected_push_target(line, "main"), None);
    }

    #[test]
    fn override_lets_a_protected_push_pass() {
        let refs = PrePushRefs::Refs("refs/heads/topic abc refs/heads/main def".to_string());
        assert!(!check_branch_guard(&refs, false), "protected push must be refused by default");
        assert!(check_branch_guard(&refs, true), "override must let a protected push pass");
    }

    #[test]
    fn incomplete_refs_fail_both_guards() {
        assert!(!check_branch_guard(&PrePushRefs::Incomplete, true), "partial refs must refuse");
    }

    /// Sends one chunk, then stalls past any deadline: a pipe git never closes.
    struct StalledReader {
        sent: bool,
    }

    impl Read for StalledReader {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            if self.sent {
                std::thread::sleep(Duration::from_secs(30));
                return Ok(0);
            }
            self.sent = true;
            let line = b"refs/heads/x abc refs/heads/feature def";
            buf[..line.len()].copy_from_slice(line);
            Ok(line.len())
        }
    }

    #[test]
    fn whole_read_empty_input_means_no_refs() {
        let refs = read_until(io::Cursor::new(Vec::new()), Duration::from_secs(1));
        assert!(matches!(refs, PrePushRefs::None));
    }

    #[test]
    fn whole_read_to_eof_keeps_the_refs() {
        let line = "refs/heads/x abc refs/heads/main def";
        let refs = read_until(io::Cursor::new(line.as_bytes().to_vec()), Duration::from_secs(1));
        let PrePushRefs::Refs(text) = refs else { panic!("expected refs") };
        assert_eq!(text, line);
    }

    #[test]
    fn deadline_after_partial_data_is_incomplete() {
        let refs = read_until(StalledReader { sent: false }, Duration::from_millis(200));
        assert!(matches!(refs, PrePushRefs::Incomplete), "partial read must not look like refs");
    }

    #[test]
    fn parallel_gates_empty_batch_passes() {
        assert!(run_gates_parallel(&[]));
    }

    // ── Stop hook ──

    const DIFF: &str = "diff --git a/src/app.py b/src/app.py\n\
        index 1..2 100644\n\
        --- a/src/app.py\n\
        +++ b/src/app.py\n\
        @@ -1,0 +2 @@ x\n\
        +y\n\
        @@ -10,2 +11,3 @@ def f():\n\
        +++ looks like a header but is an added line\n\
        +b\n\
        +c\n\
        @@ -20,4 +22,0 @@\n\
        -gone\n\
        diff --git a/old.py b/old.py\n\
        deleted file mode 100644\n\
        --- a/old.py\n\
        +++ /dev/null\n\
        @@ -1,2 +0,0 @@\n\
        -x\n\
        diff --git a/sp ace.py b/sp ace.py\n\
        new file mode 100644\n\
        --- /dev/null\n\
        +++ b/sp ace.py\t\n\
        @@ -0,0 +1,4 @@\n\
        +a\n\
        diff --git a/only_deleted.py b/only_deleted.py\n\
        --- a/only_deleted.py\n\
        +++ b/only_deleted.py\n\
        @@ -3 +2,0 @@\n\
        -x\n";

    #[test]
    fn parse_diff_ranges_per_file() {
        let expected = Scope::from([
            ("src/app.py".to_string(), vec![(2, 2), (11, 13)]),
            ("sp ace.py".to_string(), vec![(1, 4)]),
            ("only_deleted.py".to_string(), vec![]),
        ]);
        assert_eq!(parse_diff_ranges(DIFF), expected);
    }

    #[test]
    fn stop_hook_exit_codes() {
        let active = r#"{"session_id": "s", "stop_hook_active" : true}"#;
        let cases = [
            ("", 0, "", 0),
            ("", 1, active, 1),
            ("P", 0, "", 2),
            ("P", 1, "", 2),     // a crashed gate never hides another gate's findings
            ("P", 0, active, 1), // already continuing from a block: never loop
            ("P", 1, active, 1),
            ("P", 0, r#"{"stop_hook_active":false}"#, 2),
            ("P", 0, r#"{"stop_hook_active": "true"}"#, 2),
            ("P", 0, "not json", 2),
        ];
        for (payload, failed, event, expected) in cases {
            assert_eq!(
                stop_hook_exit(payload, failed, event),
                expected,
                "{payload:?} {failed} {event}"
            );
        }
    }

    fn found(gate: &'static str, findings: Vec<String>) -> DeltaResult {
        DeltaResult { gate, outcome: Ok(findings) }
    }

    fn numbered(count: usize) -> Vec<String> {
        (1..=count).map(|n| format!("a.rs:{n}: x")).collect()
    }

    #[test]
    fn payload_names_failed_gates_and_caps_findings() {
        let busy = vec!["b.rs:1: busy CCN 16 (limit 15)".to_string()];
        let results = [found("Lint", numbered(23)), found("Complexity", busy)];
        let payload = stop_hook_payload(&results, false);
        let lines: Vec<&str> = payload.lines().collect();
        assert_eq!(lines.len(), 22);
        assert_eq!(lines[0], "stop-hook failed: Lint, Complexity");
        assert_eq!(lines[20], "a.rs:20: x");
        assert_eq!(lines[21], "… +4 more — run `cargo harness stop-hook --verbose`");
        assert_eq!(stop_hook_payload(&results, true).lines().count(), 25, "verbose lifts the cap");
        assert_eq!(stop_hook_payload(&[found("Lint", numbered(20))], false).lines().count(), 21);
        let failed = DeltaResult { gate: "Complexity", outcome: Err("boom".to_string()) };
        assert_eq!(stop_hook_payload(&[found("Lint", vec![]), failed], false), "");
    }

    #[test]
    fn only_touched_functions_over_a_limit_are_findings() {
        let csv = "NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end\n\
            9,16,45,9,101,\"busy@10-110@src/a.rs\",\"src/a.rs\",\"busy\",\"busy( a , b )\",10,110\n\
            9,20,45,1,20,\"legacy@120-139@src/a.rs\",\"src/a.rs\",\"legacy\",\"legacy( )\",120,139\n\
            9,15,45,8,100,\"fine@140-239@src/a.rs\",\"src/a.rs\",\"fine\",\"fine( )\",140,239\n\
            9,30,45,1,5,\"other@1-5@src/b.rs\",\"src/b.rs\",\"other\",\"other( )\",1,5\n";
        let scope = Scope::from([("src/a.rs".to_string(), vec![(1, 10), (150, 150)])]);
        assert_eq!(
            touched_over_limit(csv, &scope),
            [
                "src/a.rs:10: busy CCN 16 (limit 15)",
                "src/a.rs:10: busy args 9 (limit 8)",
                "src/a.rs:10: busy length 101 (limit 100)",
            ]
        );
    }

    #[test]
    fn clippy_findings_read_both_forms_on_changed_lines() {
        let report = [
            "    Checking stub v0.1.0",
            "warning: unused variable: `x`",
            " --> src/a.rs:3:9",
            "note: the lint level is defined here",
            " --> src/a.rs:2:9",
            "error[E0425]: cannot find value `y`",
            "  --> src/a.rs:40:5",
            "src/a.rs:2:5: warning: unneeded `return` statement",
            "src/a.rs:9:5: warning: old",
            "warning: `stub` (lib) generated 2 warnings",
        ]
        .join("\n");
        let scope = Scope::from([("src/a.rs".to_string(), vec![(2, 3)])]);
        assert_eq!(
            clippy_findings(&report, &scope),
            [
                "src/a.rs:3: warning: unused variable: `x`",
                "src/a.rs:2: warning: unneeded `return` statement",
            ]
        );
    }

    #[test]
    fn hook_target_resolves_inside_the_project_only() {
        let tmp = env::temp_dir().join(format!("rust-hook-target-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        let project = tmp.join("project");
        for file in ["src/app.rs", "src/data.txt", "target/debug/build.rs"] {
            fs::create_dir_all(project.join(file).parent().unwrap()).unwrap();
            fs::write(project.join(file), "").unwrap();
        }
        fs::write(tmp.join("elsewhere.rs"), "").unwrap();
        let absolute = project.join("src/app.rs").to_string_lossy().replace('\\', "\\\\");
        let event = |path: &str| format!(r#"{{"tool_input": {{"file_path": "{path}"}}}}"#);
        let cases = [
            (event(&absolute), Some("src/app.rs")),
            (event("src/app.rs"), Some("src/app.rs")),
            (event(r"src\/app.rs"), Some("src/app.rs")),
            (event("src/data.txt"), None),
            (event("target/debug/build.rs"), None),
            (event("../elsewhere.rs"), None),
            (event(r"src\u0061pp.rs"), None), // escapes past \\ \" \/ are not read
            ("{not json".to_string(), None),
            (String::new(), None),
        ];
        for (input, expected) in cases {
            assert_eq!(hook_target(&input, &project).as_deref(), expected, "{input}");
        }
        fs::remove_dir_all(&tmp).unwrap();
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
    }
}
