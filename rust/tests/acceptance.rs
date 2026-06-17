//! Acceptance test runner: executes Gherkin scenarios under `tests/features/`.
//!
//! Run via `cargo harness acceptance` (or `cargo test --test acceptance`).
//! Declared in Cargo.toml with `harness = false` so cucumber owns the output.

use std::fs;
use std::path::PathBuf;
use std::process::Command;

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
    exit_code: Option<i32>,
    output: String,
}

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

#[when(expr = "I run {string}")]
fn i_run(world: &mut CrateWorld, cmd: String) {
    // Drop leading "harness" — the rest is forwarded to the binary.
    let mut argv: Vec<&str> = cmd.split_whitespace().collect();
    if !argv.is_empty() && argv[0] == "harness" {
        argv.remove(0);
    }
    let tmp = world.tmp.as_ref().expect("tmp dir not initialised");
    let output = Command::new(HARNESS_BIN)
        .args(&argv)
        .current_dir(tmp)
        .output()
        .expect("spawn harness binary");
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
