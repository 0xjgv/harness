//! Project development tasks. Zero dependencies — std only.
//!
//! Usage:
//!   cargo harness              # full pre-flight (default)
//!   cargo harness check        # full pre-flight
//!   cargo harness fix          # fix lint + format
//!   cargo harness lint         # lint + format check (read-only)
//!   cargo harness test         # run tests
//!   cargo harness test-cov     # run tests (coverage note)
//!   cargo harness pre-commit   # staged checks + tests
//!   cargo harness ci           # CI gate
//!   cargo harness setup-hooks   # install git pre-commit hook
//!   cargo harness clean        # remove artifacts
//!   cargo harness help         # show usage
//!   cargo harness --verbose    # show all command output

use std::collections::{BTreeMap, HashMap};
use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::time::Instant;

// ── Configuration ───────────────────────────────────────────────────

fn root() -> PathBuf {
    env::current_dir().expect("cannot determine working directory")
}

fn is_verbose() -> bool {
    static VERBOSE: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *VERBOSE.get_or_init(|| {
        env::args().any(|a| a == "--verbose") || env::var("VERBOSE").as_deref() == Ok("1")
    })
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
}

fn run(description: &str, cmd: &[&str], opts: Option<&RunOpts>) -> RunResult {
    let verbose = is_verbose();

    if verbose {
        println!("  {DIM}\u{2192} {}{RESET}", cmd.join(" "));
    }

    let program = cmd[0];
    let args = &cmd[1..];
    let dir = root();

    if verbose {
        let status = Command::new(program).args(args).current_dir(&dir).status();

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
    let result = Command::new(program)
        .args(args)
        .current_dir(&dir)
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
        loop {
            let Some(idx) = rest.find(prefix) else { break };
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

fn scan_suppressions(roots: &[PathBuf]) -> SuppressionCounts {
    let mut results: SuppressionCounts = BTreeMap::new();
    for root_path in roots {
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
                } else if path.extension().is_some_and(|e| e == "rs") {
                    let Ok(text) = fs::read_to_string(&path) else {
                        continue;
                    };
                    for line in text.lines() {
                        for (kind, rules) in parse_line_for_suppressions(line) {
                            results.entry(kind).or_default().push(rules);
                        }
                    }
                }
            }
        }
    }
    results
}

fn default_suppression_roots() -> Vec<PathBuf> {
    vec![root().join("src"), root().join("tests")]
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
        .args(["diff", "--cached", "--name-only", "--diff-filter=d"])
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

fn staged_packages(files: &[String]) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut pkgs = Vec::new();

    for f in files {
        let dir = Path::new(f).parent().map_or_else(
            || ".".to_string(),
            |p| {
                let s = p.to_string_lossy();
                if s.is_empty() { ".".to_string() } else { s.to_string() }
            },
        );

        if seen.insert(dir.clone()) {
            pkgs.push(dir);
        }
    }
    pkgs
}

fn has_non_test_files(files: &[String]) -> bool {
    files.iter().any(|f| {
        !Path::new(f)
            .file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|n| n.starts_with("test") || n.ends_with("_test.rs"))
    })
}

fn dirty_rs_files() -> Vec<String> {
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

fn cmd_fix(_pkgs: &[String]) {
    // clippy --fix requires --allow-dirty --allow-staged for uncommitted changes
    run("Clippy fix", &["cargo", "clippy", "--fix", "--allow-dirty", "--allow-staged"], None);
    run("Format", &["cargo", "fmt"], None);
}

fn cmd_lint() {
    run("Clippy", &["cargo", "clippy"], None);
    run("Format check", &["cargo", "fmt", "--check"], None);
}

fn cmd_test() {
    run(
        "Tests",
        &["cargo", "test"],
        Some(&RunOpts { extract: Some(extract_test_summary), ..RunOpts::default() }),
    );
}

fn cmd_test_cov() {
    // Rust has no built-in coverage. cargo test is the baseline;
    // cargo-llvm-cov can be installed separately for full coverage reports.
    run(
        "Tests",
        &["cargo", "test"],
        Some(&RunOpts { extract: Some(extract_test_summary), ..RunOpts::default() }),
    );
}

fn cmd_audit() {
    cmd_audit_inner(false);
}

fn cmd_audit_inner(strict: bool) {
    // cargo-audit requires separate installation
    let installed = Command::new("cargo")
        .args(["audit", "--version"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .status()
        .is_ok_and(|s| s.success());

    if installed {
        run("Dep audit", &["cargo", "audit"], None);
    } else if strict {
        println!("  {RED}\u{2717}{RESET} Dep audit (cargo-audit not installed)");
        std::process::exit(1);
    } else {
        println!("  {DIM}\u{2298} Dep audit skipped (install: cargo install cargo-audit){RESET}");
    }
}

fn cmd_post_edit() {
    if dirty_rs_files().is_empty() {
        return;
    }
    run("Format", &["cargo", "fmt"], Some(&RunOpts { no_exit: true, ..RunOpts::default() }));
}

// ── Stages ──────────────────────────────────────────────────────────

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
            Some(&RunOpts { extract: Some(extract_test_summary), no_exit: true }),
        ),
    ];

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

    let pkgs = staged_packages(&files);
    cmd_fix(&pkgs);

    if has_non_test_files(&files) {
        cmd_test();
    }
}

fn cmd_ci() {
    println!("\n{BLUE}[ci]{RESET}\n");
    run("Clippy (strict)", &["cargo", "clippy", "--", "-D", "warnings"], None);
    run("Format check", &["cargo", "fmt", "--check"], None);
    cmd_audit_inner(true);
    run(
        "Tests",
        &["cargo", "test"],
        Some(&RunOpts { extract: Some(extract_test_summary), ..RunOpts::default() }),
    );
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
}

fn cmd_clean() {
    println!("\n{BLUE}[clean]{RESET}\n");
    run("Clean build artifacts", &["cargo", "clean"], None);

    // Remove coverage artifacts not under target/
    for name in ["*.profraw", "*.profdata", "lcov.info", "tarpaulin-report.html"] {
        let path = root().join(name);
        if path.exists() {
            let _ = fs::remove_file(&path);
            println!("  {GREEN}\u{2713}{RESET} Removed {name}");
        }
    }
}

// ── CLI dispatch ────────────────────────────────────────────────────

const COMMANDS: &[(&str, &str)] = &[
    ("check", "Full pre-flight: fix + format + test"),
    ("fix", "Fix lint errors + format code"),
    ("lint", "Lint + format check (read-only)"),
    ("test", "Run tests"),
    ("test-cov", "Run tests (install cargo-llvm-cov for coverage)"),
    ("audit", "Audit dependencies for known vulnerabilities"),
    ("pre-commit", "Staged checks + tests"),
    ("ci", "Clippy strict + format check + tests"),
    ("setup-hooks", "Install git pre-commit hook"),
    ("post-edit", "Format if source files changed (Claude Code hook)"),
    ("clean", "Remove target/ and build cache"),
];

fn dispatch(command: &str) {
    match command {
        "check" => cmd_check(),
        "fix" => cmd_fix(&[]),
        "lint" => cmd_lint(),
        "test" => cmd_test(),
        "test-cov" => cmd_test_cov(),
        "audit" => cmd_audit(),
        "pre-commit" => cmd_pre_commit(),
        "ci" => cmd_ci(),
        "setup-hooks" => cmd_hooks(),
        "post-edit" => cmd_post_edit(),
        "clean" => cmd_clean(),
        _ => {}
    }
}

fn cmd_help() {
    println!("Usage: cargo harness <command> [--verbose]");
    println!();
    println!("Commands:");
    println!("  {:<14} Full pre-flight: fix + format + test", "(default)");
    for (name, desc) in COMMANDS {
        println!("  {name:<14} {desc}");
    }
    println!("  {:<14} Show all command output", "--verbose");
    println!("  {:<14} Show this help", "help");
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).filter(|a| !a.starts_with('-')).collect();

    if args.is_empty() {
        cmd_check();
        return ExitCode::SUCCESS;
    }

    let command = args[0].as_str();

    if command == "help" {
        cmd_help();
        return ExitCode::SUCCESS;
    }

    if COMMANDS.iter().any(|(name, _)| *name == command) {
        dispatch(command);
        return ExitCode::SUCCESS;
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
}
