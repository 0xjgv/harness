//! Project development tasks. Zero dependencies — std only.
//!
//! Usage: cargo harness <command> [--verbose]

use std::collections::{BTreeMap, HashMap};
use std::env;
use std::fs;
use std::io::Write;
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
    /// Stream inherits stdio for long commands (tests, coverage) so their live
    /// output shows instead of being captured — captured silence looks like a hang.
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
}

impl Gate {
    fn new(description: &'static str, cmd: &[&str]) -> Self {
        Self { description, cmd: cmd.iter().map(|&s| s.to_string()).collect(), extract: None }
    }
}

struct GateResult {
    description: &'static str,
    cmd: Vec<String>,
    ok: bool,
    exit_code: i32,
    output: String,
    detail: Option<String>,
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
                description: gate.description,
                cmd: gate.cmd.clone(),
                ok,
                exit_code: output.status.code().unwrap_or(1),
                output: combined,
                detail,
            }
        }
        Err(e) => GateResult {
            description: gate.description,
            cmd: gate.cmd.clone(),
            ok: false,
            exit_code: 1,
            output: format!("Failed to execute {program}: {e}"),
            detail: None,
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

fn scan_rs_file(path: &Path, results: &mut SuppressionCounts) {
    if path.extension().is_none_or(|e| e != "rs") {
        return;
    }
    let Ok(text) = fs::read_to_string(path) else {
        return;
    };
    for line in text.lines() {
        for (kind, rules) in parse_line_for_suppressions(line) {
            results.entry(kind).or_default().push(rules);
        }
    }
}

fn scan_suppressions(roots: &[PathBuf]) -> SuppressionCounts {
    let mut results: SuppressionCounts = BTreeMap::new();
    for root_path in roots {
        if root_path.is_file() {
            scan_rs_file(root_path, &mut results);
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
                    scan_rs_file(&path, &mut results);
                }
            }
        }
    }
    results
}

fn default_suppression_roots() -> Vec<PathBuf> {
    vec![root().join("src"), root().join("tests"), root().join("harness.rs")]
}

fn print_suppressions_report() {
    let results = scan_suppressions(&default_suppression_roots());
    let total: usize = results.values().map(Vec::len).sum();
    println!("\n=== Suppressions ===\n");
    println!("Suppressions: {total} total");
    if total == 0 {
        return;
    }
    for (kind, entries) in &results {
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

fn changed_rs_files() -> Vec<String> {
    let output = Command::new("git")
        .args(["status", "--porcelain"])
        .current_dir(root())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();

    let Ok(output) = output else {
        return Vec::new();
    };

    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            if line.len() < 4 {
                return None;
            }
            let f = &line[3..];
            if Path::new(f).extension().is_some_and(|ext| ext.eq_ignore_ascii_case("rs")) {
                Some(f.to_string())
            } else {
                None
            }
        })
        .collect()
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
}

fn format_check_gate() -> Gate {
    Gate::new("Format check", &["cargo", "fmt", "--check"])
}

fn cmd_test() {
    // Stream: `cargo test` is a long command, so live output beats captured silence.
    run("Tests", &["cargo", "test"], Some(&RunOpts { stream: true, ..RunOpts::default() }));
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
        return run(
            "Dep audit",
            &["cargo", "audit"],
            Some(&RunOpts { no_exit: strict, ..RunOpts::default() }),
        )
        .ok;
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

fn cmd_stop_hook() {
    println!("\n=== Stop Hook Checks ===\n");
    cmd_post_edit(); // mutating — sequential, first
    let all_ok = run_gates_parallel(&[complexity_gate()]); // read-only batch
    cmd_crap(); // streaming advisory — after the batch
    if !all_ok {
        std::process::exit(1);
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
    vec![Gate::new("Acceptance (cucumber)", &["cargo", "test", "--test", "acceptance", "--quiet"])]
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
    let min_pct = arg_value("--min").and_then(|v| v.parse::<u32>().ok()).unwrap_or(0);

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
        Some(&RunOpts { env: env.clone(), stream: true, ..RunOpts::default() }),
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
/// checks. `arch.toml` is a PROTECTED path — the pre-edit hook denies edits
/// unless the user's prompt authorizes them. Absent config → skip.
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
        ),
        Gate::new("Arch: no orphan files", &["cargo", "modules", "orphans", "--lib"]),
    ]
}

fn cmd_arch() {
    for gate in arch_gates_or_warn() {
        print_gate_result(&run_capture(&gate), false);
    }
}

/// Run lizard as a cyclomatic-complexity gate. Mirrors bun/python invocation.
fn complexity_gate() -> Gate {
    Gate::new(
        "Complexity (lizard)",
        &[
            "uvx",
            "lizard@1.22.2",
            "-l",
            "rust",
            "src",
            "tests",
            "-C",
            "15",
            "-a",
            "8",
            "-L",
            "100",
            "-i",
            "0",
        ],
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
            Some(&RunOpts { env: env.clone(), no_exit: true, stream: true, ..RunOpts::default() }),
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
        .args(["lizard@1.22.2", "-l", "rust", "src", "--csv"])
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

/// Warn when required hook scripts are missing (drift detection).
fn check_hooks_present() {
    let required = [
        ".claude/scripts/session-start.sh",
        ".claude/scripts/ups-classify.sh",
        ".claude/scripts/pre-bash-gate.sh",
        ".claude/scripts/pre-edit-gate.sh",
    ];
    let missing: Vec<&str> =
        required.iter().filter(|p| !root().join(p).exists()).copied().collect();
    if !missing.is_empty() {
        println!("  {RED}\u{26a0}{RESET} Missing hook scripts: {}", missing.join(", "));
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

    let results = [
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
    print_suppressions_report();

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

fn cmd_pre_commit() {
    let files = staged_rs_files();
    if files.is_empty() {
        println!("No staged Rust files \u{2014} skipping checks");
        return;
    }

    println!("\n{BLUE}[pre-commit]{RESET}\n");

    cmd_fix();
    check_agents_md_drift(false);
    cmd_test();
}

fn cmd_ci() {
    println!("\n{BLUE}[ci]{RESET}\n");
    // Read-only gates run as a parallel batch (captured, printed in submission
    // order, run to completion). Tests + coverage stream; CRAP is advisory — after.
    let mut gates = vec![lint_gate(), format_check_gate(), complexity_gate()];
    gates.extend(acceptance_gates_or_warn());
    gates.extend(arch_gates_or_warn());
    // Bind each result before combining: every step must run (no &&-short-circuit)
    // so one pass surfaces every failure. Audit is install-aware and strict in ci.
    let batch_ok = run_gates_parallel(&gates);
    let audit_ok = cmd_audit_inner(true);
    let tests_ok = run(
        "Tests",
        &["cargo", "test"],
        Some(&RunOpts { no_exit: true, stream: true, ..RunOpts::default() }),
    )
    .ok;
    cmd_coverage();
    cmd_crap();
    if !batch_ok || !audit_ok || !tests_ok {
        std::process::exit(1);
    }
}

/// Read-only push gate: the offline checks pre-commit and stop-hook do not run.
/// pre-commit covers fix/format/test on staged files; stop-hook adds complexity.
/// This fills the gap with the deterministic, offline gates none of them run —
/// clippy (strict), format check, acceptance, arch — validating the whole pushed
/// tree (after merges/rebases/--no-verify) before it leaves the machine. Network
/// (audit) and advisory (coverage/CRAP) gates stay in ci.
fn cmd_pre_push() {
    println!("\n{BLUE}[pre-push]{RESET}\n");
    let mut gates = vec![lint_gate(), format_check_gate()];
    gates.extend(acceptance_gates_or_warn());
    gates.extend(arch_gates_or_warn());
    if !run_gates_parallel(&gates) {
        std::process::exit(1);
    }
}

fn cmd_hooks() {
    let hook_dir = root().join(".git").join("hooks");
    let hook_path = hook_dir.join("pre-commit");

    if let Err(e) = fs::create_dir_all(&hook_dir) {
        eprintln!("Failed to create hooks directory: {e}");
        std::process::exit(1);
    }

    let content = "#!/bin/sh\ncargo harness pre-commit\n";
    let Ok(mut file) = fs::File::create(&hook_path) else {
        eprintln!("Failed to write hook");
        std::process::exit(1);
    };
    if file.write_all(content.as_bytes()).is_err() {
        eprintln!("Failed to write hook");
        std::process::exit(1);
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&hook_path, fs::Permissions::from_mode(0o755));
    }

    println!("Installed pre-commit hook");
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
    ("complexity", cmd_complexity),
    ("crap", cmd_crap),
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
