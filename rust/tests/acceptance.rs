//! Acceptance test runner: executes Gherkin scenarios under `tests/features/`.
//!
//! Run via `cargo harness acceptance` (or `cargo test --test acceptance`).
//! Declared in Cargo.toml with `harness = false` so cucumber owns the output.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use cucumber::{World, given, then, when};

/// Path to the built `harness` binary. Cargo sets this env var for integration
/// tests when the package defines a `[[bin]]` of that name, and ensures the
/// binary is built before this test runs.
const HARNESS_BIN: &str = env!("CARGO_BIN_EXE_harness");

/// Function with 9 branches (CCN ~9). Paired with hits=0 lines this scores
/// CRAP = 9² × 1³ + 9 = 90, well above --max=0.
const STUB_RS: &str = "pub fn stub(n: i32) -> i32 {
    if n < 1 { return 0; }
    if n < 2 { return 1; }
    if n < 3 { return 2; }
    if n < 4 { return 3; }
    if n < 5 { return 4; }
    if n < 6 { return 5; }
    if n < 7 { return 6; }
    if n < 8 { return 7; }
    8
}
";

const ZERO_COVERAGE_LCOV: &str = "SF:src/stub.rs
DA:1,0
DA:2,0
DA:3,0
DA:4,0
DA:5,0
DA:6,0
DA:7,0
DA:8,0
DA:9,0
DA:10,0
end_of_record
";

/// Shared state for a single scenario. Smoke and crap fields coexist because
/// cucumber-rs binds a single World type per binary.
#[derive(Debug, Default, World)]
struct CrateWorld {
    // Smoke scenario.
    name: Option<&'static str>,
    // Crap scenarios.
    tmp: Option<PathBuf>,
    env: Vec<(String, String)>,
    exit_code: Option<i32>,
    output: String,
}

/// Guard overrides leak in from the ambient shell (a human debugging a push, or
/// CI). Scenarios that want one set it explicitly; every other run starts clean.
const OVERRIDE_ENV: [&str; 3] =
    ["HARNESS_ALLOW_PROTECTED_PUSH", "HARNESS_ALLOW_ARCH_CONFIG", "HARNESS_PRE_PUSH_REFS"];

impl CrateWorld {
    fn make_tmp(&mut self) -> PathBuf {
        let dir = tempdir();
        fs::create_dir_all(dir.join("src")).expect("create src/");
        fs::write(dir.join("src").join("stub.rs"), STUB_RS).expect("write stub.rs");
        self.tmp = Some(dir.clone());
        dir
    }
}

// Minimal stand-in for the `tempfile` crate to avoid adding a dev-dep just
// for this. Returns a unique tmp path; the scenario removes it explicitly.
fn tempdir() -> PathBuf {
    let base = std::env::temp_dir();
    let pid = std::process::id();
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let dir = base.join(format!("crap-rs-{pid}-{nanos}"));
    fs::create_dir_all(&dir).expect("create tmp dir");
    dir
}

#[given("a fresh crate handle")]
fn fresh_handle(world: &mut CrateWorld) {
    world.name = None;
}

#[when("I read the crate name")]
fn read_name(world: &mut CrateWorld) {
    world.name = Some(my_project::NAME);
}

#[then("the name is not empty")]
fn name_not_empty(world: &mut CrateWorld) {
    let name = world.name.expect("crate name was never read");
    assert!(!name.is_empty(), "crate NAME is empty");
}

#[given("a coverage artifact for a high-CCN, zero-coverage function")]
fn artifact_present(world: &mut CrateWorld) {
    let dir = world.make_tmp();
    fs::create_dir_all(dir.join("target").join("llvm-cov")).expect("create llvm-cov dir");
    fs::write(dir.join("target").join("llvm-cov").join("lcov.info"), ZERO_COVERAGE_LCOV)
        .expect("write lcov.info");
}

#[given("no coverage artifact")]
fn artifact_missing(world: &mut CrateWorld) {
    world.make_tmp();
}

#[given(expr = "a git repo on branch {string}")]
fn git_repo_on_branch(world: &mut CrateWorld, branch: String) {
    let dir = tempdir();
    world.tmp = Some(dir.clone());
    git(&dir, &["init", "-q", "-b", &branch]);
    git(
        &dir,
        &[
            "-c",
            "user.email=harness@example.com",
            "-c",
            "user.name=harness",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "root",
        ],
    );
}

#[given(expr = "the push refs are {string}")]
fn push_refs_are(world: &mut CrateWorld, refs: String) {
    let dir = world.tmp.clone().expect("tmp dir not initialised");
    let head = git_out(&dir, &["rev-parse", "HEAD"]);
    world.env.push(("HARNESS_PRE_PUSH_REFS".to_string(), refs.replace("<HEAD>", &head)));
}

#[given("the arch config is staged")]
fn arch_config_staged(world: &mut CrateWorld) {
    let dir = world.tmp.clone().expect("tmp dir not initialised");
    fs::write(dir.join("arch.toml"), "[rules]\n").expect("write arch.toml");
    git(&dir, &["add", "arch.toml"]);
}

#[given("the protected-push override is set")]
fn protected_push_override(world: &mut CrateWorld) {
    world.env.push(("HARNESS_ALLOW_PROTECTED_PUSH".to_string(), "1".to_string()));
}

/// Branch pushed for the first time: `origin/main` exists, the arch config
/// changed in an earlier commit, and the tip commit touches something else.
#[given("a new branch whose earlier commit changed the arch config")]
fn branch_with_earlier_arch_change(world: &mut CrateWorld) {
    let dir = tempdir();
    world.tmp = Some(dir.clone());
    git(&dir, &["init", "-q", "-b", "feature/arch"]);
    fs::write(dir.join("arch.toml"), "[rules]\n").expect("write arch.toml");
    commit(&dir, "base");
    git(&dir, &["update-ref", "refs/remotes/origin/main", "HEAD"]);
    fs::write(dir.join("arch.toml"), "[rules]\nrelaxed = true\n").expect("rewrite arch.toml");
    commit(&dir, "relax arch rules");
    fs::write(dir.join("notes.md"), "unrelated\n").expect("write notes.md");
    commit(&dir, "unrelated");
}

/// Branch pushed for the first time: `origin/main` exists, an earlier commit
/// on the branch *deletes* the arch config, and the tip commit touches
/// something else. `git diff --diff-filter=d` hides deletions, so this is the
/// regression case for the guard dropping that filter.
#[given("a new branch whose earlier commit deleted the arch config")]
fn branch_with_earlier_arch_deletion(world: &mut CrateWorld) {
    let dir = tempdir();
    world.tmp = Some(dir.clone());
    git(&dir, &["init", "-q", "-b", "feature/arch"]);
    fs::write(dir.join("arch.toml"), "[rules]\n").expect("write arch.toml");
    commit(&dir, "base");
    git(&dir, &["update-ref", "refs/remotes/origin/main", "HEAD"]);
    fs::remove_file(dir.join("arch.toml")).expect("delete arch.toml");
    commit(&dir, "delete arch config");
    fs::write(dir.join("notes.md"), "unrelated\n").expect("write notes.md");
    commit(&dir, "unrelated");
}

fn commit(dir: &PathBuf, message: &str) {
    git(dir, &["add", "-A"]);
    git(
        dir,
        &[
            "-c",
            "user.email=harness@example.com",
            "-c",
            "user.name=harness",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
        ],
    );
}

fn git_out(dir: &PathBuf, args: &[&str]) -> String {
    let output = Command::new("git").args(args).current_dir(dir).output().expect("spawn git");
    assert!(output.status.success(), "git {args:?} failed in {dir:?}");
    String::from_utf8_lossy(&output.stdout).trim().to_string()
}

fn git(dir: &PathBuf, args: &[&str]) {
    let status = Command::new("git").args(args).current_dir(dir).status().expect("spawn git");
    assert!(status.success(), "git {args:?} failed in {dir:?}");
}

#[when(expr = "I run {string}")]
fn i_run(world: &mut CrateWorld, cmd: String) {
    // Drop leading "harness" — the rest is forwarded to the binary.
    let mut argv: Vec<&str> = cmd.split_whitespace().collect();
    if !argv.is_empty() && argv[0] == "harness" {
        argv.remove(0);
    }
    let tmp = world.tmp.as_ref().expect("tmp dir not initialised");
    let mut command = Command::new(HARNESS_BIN);
    command
        .args(&argv)
        // Null stdin keeps commands that read git pre-push refs deterministic.
        .stdin(Stdio::null())
        .current_dir(tmp);
    for key in OVERRIDE_ENV {
        command.env_remove(key);
    }
    for (key, value) in &world.env {
        command.env(key, value);
    }
    let output = command.output().expect("spawn harness binary");
    world.exit_code = output.status.code();
    world.output = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    if let Some(t) = world.tmp.take() {
        let _ = fs::remove_dir_all(&t);
    }
}

#[then(expr = "the exit code is {int}")]
fn exit_code_is(world: &mut CrateWorld, code: i32) {
    assert_eq!(
        world.exit_code,
        Some(code),
        "expected exit {code}, got {:?}\n--- output ---\n{}",
        world.exit_code,
        world.output,
    );
}

#[then(expr = "the output contains {string}")]
fn output_contains(world: &mut CrateWorld, text: String) {
    assert!(world.output.contains(&text), "expected {text:?} in output:\n{}", world.output,);
}

#[then(expr = "the output does not contain {string}")]
fn output_does_not_contain(world: &mut CrateWorld, text: String) {
    assert!(!world.output.contains(&text), "unexpected {text:?} in output:\n{}", world.output,);
}

#[tokio::main]
async fn main() {
    CrateWorld::run("tests/features").await;
}
