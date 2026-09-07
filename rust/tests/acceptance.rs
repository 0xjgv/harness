//! Acceptance test runner: executes Gherkin scenarios under `tests/features/`.
//!
//! Run via `cargo harness acceptance` (or `cargo test --test acceptance`).
//! Declared in Cargo.toml with `harness = false` so cucumber owns the output.

use std::ffi::OsString;
use std::fs;
use std::os::unix::fs::PermissionsExt as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::OnceLock;
use std::sync::atomic::{AtomicU64, Ordering};

use cucumber::{World, gherkin, given, then, when};

/// Path to the built `harness` binary. Cargo sets this env var for integration
/// tests when the package defines a `[[bin]]` of that name, and ensures the
/// binary is built before this test runs.
const HARNESS_BIN: &str = env!("CARGO_BIN_EXE_harness");
const TEMPLATE_ROOT: &str = env!("CARGO_MANIFEST_DIR");

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

// Contract double for the template Makefile. The repository-level terminal matrix
// separately exercises the real provisioner in greenfield and brownfield clones.
const FAKE_WORKSPACE: &str = r#"#!/bin/sh
set -eu

case $0 in
  */*) script_dir=${0%/*} ;;
  *) script_dir=. ;;
esac
root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
state=$root/.fake-workspace
logs=$root/.fake-logs
offline=${OFFLINE:-0}
case $offline in
  0|1) ;;
  *) printf '%s\n' 'fake workspace: OFFLINE must be 0 or 1' >&2; exit 63 ;;
esac
unset CARGO_NET_OFFLINE
operation=$*
if [ "${1:-}" = exec ]; then
  case $offline in
    0) CARGO_NET_OFFLINE=false ;;
    1) CARGO_NET_OFFLINE=true ;;
  esac
  export CARGO_NET_OFFLINE
fi
mkdir -p "$logs"
printf '%s\t%s\t%s\n' "$offline" "${CARGO_NET_OFFLINE-unset}" "$operation" >>"$logs/operations"

require_rust_profile() {
  [ "$#" -eq 2 ] && [ "$2" = rust ] || {
    printf '%s\n' 'fake workspace: expected the Rust profile' >&2
    exit 64
  }
}

case ${1:-} in
  preflight)
    require_rust_profile "$@"
    if [ -f "$root/.fake-unmanaged-hook" ]; then
      printf '%s\n' 'fake workspace: unmanaged hook' >&2
      exit 65
    fi
    ;;
  install)
    require_rust_profile "$@"
    if [ "$offline" = 1 ] && [ ! -f "$state/cache/tools" ]; then
      printf '%s\n' 'fake workspace: cold offline tool cache' >&2
      exit 66
    fi
    if [ ! -f "$state/cache/tools" ]; then
      printf '%s\n' 'rust tools' >>"$logs/downloads"
      mkdir -p "$state/cache"
      printf '%s\n' 'cached Rust tools' >"$state/cache/tools"
    fi
    if [ ! -f "$state/tools/rust" ]; then
      mkdir -p "$state/tools"
      printf '%s\n' 'managed Rust profile' >"$state/tools/rust"
    fi
    ;;
  exec)
    [ "${2:-}" = rust ] && [ "${3:-}" = -- ] || {
      printf '%s\n' 'fake workspace: malformed managed exec' >&2
      exit 67
    }
    [ -f "$state/tools/rust" ] || {
      printf '%s\n' 'fake workspace: tools are not installed' >&2
      exit 68
    }
    shift 3
    case $* in
      'cargo fetch --locked')
        if [ "$offline" = 1 ]; then
          [ -f "$state/cache/dependencies" ] || {
            printf '%s\n' 'fake workspace: cold offline dependency cache' >&2
            exit 69
          }
        elif [ ! -f "$state/cache/dependencies" ]; then
          printf '%s\n' 'rust dependencies' >>"$logs/downloads"
          mkdir -p "$state/cache"
          printf '%s\n' 'cached locked dependencies' >"$state/cache/dependencies"
        fi
        mkdir -p "$state/dependencies"
        if [ ! -f "$state/dependencies/fetched" ]; then
          printf '%s\n' 'locked dependencies fetched' >"$state/dependencies/fetched"
        fi
        ;;
      'cargo build --locked')
        [ -f "$state/dependencies/fetched" ] || {
          printf '%s\n' 'fake workspace: build ran before fetch' >&2
          exit 70
        }
        if [ ! -f "$state/dependencies/built" ]; then
          printf '%s\n' 'locked dependencies built' >"$state/dependencies/built"
        fi
        ;;
      'cargo run --quiet --locked --bin harness -- check')
        [ -f "$state/dependencies/built" ] || {
          printf '%s\n' 'fake workspace: checks ran before dependencies' >&2
          exit 71
        }
        ;;
      *)
        printf 'fake workspace: unexpected managed command: %s\n' "$*" >&2
        exit 72
        ;;
    esac
    ;;
  sync-skills)
    [ "$#" -eq 1 ] || exit 73
    [ -f "$state/tools/rust" ] && [ -f "$state/dependencies/built" ] || exit 74
    if [ ! -f "$state/skills/harness/SKILL.md" ]; then
      mkdir -p "$state/skills/harness"
      printf '%s\n' 'managed harness skill' >"$state/skills/harness/SKILL.md"
    fi
    ;;
  install-hooks)
    [ "$#" -eq 1 ] || exit 75
    [ -f "$state/tools/rust" ] && [ -f "$state/dependencies/built" ] || exit 76
    if [ ! -f "$state/hooks/pre-commit" ]; then
      mkdir -p "$state/hooks"
      printf '%s\n' '#!/bin/sh' 'exit 0' >"$state/hooks/pre-commit"
      chmod +x "$state/hooks/pre-commit"
    fi
    ;;
  verify)
    require_rust_profile "$@"
    [ -f "$state/tools/rust" ] || exit 77
    [ -f "$state/dependencies/fetched" ] || exit 78
    [ -f "$state/dependencies/built" ] || exit 79
    [ -f "$state/hooks/pre-commit" ] || exit 80
    [ -f "$state/skills/harness/SKILL.md" ] || exit 81
    ;;
  *)
    printf 'fake workspace: unexpected operation: %s\n' "${1:-}" >&2
    exit 82
    ;;
esac
"#;

const POISON_CARGO: &str = r#"#!/bin/sh
printf '%s\n' 'ambient cargo executed' >>"$POISON_CARGO_LOG"
exit 97
"#;

const WORKSPACE_OPERATIONS: [&str; 9] = [
    "preflight rust",
    "install rust",
    "exec rust -- cargo fetch --locked",
    "exec rust -- cargo build --locked",
    "sync-skills",
    "install-hooks",
    "verify rust",
    "exec rust -- cargo run --quiet --locked --bin harness -- check",
    "verify rust",
];

/// Shared state for a single scenario. Smoke and crap fields coexist because
/// cucumber-rs binds a single World type per binary.
#[derive(Clone, Debug, Eq, PartialEq)]
struct SnapshotEntry {
    path: String,
    bytes: Vec<u8>,
    executable: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct OperationRecord {
    offline: String,
    cargo_net_offline: String,
    operation: String,
}

#[derive(Debug)]
struct CommandResult {
    exit_code: i32,
    stdout: String,
    stderr: String,
}

type Snapshot = Vec<SnapshotEntry>;
type MutationSnapshot = Vec<(String, Option<Snapshot>)>;

#[derive(Debug, Default, World)]
struct CrateWorld {
    // Smoke scenario.
    name: Option<&'static str>,
    // Crap scenarios.
    tmp: Option<PathBuf>,
    exit_code: Option<i32>,
    output: String,
    // Workspace scenarios.
    workspace_root: Option<PathBuf>,
    operation_log: Option<PathBuf>,
    download_log: Option<PathBuf>,
    poison_log: Option<PathBuf>,
    poison_bin: Option<PathBuf>,
    mutations_before: MutationSnapshot,
    downloads_before: Vec<u8>,
    downloads_before_reruns: Vec<u8>,
    managed_snapshot_before: Snapshot,
    managed_snapshot_after_online: Snapshot,
    managed_snapshot_after_offline: Snapshot,
    result: Option<CommandResult>,
    online_result: Option<CommandResult>,
    offline_result: Option<CommandResult>,
    online_operations: Vec<OperationRecord>,
    offline_operations: Vec<OperationRecord>,
}

impl CrateWorld {
    fn make_tmp(&mut self) -> PathBuf {
        let dir = tempdir("crap-rs");
        fs::create_dir_all(dir.join("src")).expect("create src/");
        fs::write(dir.join("src").join("stub.rs"), STUB_RS).expect("write stub.rs");
        self.tmp = Some(dir.clone());
        dir
    }

    fn workspace_root(&self) -> &Path {
        self.workspace_root.as_deref().expect("workspace repository not initialised")
    }

    fn operation_log(&self) -> &Path {
        self.operation_log.as_deref().expect("operation log not initialised")
    }

    fn download_log(&self) -> &Path {
        self.download_log.as_deref().expect("download log not initialised")
    }

    fn poison_log(&self) -> &Path {
        self.poison_log.as_deref().expect("poison log not initialised")
    }

    fn poison_bin(&self) -> &Path {
        self.poison_bin.as_deref().expect("poison bin not initialised")
    }
}

impl Drop for CrateWorld {
    fn drop(&mut self) {
        if let Some(path) = self.tmp.take() {
            let _ = fs::remove_dir_all(path);
        }
        if let Some(path) = self.workspace_root.take() {
            let _ = fs::remove_dir_all(path);
        }
    }
}

// Minimal stand-in for the `tempfile` crate to keep acceptance fixtures stdlib-only.
fn tempdir(prefix: &str) -> PathBuf {
    static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);
    let base = std::env::temp_dir();
    let pid = std::process::id();
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let sequence = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
    let dir = base.join(format!("{prefix}-{pid}-{nanos}-{sequence}"));
    fs::create_dir_all(&dir).expect("create tmp dir");
    dir
}

fn write_executable(path: &Path, content: &str) {
    fs::write(path, content).unwrap_or_else(|error| panic!("write {}: {error}", path.display()));
    let mut permissions = fs::metadata(path)
        .unwrap_or_else(|error| panic!("stat {}: {error}", path.display()))
        .permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(path, permissions)
        .unwrap_or_else(|error| panic!("chmod {}: {error}", path.display()));
}

fn real_make() -> &'static Path {
    static MAKE: OnceLock<PathBuf> = OnceLock::new();
    MAKE.get_or_init(|| {
        let path = std::env::var_os("PATH").unwrap_or_else(|| OsString::from("/usr/bin:/bin"));
        for directory in std::env::split_paths(&path) {
            let candidate = directory.join("make");
            let Ok(metadata) = fs::metadata(&candidate) else {
                continue;
            };
            if metadata.is_file() && metadata.permissions().mode() & 0o111 != 0 {
                let resolved = fs::canonicalize(&candidate).unwrap_or(candidate);
                assert!(
                    resolved.is_absolute(),
                    "make path is not absolute: {}",
                    resolved.display()
                );
                return resolved;
            }
        }
        panic!("make is required for workspace acceptance tests");
    })
    .as_path()
}

fn snapshot_tree(root: &Path) -> Snapshot {
    fn visit(root: &Path, directory: &Path, snapshot: &mut Snapshot) {
        let mut entries = fs::read_dir(directory)
            .unwrap_or_else(|error| panic!("read {}: {error}", directory.display()))
            .map(|entry| entry.expect("read directory entry"))
            .collect::<Vec<_>>();
        entries.sort_by_key(fs::DirEntry::file_name);
        for entry in entries {
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)
                .unwrap_or_else(|error| panic!("stat {}: {error}", path.display()));
            let relative = path.strip_prefix(root).expect("snapshot path is outside root");
            let mut display = relative.to_string_lossy().replace(std::path::MAIN_SEPARATOR, "/");
            if metadata.is_dir() {
                display.push('/');
            }
            snapshot.push(SnapshotEntry {
                path: display,
                bytes: if metadata.is_dir() {
                    Vec::new()
                } else {
                    fs::read(&path)
                        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()))
                },
                executable: metadata.permissions().mode() & 0o111 != 0,
            });
            if metadata.is_dir() {
                visit(root, &path, snapshot);
            }
        }
    }

    if !root.exists() {
        return Vec::new();
    }
    let mut snapshot = Vec::new();
    visit(root, root, &mut snapshot);
    snapshot
}

fn managed_snapshot(root: &Path) -> Snapshot {
    snapshot_tree(&root.join(".fake-workspace"))
}

fn mutation_snapshot(root: &Path) -> MutationSnapshot {
    let state = root.join(".fake-workspace");
    ["hooks", "skills"]
        .into_iter()
        .map(|name| {
            let path = state.join(name);
            (name.to_owned(), path.exists().then(|| snapshot_tree(&path)))
        })
        .collect()
}

fn read_lines(path: &Path) -> Vec<String> {
    let content =
        fs::read_to_string(path).unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    content.lines().map(str::to_owned).collect()
}

fn operation_records(path: &Path) -> Vec<OperationRecord> {
    read_lines(path)
        .into_iter()
        .map(|line| {
            let fields = line.splitn(3, '\t').collect::<Vec<_>>();
            assert_eq!(fields.len(), 3, "malformed operation record: {line:?}");
            OperationRecord {
                offline: fields[0].to_owned(),
                cargo_net_offline: fields[1].to_owned(),
                operation: fields[2].to_owned(),
            }
        })
        .collect()
}

fn assert_workspace_sequence(records: &[OperationRecord], offline: &str, cargo_net_offline: &str) {
    let actual = records.iter().map(|record| record.operation.as_str()).collect::<Vec<_>>();
    assert_eq!(actual, WORKSPACE_OPERATIONS, "workspace operations ran out of order");
    assert!(records.iter().all(|record| record.offline == offline));
    assert!(records.iter().all(|record| {
        let expected =
            if record.operation.starts_with("exec rust --") { cargo_net_offline } else { "unset" };
        record.cargo_net_offline == expected
    }));
}

fn setup_workspace(world: &mut CrateWorld) {
    let root = tempdir("rust-workspace");
    world.workspace_root = Some(root.clone());
    fs::copy(Path::new(TEMPLATE_ROOT).join("Makefile"), root.join("Makefile"))
        .expect("copy Rust template Makefile");

    let harness_dir = root.join(".harness");
    fs::create_dir(&harness_dir).expect("create .harness");
    write_executable(&harness_dir.join("workspace.sh"), FAKE_WORKSPACE);

    let poison_bin = root.join(".poison-bin");
    fs::create_dir(&poison_bin).expect("create poison bin");
    write_executable(&poison_bin.join("cargo"), POISON_CARGO);

    let logs = root.join(".fake-logs");
    fs::create_dir(&logs).expect("create fake logs");
    let operation_log = logs.join("operations");
    let download_log = logs.join("downloads");
    let poison_log = logs.join("poison-cargo");
    for path in [&operation_log, &download_log, &poison_log] {
        fs::write(path, []).unwrap_or_else(|error| panic!("write {}: {error}", path.display()));
    }

    world.operation_log = Some(operation_log);
    world.download_log = Some(download_log.clone());
    world.poison_log = Some(poison_log);
    world.poison_bin = Some(poison_bin);
    world.mutations_before = mutation_snapshot(&root);
    world.downloads_before = fs::read(download_log).expect("read initial downloads");
}

fn command_result(output: Output) -> CommandResult {
    CommandResult {
        exit_code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

fn run_make(world: &CrateWorld, target: &str, offline: bool) -> CommandResult {
    let mut paths = vec![world.poison_bin().to_path_buf()];
    if let Some(path) = std::env::var_os("PATH") {
        paths.extend(std::env::split_paths(&path));
    }
    let path = std::env::join_paths(paths).expect("join poisoned PATH");
    let offline_assignment = if offline { "OFFLINE=1" } else { "OFFLINE=0" };
    let output = Command::new(real_make())
        .args(["--no-print-directory", target, offline_assignment])
        .current_dir(world.workspace_root())
        .env("PATH", path)
        .env("POISON_CARGO_LOG", world.poison_log())
        .output()
        .expect("spawn absolute make");
    command_result(output)
}

fn assert_command_succeeded(result: &CommandResult) {
    assert_eq!(
        result.exit_code, 0,
        "expected workspace success, got {}\n--- stdout ---\n{}\n--- stderr ---\n{}",
        result.exit_code, result.stdout, result.stderr,
    );
}

fn assert_poison_unused(world: &CrateWorld) {
    assert_eq!(
        fs::read(world.poison_log()).expect("read Cargo poison log"),
        Vec::<u8>::new(),
        "workspace invoked poisoned ambient Cargo",
    );
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

#[given("an isolated clean Rust template repository")]
fn clean_rust_repository(world: &mut CrateWorld) {
    setup_workspace(world);
}

#[when("I run the Rust workspace target online")]
fn run_rust_workspace_online(world: &mut CrateWorld) {
    world.result = Some(run_make(world, "workspace", false));
}

#[then("the workspace command succeeds")]
fn workspace_succeeds(world: &mut CrateWorld) {
    assert_command_succeeded(world.result.as_ref().expect("workspace result missing"));
    assert_poison_unused(world);
}

#[then("the workspace operations run in order:")]
fn workspace_operations_in_order(world: &mut CrateWorld, step: &gherkin::Step) {
    let table = step.table.as_ref().expect("workspace operation table is missing");
    assert_eq!(
        table.rows.first(),
        Some(&vec!["operation".to_owned()]),
        "workspace operation table header differs",
    );
    let expected = table
        .rows
        .iter()
        .skip(1)
        .map(|row| {
            assert_eq!(row.len(), 1, "workspace operation row must have one cell");
            row[0].clone()
        })
        .collect::<Vec<_>>();
    let records = operation_records(world.operation_log());
    let actual = records.iter().map(|record| record.operation.clone()).collect::<Vec<_>>();
    assert_eq!(actual, expected, "workspace operations ran out of order");
    assert!(records.iter().all(|record| record.offline == "0"));
    assert!(records.iter().all(|record| {
        let expected = if record.operation.starts_with("exec rust --") { "false" } else { "unset" };
        record.cargo_net_offline == expected
    }));
}

#[given("an isolated warm Rust template repository")]
fn warm_rust_repository(world: &mut CrateWorld) {
    setup_workspace(world);
    let initial = run_make(world, "workspace", false);
    assert_command_succeeded(&initial);
    assert_poison_unused(world);
    world.managed_snapshot_before = managed_snapshot(world.workspace_root());
    world.downloads_before_reruns = fs::read(world.download_log()).expect("read warm download log");
    fs::write(world.operation_log(), []).expect("clear operation log");
}

#[when("I rerun the Rust workspace target online and bootstrap offline")]
fn rerun_rust_workspace_online_and_offline(world: &mut CrateWorld) {
    let online_result = run_make(world, "workspace", false);
    world.online_operations = operation_records(world.operation_log());
    world.managed_snapshot_after_online = managed_snapshot(world.workspace_root());
    fs::write(world.operation_log(), []).expect("clear online operation log");

    let offline_result = run_make(world, "bootstrap", true);
    world.offline_operations = operation_records(world.operation_log());
    world.managed_snapshot_after_offline = managed_snapshot(world.workspace_root());
    world.online_result = Some(online_result);
    world.offline_result = Some(offline_result);
}

#[then("both workspace commands succeed")]
fn both_workspace_commands_succeed(world: &mut CrateWorld) {
    assert_command_succeeded(world.online_result.as_ref().expect("online result missing"));
    assert_command_succeeded(world.offline_result.as_ref().expect("offline result missing"));
    assert_workspace_sequence(&world.online_operations, "0", "false");
    assert_workspace_sequence(&world.offline_operations, "1", "true");
    assert_poison_unused(world);
}

#[then("dependencies use locked Cargo fetch and build commands")]
fn dependencies_are_locked(world: &mut CrateWorld) {
    let expected = ["exec rust -- cargo fetch --locked", "exec rust -- cargo build --locked"];
    for (label, operations) in
        [("online", &world.online_operations), ("offline", &world.offline_operations)]
    {
        let actual = operations
            .iter()
            .filter_map(|record| {
                expected.contains(&record.operation.as_str()).then_some(record.operation.as_str())
            })
            .collect::<Vec<_>>();
        assert_eq!(actual, expected, "{label} dependency operations differ");
    }
}

#[then("offline dependency restoration disables Cargo networking")]
fn offline_dependencies_disable_network(world: &mut CrateWorld) {
    let dependency_operations = world
        .offline_operations
        .iter()
        .filter(|record| {
            matches!(
                record.operation.as_str(),
                "exec rust -- cargo fetch --locked" | "exec rust -- cargo build --locked"
            )
        })
        .collect::<Vec<_>>();
    assert_eq!(dependency_operations.len(), 2, "offline dependency operation count differs");
    assert!(
        dependency_operations
            .iter()
            .all(|record| { record.offline == "1" && record.cargo_net_offline == "true" })
    );
}

#[then("the managed workspace snapshot is unchanged")]
fn managed_workspace_is_unchanged(world: &mut CrateWorld) {
    assert_eq!(world.managed_snapshot_after_online, world.managed_snapshot_before);
    assert_eq!(world.managed_snapshot_after_offline, world.managed_snapshot_before);
    assert_eq!(
        fs::read(world.download_log()).expect("read final download log"),
        world.downloads_before_reruns,
        "warm reruns downloaded artifacts",
    );
}

#[given("an isolated cold offline Rust template repository")]
fn cold_offline_rust_repository(world: &mut CrateWorld) {
    setup_workspace(world);
}

#[when("I run the Rust workspace target offline")]
fn run_rust_workspace_offline(world: &mut CrateWorld) {
    world.result = Some(run_make(world, "workspace", true));
}

#[then("the workspace command fails during tool installation")]
fn workspace_fails_during_install(world: &mut CrateWorld) {
    let result = world.result.as_ref().expect("workspace result missing");
    assert_ne!(result.exit_code, 0, "cold offline workspace unexpectedly succeeded");
    assert_eq!(
        operation_records(world.operation_log()),
        [
            OperationRecord {
                offline: "1".to_owned(),
                cargo_net_offline: "unset".to_owned(),
                operation: "preflight rust".to_owned(),
            },
            OperationRecord {
                offline: "1".to_owned(),
                cargo_net_offline: "unset".to_owned(),
                operation: "install rust".to_owned(),
            },
        ],
    );
    assert!(result.stderr.contains("cold offline tool cache"), "{}", result.stderr);
    assert_poison_unused(world);
}

#[then("neither hooks nor skills are modified")]
fn hooks_and_skills_are_unchanged(world: &mut CrateWorld) {
    assert_eq!(mutation_snapshot(world.workspace_root()), world.mutations_before);
}

#[given("an isolated Rust template repository with an unmanaged hook")]
fn rust_repository_with_unmanaged_hook(world: &mut CrateWorld) {
    setup_workspace(world);
    fs::write(world.workspace_root().join(".fake-unmanaged-hook"), "unmanaged\n")
        .expect("write unmanaged hook marker");
}

#[then("the workspace command fails during preflight")]
fn workspace_fails_during_preflight(world: &mut CrateWorld) {
    let result = world.result.as_ref().expect("workspace result missing");
    assert_ne!(result.exit_code, 0, "unmanaged-hook workspace unexpectedly succeeded");
    assert_eq!(
        operation_records(world.operation_log()),
        [OperationRecord {
            offline: "0".to_owned(),
            cargo_net_offline: "unset".to_owned(),
            operation: "preflight rust".to_owned(),
        }],
    );
    assert!(result.stderr.contains("unmanaged hook"), "{}", result.stderr);
    assert_poison_unused(world);
}

#[then("no tools are downloaded")]
fn no_tools_are_downloaded(world: &mut CrateWorld) {
    assert_eq!(fs::read(world.download_log()).expect("read download log"), world.downloads_before,);
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
