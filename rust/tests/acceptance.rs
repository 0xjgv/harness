//! Acceptance test runner: executes Gherkin scenarios under `tests/features/`.
//!
//! Run via `cargo harness acceptance` (or `cargo test --test acceptance`).
//! Declared in Cargo.toml with `harness = false` so cucumber owns the output.

use std::fs;
use std::io::Write as _;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use cucumber::gherkin::Step;
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
    // Hook scenarios: the crate under test (inside `tmp`), what the hook reads on
    // stdin, and its two output streams apart.
    project: Option<PathBuf>,
    hook_input: String,
    stdout: String,
    stderr: String,
}

impl Drop for CrateWorld {
    fn drop(&mut self) {
        if let Some(tmp) = self.tmp.take() {
            let _ = fs::remove_dir_all(tmp);
        }
    }
}

/// Guard overrides leak in from the ambient shell (a human debugging a push, or
/// CI). Scenarios that want one set it explicitly; every other run starts clean.
const OVERRIDE_ENV: [&str; 3] =
    ["HARNESS_ALLOW_PROTECTED_PUSH", "HARNESS_ALLOW_ARCH_CONFIG", "HARNESS_PRE_PUSH_REFS"];

/// Variables that point a child at another repository or base: git exports the
/// first three to hooks (a test run from one would write into the real repo), and
/// the last two move the stop hook's base.
const REPO_ENV: [&str; 5] =
    ["GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "HARNESS_ARCH_BASE", "GITHUB_BASE_REF"];

fn isolated(mut command: Command) -> Command {
    for key in OVERRIDE_ENV.iter().chain(&REPO_ENV) {
        command.env_remove(key);
    }
    command
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
    let output =
        isolated(Command::new("git")).args(args).current_dir(dir).output().expect("spawn git");
    assert!(output.status.success(), "git {args:?} failed in {dir:?}");
    String::from_utf8_lossy(&output.stdout).trim().to_string()
}

fn git(dir: &PathBuf, args: &[&str]) {
    let status =
        isolated(Command::new("git")).args(args).current_dir(dir).status().expect("spawn git");
    assert!(status.success(), "git {args:?} failed in {dir:?}");
}

#[when(expr = "I run {string}")]
fn i_run(world: &mut CrateWorld, cmd: String) {
    // Drop leading "harness" — the rest is forwarded to the binary.
    let mut argv: Vec<&str> = cmd.split_whitespace().collect();
    if !argv.is_empty() && argv[0] == "harness" {
        argv.remove(0);
    }
    let dir = world.project.clone().or_else(|| world.tmp.clone()).expect("tmp dir not initialised");
    let mut command = isolated(Command::new(HARNESS_BIN));
    command
        .args(&argv)
        // Stdin carries the hook input and then closes, so commands that read git
        // pre-push refs stay deterministic.
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // A crate under test builds into its own target dir, never a shared one.
        .env("CARGO_TARGET_DIR", dir.join("target"))
        .current_dir(&dir);
    for (key, value) in &world.env {
        command.env(key, value);
    }
    let mut child = command.spawn().expect("spawn harness binary");
    let mut stdin = child.stdin.take().expect("piped stdin");
    stdin.write_all(world.hook_input.as_bytes()).expect("write hook input");
    drop(stdin);
    let output = child.wait_with_output().expect("wait for harness binary");
    world.exit_code = output.status.code();
    world.stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    world.stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    world.output = format!("{}{}", world.stdout, world.stderr);
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

// ── Stop hook, post-edit hook, AGENTS.md mirror ────────────────────

/// A formatted function whose CCN is `branches + 1`.
fn branchy(name: &str, branches: usize) -> String {
    let body: String = (0..branches)
        .map(|i| format!("    if value == {i} {{\n        return {i};\n    }}\n"))
        .collect();
    format!("pub fn {name}(value: i32) -> i32 {{\n{body}    -1\n}}\n")
}

/// A docstring as file content: gherkin keeps the newline after the opening quotes.
fn docstring(step: &Step) -> String {
    let text = step.docstring.as_deref().expect("step has no docstring");
    format!("{}\n", text.strip_prefix('\n').unwrap_or(text).trim_end())
}

impl CrateWorld {
    fn project(&self) -> PathBuf {
        self.project.clone().expect("project not initialised")
    }

    fn write(&self, relative: &str, text: &str) {
        let path = self.project().join(relative);
        fs::create_dir_all(path.parent().expect("file has a parent")).expect("create parent");
        fs::write(path, text).expect("write file");
    }
}

#[given(expr = "a crate in {string}")]
fn crate_in(world: &mut CrateWorld, subdir: String) {
    let repo = tempdir();
    git(&repo, &["init", "-q", "-b", "main"]);
    world.project = Some(repo.join(subdir));
    world.tmp = Some(repo);
    world.write(
        "Cargo.toml",
        "[package]\nname = \"stub\"\nversion = \"0.1.0\"\nedition = \"2024\"\n",
    );
    world.write(".gitignore", "/target/\n");
    world.write("CLAUDE.md", "docs\n");
    world.write("AGENTS.md", "docs\n");
    world.write("src/lib.rs", "pub fn helper() -> i32 {\n    1\n}\n"); // lines 1-3
}

#[given(expr = "{string} gains {string} with {int} branches")]
fn file_gains_branchy(world: &mut CrateWorld, file: String, name: String, branches: usize) {
    let text = fs::read_to_string(world.project().join(&file)).expect("read file");
    world.write(&file, &format!("{text}\n{}", branchy(&name, branches)));
}

#[given(expr = "{string} is written as:")]
fn file_written(world: &mut CrateWorld, file: String, step: &Step) {
    world.write(&file, &docstring(step));
}

#[given("the base is committed on main")]
fn base_committed(world: &mut CrateWorld) {
    let repo = world.project(); // git works from a subdirectory too
    commit(&repo, "base");
    git(&repo, &["update-ref", "refs/remotes/origin/main", "HEAD"]);
    git(&repo, &["checkout", "-q", "-b", "feature"]);
}

#[given("the change is committed")]
fn change_committed(world: &mut CrateWorld) {
    commit(&world.project(), "change");
}

#[given(expr = "{string} is staged")]
fn file_staged(world: &mut CrateWorld, file: String) {
    git(&world.project(), &["add", "--", &file]);
}

#[given(expr = "the hook input is {string}")]
fn hook_input_is(world: &mut CrateWorld, input: String) {
    world.hook_input = input;
}

#[given(expr = "the hook input names {string}")]
fn hook_input_names(world: &mut CrateWorld, file: String) {
    let path = world.project().join(file);
    let path = path.to_str().expect("utf-8 path");
    world.hook_input = format!(r#"{{"tool_input":{{"file_path":"{path}"}}}}"#);
}

#[then(expr = "the hook exits {int} silently")]
fn hook_exits_silently(world: &mut CrateWorld, code: i32) {
    assert_eq!((world.exit_code, world.output.as_str()), (Some(code), ""));
}

/// The named stream holds exactly the docstring; the other one is empty.
#[then(expr = "the hook exits {int} with {word}:")]
fn hook_exits_with(world: &mut CrateWorld, code: i32, stream: String, step: &Step) {
    let (named, other) = match stream.as_str() {
        "stdout" => (&world.stdout, &world.stderr),
        _ => (&world.stderr, &world.stdout),
    };
    let actual = (world.exit_code, named.as_str(), other.as_str());
    assert_eq!(actual, (Some(code), docstring(step).as_str(), ""), "{stream} differs");
}

#[then(expr = "the staged files are {string}")]
fn staged_files(world: &mut CrateWorld, expected: String) {
    let staged = git_out(&world.project(), &["diff", "--cached", "--name-only"]);
    assert_eq!(staged.split_whitespace().collect::<Vec<_>>().join(" "), expected);
}

#[tokio::main]
async fn main() {
    CrateWorld::run("tests/features").await;
}
