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
//!   cargo harness hooks        # install git pre-commit hook
//!   cargo harness clean        # remove artifacts
//!   cargo harness help         # show usage
//!   cargo harness --verbose    # show all command output

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
    ("pre-commit", "Staged checks + tests"),
    ("ci", "Clippy strict + format check + tests"),
    ("hooks", "Install git pre-commit hook"),
    ("clean", "Remove target/ and build cache"),
];

fn dispatch(command: &str) {
    match command {
        "check" => cmd_check(),
        "fix" => cmd_fix(&[]),
        "lint" => cmd_lint(),
        "test" => cmd_test(),
        "test-cov" => cmd_test_cov(),
        "pre-commit" => cmd_pre_commit(),
        "ci" => cmd_ci(),
        "hooks" => cmd_hooks(),
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
