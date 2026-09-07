import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from behave import given, then, when

TEMPLATE_ROOT = Path(__file__).resolve().parents[3]
MAKE = shutil.which("make")
if MAKE is None:
    raise RuntimeError("make is required for workspace acceptance tests")

FAKE_WORKSPACE = r"""#!/bin/sh
set -eu

case $0 in
  */*) script_dir=${0%/*} ;;
  *) script_dir=. ;;
esac
root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
state=$root/.fake-workspace
logs=$root/.fake-logs
offline=${OFFLINE:-0}
mkdir -p "$logs"
printf '%s\t%s\n' "$offline" "$*" >>"$logs/operations"

require_python_profile() {
  [ "$#" -eq 2 ] && [ "$2" = python ] || {
    printf '%s\n' 'fake workspace: expected the Python profile' >&2
    exit 64
  }
}

case ${1:-} in
  preflight)
    require_python_profile "$@"
    if [ -f "$root/.fake-unmanaged-hook" ]; then
      printf '%s\n' 'fake workspace: unmanaged hook' >&2
      exit 65
    fi
    ;;
  install)
    require_python_profile "$@"
    if [ "$offline" = 1 ] && [ ! -f "$state/cache/tools" ]; then
      printf '%s\n' 'fake workspace: cold offline tool cache' >&2
      exit 66
    fi
    if [ ! -f "$state/cache/tools" ]; then
      printf '%s\n' 'python tools' >>"$logs/downloads"
      mkdir -p "$state/cache"
      printf '%s\n' 'cached Python tools' >"$state/cache/tools"
    fi
    if [ ! -f "$state/tools/python" ]; then
      mkdir -p "$state/tools"
      printf '%s\n' 'managed Python profile' >"$state/tools/python"
    fi
    ;;
  exec)
    [ "${2:-}" = python ] && [ "${3:-}" = -- ] || {
      printf '%s\n' 'fake workspace: malformed managed exec' >&2
      exit 67
    }
    [ -f "$state/tools/python" ] || {
      printf '%s\n' 'fake workspace: tools are not installed' >&2
      exit 68
    }
    shift 3
    case $* in
      'uv sync --locked')
        [ "$offline" = 0 ] || {
          printf '%s\n' 'fake workspace: offline dependency restore lacks --offline' >&2
          exit 69
        }
        if [ ! -f "$state/cache/dependencies" ]; then
          printf '%s\n' 'python dependencies' >>"$logs/downloads"
          mkdir -p "$state/cache"
          printf '%s\n' 'cached locked dependencies' >"$state/cache/dependencies"
        fi
        mkdir -p "$state/dependencies"
        if [ ! -f "$state/dependencies/locked" ]; then
          printf '%s\n' 'locked dependencies' >"$state/dependencies/locked"
        fi
        ;;
      'uv sync --locked --offline')
        [ "$offline" = 1 ] || {
          printf '%s\n' 'fake workspace: online dependency restore used --offline' >&2
          exit 70
        }
        [ -f "$state/cache/dependencies" ] || {
          printf '%s\n' 'fake workspace: cold offline dependency cache' >&2
          exit 71
        }
        mkdir -p "$state/dependencies"
        if [ ! -f "$state/dependencies/locked" ]; then
          printf '%s\n' 'locked dependencies' >"$state/dependencies/locked"
        fi
        ;;
      'uv run --frozen --no-sync harness check')
        [ -f "$state/dependencies/locked" ] || {
          printf '%s\n' 'fake workspace: checks ran before dependencies' >&2
          exit 72
        }
        ;;
      *)
        printf 'fake workspace: unexpected managed command: %s\n' "$*" >&2
        exit 73
        ;;
    esac
    ;;
  sync-skills)
    [ "$#" -eq 1 ] || exit 74
    [ -f "$state/tools/python" ] && [ -f "$state/dependencies/locked" ] || exit 75
    if [ ! -f "$state/skills/harness/SKILL.md" ]; then
      mkdir -p "$state/skills/harness"
      printf '%s\n' 'managed harness skill' >"$state/skills/harness/SKILL.md"
    fi
    ;;
  install-hooks)
    [ "$#" -eq 1 ] || exit 76
    [ -f "$state/tools/python" ] && [ -f "$state/dependencies/locked" ] || exit 77
    if [ ! -f "$state/hooks/pre-commit" ]; then
      mkdir -p "$state/hooks"
      printf '%s\n' '#!/bin/sh' 'exit 0' >"$state/hooks/pre-commit"
      chmod +x "$state/hooks/pre-commit"
    fi
    ;;
  verify)
    require_python_profile "$@"
    [ -f "$state/tools/python" ] || exit 78
    [ -f "$state/hooks/pre-commit" ] || exit 79
    [ -f "$state/skills/harness/SKILL.md" ] || exit 80
    ;;
  *)
    printf 'fake workspace: unexpected operation: %s\n' "${1:-}" >&2
    exit 81
    ;;
esac
"""

POISON_UV = r"""#!/bin/sh
printf '%s\n' 'ambient uv executed' >>"$POISON_UV_LOG"
exit 97
"""


@given("a fresh python environment")
def step_fresh_env(context):
    context.error = None
    context.module = None


@when("I import src")
def step_import_src(context):
    try:
        import src

        context.module = src
    except Exception as exc:
        context.error = exc


@then("no exception is raised")
def step_no_exception(context):
    assert context.error is None, f"unexpected error: {context.error}"
    assert context.module is not None


def _write_executable(path, content):
    path.write_text(content)
    path.chmod(0o755)


def _managed_snapshot(root):
    state = root / ".fake-workspace"
    entries = []
    if not state.exists():
        return tuple(entries)
    for path in sorted(state.rglob("*")):
        relative = path.relative_to(state).as_posix()
        mode = path.stat().st_mode & 0o777
        if path.is_dir():
            entries.append((f"{relative}/", mode, b""))
        else:
            entries.append((relative, mode, path.read_bytes()))
    return tuple(entries)


def _mutation_snapshot(root):
    state = root / ".fake-workspace"
    entries = []
    for name in ("hooks", "skills"):
        path = state / name
        if not path.exists():
            entries.append((name, None))
            continue
        entries.append((name, _managed_snapshot_for(path)))
    return tuple(entries)


def _managed_snapshot_for(root):
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.stat().st_mode & 0o777
        content = b"" if path.is_dir() else path.read_bytes()
        entries.append((relative, mode, content))
    return tuple(entries)


def _read_log(path):
    if not path.exists():
        return []
    return path.read_text().splitlines()


def _new_isolated_repository(context):
    root = Path(tempfile.mkdtemp(prefix="python-workspace-"))
    context.add_cleanup(shutil.rmtree, root, ignore_errors=True)
    shutil.copy2(TEMPLATE_ROOT / "Makefile", root / "Makefile")
    (root / ".harness").mkdir()
    _write_executable(root / ".harness" / "workspace.sh", FAKE_WORKSPACE)
    poison_bin = root / ".poison-bin"
    poison_bin.mkdir()
    _write_executable(poison_bin / "uv", POISON_UV)
    logs = root / ".fake-logs"
    logs.mkdir()
    (logs / "operations").write_text("")
    (logs / "downloads").write_text("")
    (logs / "poison-uv").write_text("")
    context.workspace_root = root
    context.operation_log = logs / "operations"
    context.download_log = logs / "downloads"
    context.poison_log = logs / "poison-uv"
    context.poison_bin = poison_bin
    context.mutations_before = _mutation_snapshot(root)
    context.downloads_before = context.download_log.read_bytes()


def _run_make(context, target, offline):
    environment = os.environ.copy()
    environment["PATH"] = f"{context.poison_bin}{os.pathsep}{environment['PATH']}"
    environment["OFFLINE"] = "1" if offline else "0"
    environment["POISON_UV_LOG"] = str(context.poison_log)
    return subprocess.run(
        [MAKE, "--no-print-directory", target],
        cwd=context.workspace_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_make_dry(context, target, offline):
    environment = os.environ.copy()
    environment["PATH"] = f"{context.poison_bin}{os.pathsep}{environment['PATH']}"
    environment["OFFLINE"] = "1" if offline else "0"
    environment["POISON_UV_LOG"] = str(context.poison_log)
    return subprocess.run(
        [MAKE, "--no-print-directory", "--dry-run", target],
        cwd=context.workspace_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_make_dispatch_contract(context):
    setup_hooks = _run_make_dry(context, "setup-hooks", offline=True)
    _assert_command_succeeded(setup_hooks)
    assert setup_hooks.stdout.splitlines() == ["OFFLINE=1 .harness/workspace.sh install-hooks"], (
        setup_hooks.stdout
    )
    assert " uv " not in setup_hooks.stdout
    assert " harness setup-hooks" not in setup_hooks.stdout

    for offline in (False, True):
        check = _run_make_dry(context, "check", offline=offline)
        _assert_command_succeeded(check)
        offline_value = "1" if offline else "0"
        assert check.stdout.splitlines() == [
            f"OFFLINE={offline_value} .harness/workspace.sh exec python -- "
            "uv run --frozen --no-sync harness check"
        ], check.stdout

    _assert_poison_unused(context)


def _operation_records(path):
    records = []
    for line in _read_log(path):
        offline, separator, operation = line.partition("\t")
        assert separator, f"malformed operation record: {line!r}"
        records.append((offline, operation))
    return records


def _assert_command_succeeded(result):
    assert result.returncode == 0, (
        f"expected workspace success, got {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def _assert_poison_unused(context):
    assert context.poison_log.read_text() == "", "workspace invoked poisoned ambient uv"


@given("an isolated clean Python template repository")
def step_clean_python_repository(context):
    _new_isolated_repository(context)
    _assert_make_dispatch_contract(context)


@when("I run the Python workspace target online")
def step_run_python_workspace_online(context):
    context.result = _run_make(context, "workspace", offline=False)


@then("the workspace command succeeds")
def step_workspace_succeeds(context):
    _assert_command_succeeded(context.result)
    _assert_poison_unused(context)


@then("the workspace operations run in order:")
def step_workspace_operations_in_order(context):
    records = _operation_records(context.operation_log)
    actual = [operation for _offline, operation in records]
    expected = [row["operation"] for row in context.table]
    expected = [
        "exec python -- uv run --frozen --no-sync harness check"
        if operation == "exec python -- uv run harness check"
        else operation
        for operation in expected
    ]
    assert actual == expected, f"expected operations {expected!r}, got {actual!r}"
    assert all(offline == "0" for offline, _operation in records), records


@given("an isolated warm Python template repository")
def step_warm_python_repository(context):
    _new_isolated_repository(context)
    _assert_make_dispatch_contract(context)
    initial_result = _run_make(context, "workspace", offline=False)
    _assert_command_succeeded(initial_result)
    _assert_poison_unused(context)
    context.managed_snapshot_before = _managed_snapshot(context.workspace_root)
    context.downloads_before_reruns = context.download_log.read_bytes()
    context.operation_log.write_text("")


@when("I rerun the Python workspace target online and bootstrap offline")
def step_rerun_online_and_bootstrap_offline(context):
    context.online_result = _run_make(context, "workspace", offline=False)
    context.online_operations = _operation_records(context.operation_log)
    context.managed_snapshot_after_online = _managed_snapshot(context.workspace_root)
    context.operation_log.write_text("")
    context.offline_result = _run_make(context, "bootstrap", offline=True)
    context.offline_operations = _operation_records(context.operation_log)
    context.managed_snapshot_after_offline = _managed_snapshot(context.workspace_root)


@then("both workspace commands succeed")
def step_both_workspace_commands_succeed(context):
    _assert_command_succeeded(context.online_result)
    _assert_command_succeeded(context.offline_result)
    _assert_poison_unused(context)


@then('online dependencies use "{command}"')
def step_online_dependencies(context, command):
    expected = f"exec python -- {command}"
    assert context.online_operations.count(("0", expected)) == 1, context.online_operations


@then('offline dependencies use "{command}"')
def step_offline_dependencies(context, command):
    expected = f"exec python -- {command}"
    assert context.offline_operations.count(("1", expected)) == 1, context.offline_operations
    assert all(offline == "1" for offline, _operation in context.offline_operations)


@then("the managed workspace snapshot is unchanged")
def step_managed_snapshot_unchanged(context):
    before = context.managed_snapshot_before
    assert context.managed_snapshot_after_online == before
    assert context.managed_snapshot_after_offline == before
    assert context.download_log.read_bytes() == context.downloads_before_reruns


@given("an isolated cold offline Python template repository")
def step_cold_offline_python_repository(context):
    _new_isolated_repository(context)
    _assert_make_dispatch_contract(context)


@when("I run the Python workspace target offline")
def step_run_python_workspace_offline(context):
    context.result = _run_make(context, "workspace", offline=True)


@then("the workspace command fails during tool installation")
def step_workspace_fails_during_install(context):
    assert context.result.returncode != 0
    records = _operation_records(context.operation_log)
    assert records == [("1", "preflight python"), ("1", "install python")], records
    assert "cold offline tool cache" in context.result.stderr
    _assert_poison_unused(context)


@then("neither hooks nor skills are modified")
def step_hooks_and_skills_unchanged(context):
    assert _mutation_snapshot(context.workspace_root) == context.mutations_before


@given("an isolated Python template repository with an unmanaged hook")
def step_python_repository_with_unmanaged_hook(context):
    _new_isolated_repository(context)
    _assert_make_dispatch_contract(context)
    (context.workspace_root / ".fake-unmanaged-hook").write_text("unmanaged\n")


@then("the workspace command fails during preflight")
def step_workspace_fails_during_preflight(context):
    assert context.result.returncode != 0
    records = _operation_records(context.operation_log)
    assert records == [("0", "preflight python")], records
    assert "unmanaged hook" in context.result.stderr
    _assert_poison_unused(context)


@then("no tools are downloaded")
def step_no_tools_downloaded(context):
    assert context.download_log.read_bytes() == context.downloads_before
