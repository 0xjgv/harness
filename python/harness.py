#!/usr/bin/env python3
"""Project development tasks. Zero dependencies — stdlib only."""

from __future__ import annotations

import concurrent.futures
import csv
import dataclasses
import functools
import hashlib
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

# ── Configuration ─────────────────────────────────────────────────

APP_SOURCES = ("src",)
QUALITY_SOURCES = ("src", "harness.py")
TEST_DIR = "tests"
LIZARD = "lizard@1.22.2"
VULTURE = "vulture@2.16"
VULTURE_MIN_CONFIDENCE = "60"
VULTURE_ALLOWLIST = "vulture_allowlist.py"
COMPLEXITY_MAX_CCN = 15
COMPLEXITY_MAX_ARGS = 8
COMPLEXITY_MAX_LENGTH = 100
BASELINE_FILE = ".harness-baseline"
SUPPRESSION_BASELINE_PREFIX = "suppressions."
ARCH_CONFIGS = (".importlinter",)
ARCH_CONFIG_ALLOW_ENV = "HARNESS_ALLOW_ARCH_CONFIG"
PROTECTED_BRANCHES = ("main", "master")
PROTECTED_PUSH_ALLOW_ENV = "HARNESS_ALLOW_PROTECTED_PUSH"
PRE_PUSH_REFS_ENV = "HARNESS_PRE_PUSH_REFS"
PRE_PUSH_STDIN_TIMEOUT = 1.0
ARCH_BASE_ENV = "HARNESS_ARCH_BASE"
ARCH_BASE_CANDIDATES = ("origin/main", "origin/master")
# Where the stop hook's delta starts: env overrides first (ARCH_BASE_ENV, then
# GITHUB_BASE_REF), then these. Never fetched — a hook must not touch the network.
DELTA_BASE_CANDIDATES = ("origin/HEAD", "origin/main", "origin/master", "main", "master")
HOOK_STDIN_TIMEOUT = 1.0
# Finding lines a stop-hook payload carries; the rest are one command away.
HOOK_FINDING_LIMIT = 20

# ── Hook wiring (installed by `setup-hooks`) ──────────────────────
# Claude reads .claude/settings.json and runs the harness directly; Codex reads
# .codex/hooks.json and goes through the codex-stop-hook.sh wrapper (which turns
# the exit code into the block/continue JSON Codex expects). Keep both forms in
# sync with the committed template files so re-running the installer is a no-op.
# PostToolUse is Claude-only: it formats the file an Edit/Write just touched.
CLAUDE_SETTINGS = ".claude/settings.json"
CLAUDE_SETTINGS_SCHEMA = "https://json.schemastore.org/claude-code-settings.json"
CLAUDE_STOP_COMMAND = "cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook"
CLAUDE_POST_EDIT_COMMAND = "cd $CLAUDE_PROJECT_DIR && uv run harness post-edit --hook"
CODEX_STOP_COMMAND = (
    'cd "$(git rev-parse --show-toplevel)" && '
    ".codex/hooks/codex-stop-hook.sh uv run harness stop-hook"
)
CLAUDE_STOP_HOOK: dict[str, Any] = {
    "type": "command",
    "command": CLAUDE_STOP_COMMAND,
    "timeout": 300,
}
CLAUDE_POST_EDIT_HOOK: dict[str, Any] = {
    "type": "command",
    "command": CLAUDE_POST_EDIT_COMMAND,
    "timeout": 60,
}
CODEX_STOP_HOOK: dict[str, Any] = {
    "type": "command",
    "command": CODEX_STOP_COMMAND,
    "timeout": 300,
    "statusMessage": "Running stop-hook checks",
}


@dataclasses.dataclass(frozen=True)
class HookWiring:
    """One harness hook in an agent settings file; `marker` identifies it on reinstall."""

    path: str
    event: str
    marker: str
    handler: dict[str, Any]
    matcher: str | None = None


HOOK_WIRINGS = (
    HookWiring(CLAUDE_SETTINGS, "Stop", "stop-hook", CLAUDE_STOP_HOOK),
    HookWiring(
        CLAUDE_SETTINGS, "PostToolUse", "post-edit --hook", CLAUDE_POST_EDIT_HOOK, "Edit|Write"
    ),
    HookWiring(".codex/hooks.json", "Stop", "stop-hook", CODEX_STOP_HOOK),
)

# ── Output ────────────────────────────────────────────────────────

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
VERBOSE = "--verbose" in sys.argv


@dataclasses.dataclass(frozen=True)
class GateResult:
    """The captured outcome of one gate command; safe to build off the main thread."""

    description: str
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    hint: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclasses.dataclass(frozen=True)
class Gate:
    """A read-only gate's label and command, shared by standalone cmd_* and the batch."""

    description: str
    cmd: list[str]
    hint: str | None = None


def run_capture(
    description: str,
    cmd: list[str],
    hint: str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> GateResult:
    """Run a command with output captured; the thread-safe unit for the parallel batch."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    return GateResult(description, cmd, result.returncode, result.stdout, result.stderr, hint)


def print_gate_result(result: GateResult, *, no_exit: bool = False) -> None:
    """Print a gate's pass/fail line (with the failure body); exit on failure unless no_exit."""
    if result.ok:
        extra = _parse_unittest_summary(result.stderr) if "unittest" in result.cmd else ""
        print(f"  {GREEN}✓{RESET} {result.description}{extra}")
        return

    print(f"  {RED}✗{RESET} {result.description}")
    print(f"{RED}Command failed: {' '.join(result.cmd)}{RESET}")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.hint:
        print(f"  ↳ fix: {result.hint}")
    if not no_exit:
        sys.exit(result.returncode)


def run(
    description: str,
    cmd: list[str],
    *,
    no_exit: bool = False,
    stream: bool = False,
    hint: str | None = None,
) -> None:
    """Run command silently; show output only on failure.

    Pass stream=True only when live output is part of the command contract.
    """
    if VERBOSE or stream:
        print(f"  -> {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0 and not no_exit:
            sys.exit(result.returncode)
        return

    print_gate_result(run_capture(description, cmd, hint), no_exit=no_exit)


def run_gates_parallel(gates: list[Gate]) -> bool:
    """Run read-only gates concurrently, then print each result in submission order.

    Returns True when every gate passed. Unlike the fail-fast standalone gates, this
    runs all gates to completion so one pass surfaces every failure; the caller exits
    non-zero afterward. Output is captured and printed in submission order (not as
    they finish) so a parallel run reads the same every time — matching the monorepo
    Makefile's buffered, deterministic dump. VERBOSE falls back to a sequential run
    so the live `-> cmd` echoes stay ordered.
    """
    if not gates:
        return True

    if VERBOSE:
        all_ok = True
        for gate in gates:
            print(f"  -> {' '.join(gate.cmd)}")
            result = subprocess.run(gate.cmd, check=False)
            all_ok = all_ok and result.returncode == 0
        return all_ok

    max_workers = min(len(gates), os.cpu_count() or 4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(
            executor.map(lambda gate: run_capture(gate.description, gate.cmd, gate.hint), gates)
        )

    all_ok = True
    for result in results:
        print_gate_result(result, no_exit=True)
        all_ok = all_ok and result.ok
    return all_ok


def _exit_if_failed(all_ok: bool) -> None:
    if not all_ok:
        sys.exit(1)


def _parse_unittest_summary(output: str) -> str:
    """Extract '(N tests, X.Xs)' from unittest output."""
    m = re.search(r"Ran (\d+) tests? in ([\d.]+s)", output)
    return f" ({m.group(1)} tests, {m.group(2)})" if m else ""


def warn(message: str) -> None:
    """Print a non-blocking warning line."""
    print(f"  {GREEN}⚠{RESET} {message}")


def _existing(paths: Iterable[str]) -> list[str]:
    """Return paths that exist in this project."""
    return [path for path in paths if Path(path).exists()]


def _quality_targets(*, include_tests: bool = True) -> list[str]:
    """Return quality-check targets that exist."""
    targets = _existing(QUALITY_SOURCES)
    if include_tests and Path(TEST_DIR).is_dir():
        targets.append(TEST_DIR)
    return targets


def _app_targets(*, include_tests: bool = False) -> list[str]:
    """Return app targets that exist."""
    targets = _existing(APP_SOURCES)
    if include_tests and Path(TEST_DIR).is_dir():
        targets.append(TEST_DIR)
    return targets


def _iter_python_files(paths: Iterable[str]) -> list[Path]:
    """Return Python files under existing file or directory targets."""
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
    return files


def _has_tests() -> bool:
    """Return true when unittest-discoverable tests exist."""
    test_root = Path(TEST_DIR)
    return test_root.is_dir() and any(test_root.rglob("test*.py"))


def _matches_python_target(path: str, targets: Iterable[str]) -> bool:
    """Return true if path is a Python file inside one of the target paths."""
    if not path.endswith(".py"):
        return False
    for target in targets:
        if target.endswith(".py") and path == target:
            return True
        if not target.endswith(".py") and path.startswith(f"{target}/"):
            return True
    return False


def _is_project_python_file(path: str) -> bool:
    """Return true for Python files owned by the template project."""
    return _matches_python_target(path, (*QUALITY_SOURCES, TEST_DIR))


def _porcelain_path(line: str) -> str:
    """Extract the current path from a git porcelain status line."""
    path = line[3:]
    if " -> " in path:
        return path.rsplit(" -> ", 1)[1]
    return path


# ── Suppressions ──────────────────────────────────────────────────

_SUPPRESSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("noqa", re.compile(r"#\s*noqa(?::\s*([A-Z][A-Z0-9]+(?:\s*,\s*[A-Z][A-Z0-9]+)*))?")),
    (
        "type_ignore",
        re.compile(r"#\s*type:\s*ignore(?:\[([a-zA-Z0-9_-]+(?:\s*,\s*[a-zA-Z0-9_-]+)*)\])?"),
    ),
    (
        "pyright_ignore",
        re.compile(r"#\s*pyright:\s*ignore(?:\[([a-zA-Z0-9_-]+(?:\s*,\s*[a-zA-Z0-9_-]+)*)\])?"),
    ),
]


@dataclasses.dataclass(frozen=True)
class SuppressionFinding:
    kind: str
    rules: list[str]
    location: str


def _parse_line_for_suppressions(line: str) -> list[tuple[str, list[str]]]:
    """Return all (kind, rules) matches found on a single line."""
    matches: list[tuple[str, list[str]]] = []
    for kind, pat in _SUPPRESSION_PATTERNS:
        m = pat.search(line)
        if m:
            rules = [r.strip() for r in m.group(1).split(",") if r.strip()] if m.group(1) else []
            matches.append((kind, rules))
    return matches


def _scan_suppression_findings(roots: Iterable[str] | None = None) -> list[SuppressionFinding]:
    """Scan Python files for suppression comments with file:line locations."""
    findings: list[SuppressionFinding] = []
    actual_roots = roots if roots is not None else _quality_targets()
    for py_file in _iter_python_files(actual_roots):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for kind, rules in _parse_line_for_suppressions(line):
                findings.append(SuppressionFinding(kind, rules, f"{py_file}:{line_no}"))
    return findings


def _scan_suppressions(roots: Iterable[str] | None = None) -> dict[str, list[list[str]]]:
    """Scan Python files for suppression comments. Returns {kind: [rules...]}."""
    results: dict[str, list[list[str]]] = {}
    for finding in _scan_suppression_findings(roots):
        results.setdefault(finding.kind, []).append(finding.rules)
    return results


def _suppression_counts(results: dict[str, list[list[str]]]) -> dict[str, int]:
    return {
        f"{SUPPRESSION_BASELINE_PREFIX}{kind}": len(entries) for kind, entries in results.items()
    }


def _read_baseline() -> dict[str, int] | None:
    path = Path(BASELINE_FILE)
    if not path.exists():
        return None
    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) != 2:
            continue
        key, value = parts
        try:
            values[key] = int(value)
        except ValueError:
            continue
    return values


def _coverage_min_default() -> int:
    explicit = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--min=")), None)
    if explicit is not None:
        return int(explicit)
    baseline = _read_baseline()
    return 0 if baseline is None else baseline.get("coverage.min", 0)


def _write_baseline(results: dict[str, list[list[str]]]) -> None:
    baseline = _read_baseline() or {}
    counts = _suppression_counts(results)
    coverage_min = baseline.get("coverage.min", 0)
    lines = [f"{key} {counts[key]}" for key in sorted(counts)]
    lines.append(f"coverage.min {coverage_min}")
    Path(BASELINE_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_suppressions_breakdown(results: dict[str, list[list[str]]]) -> None:
    total = sum(len(v) for v in results.values())
    print("\n=== Suppressions ===\n")
    print(f"Suppressions: {total} total")
    if total == 0:
        return
    for kind in sorted(results):
        entries = results[kind]
        print(f"  {kind}: {len(entries)}")
        rule_counts: dict[str, int] = {}
        for rules in entries:
            for r in rules:
                rule_counts[r] = rule_counts.get(r, 0) + 1
        for rule, count in sorted(rule_counts.items(), key=lambda x: (-x[1], x[0]))[:10]:
            print(f"    {rule}: {count}")


def _check_suppressions_baseline(*, no_exit: bool = False) -> bool:
    """Compare current suppression counts to the committed baseline."""
    findings = _scan_suppression_findings()
    results: dict[str, list[list[str]]] = {}
    locations: dict[str, list[str]] = {}
    for finding in findings:
        results.setdefault(finding.kind, []).append(finding.rules)
        locations.setdefault(finding.kind, []).append(finding.location)

    current = _suppression_counts(results)
    baseline = _read_baseline()
    if baseline is None:
        _print_suppressions_breakdown(results)
        print(f"  {GREEN}⚠{RESET} Suppressions are report-only: no {BASELINE_FILE} found")
        print("  ↳ fix: run `harness suppressions --update-baseline` to start ratcheting")
        return True

    total = sum(current.values())
    baseline_total = sum(
        count for key, count in baseline.items() if key.startswith(SUPPRESSION_BASELINE_PREFIX)
    )
    grown = {key: count for key, count in current.items() if count > baseline.get(key, 0)}
    if not grown:
        suffix = ""
        if total < baseline_total:
            suffix = " — run `harness suppressions --update-baseline` to ratchet down"
        print(f"  {GREEN}✓{RESET} Suppressions: {total} (baseline {baseline_total}){suffix}")
        return True

    print(f"  {RED}✗{RESET} Suppressions grew: {total} (baseline {baseline_total})")
    for key in sorted(grown):
        kind = key.removeprefix(SUPPRESSION_BASELINE_PREFIX)
        print(f"    {kind}: {grown[key]} > {baseline.get(key, 0)}")
        for location in locations.get(kind, [])[:10]:
            print(f"      {location}")
    print("  ↳ fix: fix it, or with human sign-off: `harness suppressions --update-baseline`")
    if not no_exit:
        sys.exit(1)
    return False


def cmd_suppressions() -> None:
    """Print suppression details, or update the committed suppression baseline."""
    results = _scan_suppressions()
    if "--update-baseline" in sys.argv:
        _write_baseline(results)
        total = sum(len(v) for v in results.values())
        print(f"  {GREEN}✓{RESET} {BASELINE_FILE}: suppressions baseline set to {total}")
        return
    _print_suppressions_breakdown(results)
    _check_suppressions_baseline()


# ── Git helpers ───────────────────────────────────────────────────


def _staged_py_files() -> list[str]:
    """Return staged project .py files, excluding deleted files."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=d", "--relative"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [path for path in result.stdout.splitlines() if _is_project_python_file(path)]


def _changed_py_files() -> list[str]:
    """Return project .py files with uncommitted changes, relative to this project.

    Porcelain paths are repository-relative, so a project in a subdirectory strips
    its prefix; `--untracked-files=all` lists new files inside new directories.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "."],
        capture_output=True,
        text=True,
        check=False,
    )
    prefix = _git_prefix()
    changed: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) <= 3 or "D" in line[:2]:
            continue
        path = _normalize_changed_path(_porcelain_path(line), prefix)
        if _is_project_python_file(path):
            changed.append(path)
    return changed


# ── Commands ──────────────────────────────────────────────────────


def cmd_fix(files: list[str] | None = None) -> None:
    target = files or ["."]
    run("Fix lint errors", ["uv", "run", "ruff", "check", "--fix", *target])


def cmd_format(files: list[str] | None = None) -> None:
    target = files or ["."]
    run("Format code", ["uv", "run", "ruff", "format", *target])


def _lint_gate(files: list[str] | None = None) -> Gate:
    target = files or ["."]
    return Gate("Lint check", ["uv", "run", "ruff", "check", *target], "run `harness fix`")


def cmd_lint(files: list[str] | None = None) -> None:
    gate = _lint_gate(files)
    run(gate.description, gate.cmd)


def _format_check_gate() -> Gate:
    return Gate(
        "Format check", ["uv", "run", "ruff", "format", "--check", "."], "run `harness format`"
    )


def _typecheck_gate() -> Gate:
    return Gate(
        "Type check",
        ["uv", "run", "basedpyright", *_quality_targets()],
        "fix the type; ignores are counted by the suppression ratchet",
    )


def cmd_typecheck() -> None:
    gate = _typecheck_gate()
    run(gate.description, gate.cmd)


def _test_gate() -> Gate | None:
    """The unittest suite, or a syntax check when no tests exist; None with nothing to check."""
    if _has_tests():
        return Gate(
            "Tests", ["uv", "run", "python", "-m", "unittest", "discover", "-s", TEST_DIR, "-q"]
        )
    files = [str(path) for path in _iter_python_files(_quality_targets(include_tests=False))]
    if not files:
        return None
    return Gate("Syntax check", ["uv", "run", "python", "-m", "py_compile", *files])


def cmd_test() -> None:
    gate = _test_gate()
    if gate is None:
        warn("Syntax check: no Python files found; skipped")
        return
    run(gate.description, gate.cmd)


def _env_without_git() -> dict[str, str]:
    """This environment minus the GIT_* variables git exports to hooks."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _check_tests() -> bool:
    """Run the test gate captured, outside git's hook environment.

    git exports GIT_DIR (and, for commits, GIT_INDEX_FILE) to hooks; a test that runs
    `git init` in a temp dir would otherwise write into this repository.
    """
    gate = _test_gate()
    if gate is None:
        warn("Syntax check: no Python files found; skipped")
        return True
    result = run_capture(gate.description, gate.cmd, gate.hint, env=_env_without_git())
    print_gate_result(result, no_exit=True)
    return result.ok


def cmd_coverage() -> None:
    """Run tests under coverage with threshold + uncovered listing."""
    if not _has_tests():
        warn(f"Coverage: no {TEST_DIR}/test*.py files; skipped")
        return

    min_pct = _coverage_min_default()
    run(
        "Coverage (run)",
        ["uv", "run", "coverage", "run", "-m", "unittest", "discover", "-s", TEST_DIR, "-q"],
    )
    run(
        f"Coverage >= {min_pct}%",
        ["uv", "run", "coverage", "report", "--show-missing", f"--fail-under={min_pct}"],
    )


def _acceptance_gates_or_warn() -> list[Gate]:
    """Build the acceptance gate, or warn + return [] when there are no scenarios."""
    features_dir = Path(TEST_DIR) / "features"
    if not features_dir.exists() or not list(features_dir.rglob("*.feature")):
        warn(f"Acceptance: no .feature files in {features_dir}/ (add one to enable this gate)")
        return []
    return [
        Gate(
            "Acceptance (behave)",
            ["uv", "run", "behave", str(features_dir), "--no-color"],
            "align implementation with the `.feature`, not vice versa",
        )
    ]


def cmd_acceptance() -> None:
    """Run behave scenarios. Empty features dir warns + exits 0."""
    for gate in _acceptance_gates_or_warn():
        run(gate.description, gate.cmd)


def cmd_mutation() -> None:
    """Run mutmut. Advisory — not wired into ci.

    mutmut 3.x takes no --paths-to-mutate flag; it defaults to `src/` and reads
    `[tool.mutmut]` in pyproject.toml for customization.
    """
    if not _has_tests():
        warn(f"Mutation: no {TEST_DIR}/test*.py files; skipped")
        return

    run("Mutation (mutmut)", ["uv", "run", "mutmut", "run"], no_exit=True)
    run("Mutation results", ["uv", "run", "mutmut", "results"], no_exit=True)


def _arch_gates_or_warn() -> list[Gate]:
    """Build the import-linter gate, or warn + return [] when no .importlinter exists."""
    if not Path(".importlinter").exists():
        warn("Arch: no .importlinter — skipped")
        return []
    return [Gate("Arch (import-linter)", ["uv", "run", "lint-imports"])]


def cmd_arch() -> None:
    """Run import-linter against .importlinter."""
    for gate in _arch_gates_or_warn():
        run(gate.description, gate.cmd)


def _git_lines(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _git_prefix() -> str:
    lines = _git_lines(["rev-parse", "--show-prefix"])
    if not lines:
        return ""
    return lines[0].removeprefix("./").replace("\\", "/").strip("/")


def _normalize_changed_path(path: str, prefix: str) -> str:
    normalized = path.strip().removeprefix("./").replace("\\", "/")
    if prefix and normalized.startswith(f"{prefix}/"):
        return normalized[len(prefix) + 1 :]
    return normalized


def _changed_paths_from_base() -> list[str]:
    bases: list[str] = []
    if base := os.environ.get("HARNESS_ARCH_BASE"):
        bases.append(base)
    if github_base := os.environ.get("GITHUB_BASE_REF"):
        bases.append(f"origin/{github_base}")

    paths: list[str] = []
    for base in bases:
        if not _git_lines(["rev-parse", "--verify", base]):
            continue
        paths.extend(_git_lines(["diff", "--name-only", f"{base}...HEAD", "--", "."]))
    return paths


@dataclasses.dataclass(frozen=True)
class PrePushRefs:
    """What a push is about to do: `<local ref> <local sha> <remote ref> <remote sha>` lines.

    `incomplete` means data started arriving on stdin but never finished — the guards
    fail rather than guess from the current branch.
    """

    lines: tuple[str, ...] = ()
    incomplete: bool = False


def _parse_pre_push_refs(text: str) -> PrePushRefs:
    return PrePushRefs(tuple(line for line in text.splitlines() if len(line.split()) >= 4))


def _read_stdin(timeout: float) -> tuple[bytes, bool]:
    """Read stdin to EOF within `timeout` seconds; returns (bytes, whether EOF arrived).

    The deadline bounds the whole read, not just the first byte, so an idle pipe (an
    agent tool that never closes stdin) cannot hang the caller.
    """
    deadline = time.monotonic() + timeout
    fd = sys.stdin.fileno()
    received = b""
    while True:
        remaining = deadline - time.monotonic()
        ready = select.select([fd], [], [], remaining)[0] if remaining > 0 else []
        if not ready:
            return received, False
        chunk = os.read(fd, 65536)
        if not chunk:
            return received, True
        received += chunk


def _read_pre_push_stdin() -> PrePushRefs:
    """Read the hook's ref lines under PRE_PUSH_STDIN_TIMEOUT.

    A deadline hit with nothing received is taken as "not a hook invocation" (an idle
    pipe under an agent tool) and falls back to the current branch: git writes its ref
    lines immediately, and dispatchers that already drained stdin pass PRE_PUSH_REFS_ENV.
    A hook whose writer is slower than the deadline therefore degrades to that check.
    """
    received, complete = _read_stdin(PRE_PUSH_STDIN_TIMEOUT)
    if not complete:
        # Nothing yet means an idle pipe (no refs); a stalled partial read is fatal.
        return PrePushRefs(incomplete=bool(received.strip()))
    return _parse_pre_push_refs(received.decode("utf-8", errors="replace"))


@functools.cache
def _pre_push_refs() -> PrePushRefs:
    """Resolve the push destinations once per process; both push guards share the result.

    PRE_PUSH_REFS_ENV wins — dispatchers export it for child harnesses, whose own stdin
    is already exhausted. Otherwise the git hook's stdin: absent on a manual run (tty),
    often an idle pipe under an agent tool.
    """
    env_refs = os.environ.get(PRE_PUSH_REFS_ENV, "")
    if env_refs.strip():
        return _parse_pre_push_refs(env_refs)
    if sys.stdin.isatty():
        return PrePushRefs()
    return _read_pre_push_stdin()


def _report_incomplete_refs() -> bool:
    print(f"  {RED}✗{RESET} Pre-push refs incomplete after {PRE_PUSH_STDIN_TIMEOUT:g}s")
    print(f"  ↳ fix: rerun the push, or pass the refs via {PRE_PUSH_REFS_ENV}")
    return False


def _new_branch_paths(local_sha: str) -> list[str]:
    """Paths a branch git has never seen changes: the whole branch, not just its tip."""
    for base in (*ARCH_BASE_CANDIDATES, os.environ.get(ARCH_BASE_ENV, "")):
        if not base or not _git_lines(["rev-parse", "--verify", base]):
            continue
        merge_base = _git_lines(["merge-base", base, local_sha])
        if not merge_base:
            continue
        return _git_lines([
            "diff",
            "--name-only",
            f"{merge_base[0]}..{local_sha}",
            "--",
            ".",
        ])
    return _git_lines(["diff-tree", "--no-commit-id", "--name-only", "-r", local_sha, "--", "."])


def _changed_paths_from_pre_push_refs() -> list[str]:
    zero = "0" * 40
    paths: list[str] = []
    for line in _pre_push_refs().lines:
        _, local_sha, _, remote_sha = line.split()[:4]
        if local_sha == zero:
            continue
        if remote_sha == zero:
            paths.extend(_new_branch_paths(local_sha))
        else:
            paths.extend(_git_lines(["diff", "--name-only", remote_sha, local_sha, "--", "."]))
    return paths


def _changed_arch_configs(
    *, staged: bool = False, include_pre_push_refs: bool = False
) -> list[str]:
    paths: list[str] = []
    if staged:
        paths.extend(_git_lines(["diff", "--cached", "--name-only", "--", "."]))
    else:
        paths.extend(_git_lines(["diff", "--name-only", "--", "."]))
        paths.extend(_git_lines(["diff", "--cached", "--name-only", "--", "."]))
        paths.extend(_git_lines(["ls-files", "--others", "--exclude-standard", "--", "."]))
        paths.extend(_changed_paths_from_base())
    if include_pre_push_refs:
        paths.extend(_changed_paths_from_pre_push_refs())

    protected = set(ARCH_CONFIGS)
    prefix = _git_prefix()
    changed: set[str] = set()
    for path in paths:
        normalized = _normalize_changed_path(path, prefix)
        if normalized in protected:
            changed.add(normalized)
    return sorted(changed)


def _check_arch_config_guard(
    *,
    warn_only: bool = False,
    staged: bool = False,
    include_pre_push_refs: bool = False,
) -> bool:
    if include_pre_push_refs and _pre_push_refs().incomplete:
        return _report_incomplete_refs()
    changed = _changed_arch_configs(staged=staged, include_pre_push_refs=include_pre_push_refs)
    if not changed:
        print(f"  {GREEN}✓{RESET} Arch config guard")
        return True
    joined = ", ".join(changed)
    if os.environ.get(ARCH_CONFIG_ALLOW_ENV) == "1":
        print(f"  {GREEN}⚠{RESET} Arch config guard override: {joined}")
        return True
    if warn_only:
        print(f"  {GREEN}⚠{RESET} Arch config changed: {joined}")
        print(
            f"  ↳ fix: review intentionally, then use {ARCH_CONFIG_ALLOW_ENV}=1 for commit/push/CI"
        )
        return True
    print(f"  {RED}✗{RESET} Arch config changed: {joined}")
    print(f"  ↳ fix: review intentionally, then rerun with {ARCH_CONFIG_ALLOW_ENV}=1")
    return False


def cmd_arch_config_guard() -> None:
    ok = _check_arch_config_guard(warn_only="--warn" in sys.argv, staged="--staged" in sys.argv)
    if not ok:
        sys.exit(1)


def _protected_push_branch(ref_lines: Sequence[str], current_branch: str) -> str | None:
    """Protected branch this push writes to, or None.

    Ref lines win when present — including deletions (zero local sha), which are the
    most destructive write of all. Only `refs/heads/*` remote refs count, so tags never
    match. With no ref lines the current branch decides.
    """
    if not ref_lines:
        return current_branch if current_branch in PROTECTED_BRANCHES else None
    for line in ref_lines:
        remote_ref = line.split()[2]
        if not remote_ref.startswith("refs/heads/"):
            continue
        branch = remote_ref.removeprefix("refs/heads/")
        if branch in PROTECTED_BRANCHES:
            return branch
    return None


def _current_branch() -> str:
    lines = _git_lines(["rev-parse", "--abbrev-ref", "HEAD"])
    return lines[0] if lines else ""


def _check_branch_guard() -> bool:
    refs = _pre_push_refs()
    if refs.incomplete:
        return _report_incomplete_refs()
    target = _protected_push_branch(refs.lines, _current_branch())
    if target is None:
        print(f"  {GREEN}✓{RESET} Branch guard")
        return True
    if os.environ.get(PROTECTED_PUSH_ALLOW_ENV) == "1":
        print(f"  {GREEN}⚠{RESET} Branch guard override: {target}")
        return True
    print(f"  {RED}✗{RESET} Push targets protected branch: {target}")
    print(
        "  ↳ fix: push a feature branch and open a PR; "
        f"humans may set {PROTECTED_PUSH_ALLOW_ENV}=1"
    )
    return False


def cmd_branch_guard() -> None:
    if not _check_branch_guard():
        sys.exit(1)


def _crap_score(ccn: int, cov: float) -> float:
    """CRAP = ccn^2 * (1-cov)^3 + ccn."""
    return ccn * ccn * (1 - cov) ** 3 + ccn


def _parse_coverage_xml(path: Path) -> dict[str, dict[int, int]]:
    """Parse a Cobertura coverage XML into {filename: {line_no: hits}}."""
    cov_map: dict[str, dict[int, int]] = {}
    for cls in ET.parse(path).iter("class"):
        fn = cls.get("filename", "")
        cov_map[fn] = {
            int(ln.get("number", "0")): int(ln.get("hits", "0"))
            for ln in cls.iter("line")
            if ln.get("number")
        }
    return cov_map


def _artifact_is_fresh(path: Path, roots: Iterable[str]) -> bool:
    """Return true when artifact is newer than every Python file under roots."""
    try:
        artifact_mtime = path.stat().st_mtime
    except OSError:
        return False

    try:
        return all(p.stat().st_mtime <= artifact_mtime for p in _iter_python_files(roots))
    except OSError:
        return False


def cmd_crap() -> None:
    """CRAP = ccn^2 * (1-cov)^3 + ccn per function. Advisory — lizard + coverage XML."""
    if not _has_tests():
        warn("CRAP: no tests; skipped")
        return

    max_crap = float(
        next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--max=")), "30")
    )
    enforce = "--enforce" in sys.argv

    cov_data = Path(".coverage")
    if not _artifact_is_fresh(cov_data, _quality_targets()):
        cmd_coverage()

    # Emit coverage XML quietly; cmd_coverage must have populated .coverage.
    subprocess.run(
        ["uv", "run", "coverage", "xml", "-o", "coverage.xml", "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    cov_file = Path("coverage.xml")
    if not cov_file.exists():
        warn("CRAP: coverage XML not found after coverage run")
        return

    cov_map = _parse_coverage_xml(cov_file)
    targets = _app_targets()
    if not targets:
        warn("CRAP: no app sources; skipped")
        return

    lizard_res = subprocess.run(
        ["uvx", LIZARD, *targets],
        capture_output=True,
        text=True,
        check=False,
    )
    if lizard_res.returncode != 0:
        # Lizard could not run (uvx missing, network failure, lizard crash).
        # Reporting "all functions below max" would be a silent false-pass.
        suffix = "" if enforce else " (advisory)"
        print(f"  {RED}✗{RESET} CRAP: lizard failed to run (exit {lizard_res.returncode}){suffix}")
        if lizard_res.stderr.strip():
            print(lizard_res.stderr.strip())
        if enforce:
            sys.exit(lizard_res.returncode or 1)
        return
    # Function name capture allows the empty string so we can detect (and skip)
    # anonymous functions explicitly rather than silently dropping them.
    line_re = re.compile(r"^\s*(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+([^@\s]*)@(\d+)-(\d+)@(.+)$")
    offenders: list[tuple[float, int, float, str]] = []
    for out_line in lizard_res.stdout.splitlines():
        m = line_re.match(out_line)
        if not m:
            continue
        _, ccn_s, func, start_s, end_s, path = m.groups()
        # Anonymous functions: lizard emits an empty name. Coverage in cobertura
        # is attributed to the enclosing scope, so a per-function join would
        # mis-score — skip rather than silently misattribute.
        if not func:
            continue
        ccn = int(ccn_s)
        start, end = int(start_s), int(end_s)
        lines = cov_map.get(path) or cov_map.get(path.lstrip("./")) or {}
        in_range = [n for n in range(start, end + 1) if n in lines]
        cov = (sum(1 for n in in_range if lines[n] > 0) / len(in_range)) if in_range else 0.0
        crap = _crap_score(ccn, cov)
        if crap > max_crap:
            offenders.append((crap, ccn, cov, f"{func}@{start}-{end}@{path}"))

    if not offenders:
        print(f"  {GREEN}✓{RESET} CRAP: all functions below {max_crap}")
        return
    offenders.sort(reverse=True)
    mode_suffix = "" if enforce else " (advisory)"
    print(f"  {RED}✗{RESET} CRAP: {len(offenders)} function(s) exceed {max_crap}{mode_suffix}")
    for crap, ccn, cov, loc in offenders[:20]:
        print(f"    CRAP={crap:6.1f}  CCN={ccn:3d}  cov={cov * 100:5.1f}%  {loc}")
    if enforce:
        sys.exit(1)


def _audit_gate() -> Gate:
    return Gate(
        "Dep audit",
        ["uv", "run", "--with", "pip-audit", "pip-audit"],
        "bump the vulnerable dependency or escalate",
    )


def cmd_audit() -> None:
    gate = _audit_gate()
    run(gate.description, gate.cmd)


def _complexity_gate() -> Gate:
    return Gate(
        "Complexity (lizard)",
        [
            "uvx",
            LIZARD,
            *_app_targets(include_tests=True),
            "-C",
            str(COMPLEXITY_MAX_CCN),
            "-a",
            str(COMPLEXITY_MAX_ARGS),
            "-L",
            str(COMPLEXITY_MAX_LENGTH),
            "-i",
            "0",
        ],
        f"extract helpers or flatten branches until CCN <= {COMPLEXITY_MAX_CCN}; "
        "do not raise the threshold",
    )


def cmd_complexity() -> None:
    gate = _complexity_gate()
    run(gate.description, gate.cmd)


def _deadcode_gate() -> Gate:
    """Build the vulture dead-code gate.

    Scans the app sources only — never `tests/` — so code referenced solely by a
    test (a dead helper that still has a test) is reported, not masked. Confidence
    60 is vulture's floor for unused functions/methods/classes. List legitimate
    dynamic references (decorator-registered handlers, getattr dispatch) in
    `vulture_allowlist.py` to silence false positives.
    """
    return Gate(
        "Dead code (vulture)",
        [
            "uvx",
            VULTURE,
            *_app_targets(),
            *_existing([VULTURE_ALLOWLIST]),
            "--min-confidence",
            VULTURE_MIN_CONFIDENCE,
        ],
        f"delete unused code, or allowlist genuine dynamic refs in {VULTURE_ALLOWLIST}",
    )


def cmd_deadcode() -> None:
    gate = _deadcode_gate()
    run(gate.description, gate.cmd)


# ── Agent hooks ───────────────────────────────────────────────────
# The stop hook runs after every agent turn and judges the change, not the tree:
# lint left on changed lines, functions pushed over (or further over) a complexity
# limit, dead code on changed lines. Pre-existing debt never blocks a stop; the
# whole-tree gates stay in check / ci / pre-push. Exit contract: silent 0 when
# clean, 2 with a stderr payload the agent reads, 1 when a tool could not run.

LineRanges = list[tuple[int, int]]  # inclusive (start, end) line spans

WHOLE_FILE: LineRanges = [(1, sys.maxsize)]
STOP_HOOK_RERUN = "uv run harness stop-hook --verbose"
LOOP_GUARD_NOTICE = "harness: same findings as the previous stop; not blocking again"
POST_EDIT_NOTICE = "harness: reformatted {}; re-read it before editing it again"
COMPLEXITY_LIMITS = (
    ("CCN", "ccn", COMPLEXITY_MAX_CCN),
    ("args", "args", COMPLEXITY_MAX_ARGS),
    ("length", "length", COMPLEXITY_MAX_LENGTH),
)
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_VULTURE_LINE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): ")


class ToolError(Exception):
    """A gate's tool could not run, or printed output the gate cannot read."""


@dataclasses.dataclass(frozen=True)
class DeltaResult:
    """One delta gate: findings block the stop; a problem means its tool failed."""

    gate: str
    findings: list[str]
    problem: str = ""


@dataclasses.dataclass(frozen=True)
class FunctionMetrics:
    """One function as `lizard --csv` measured it."""

    name: str
    line: int
    ccn: int
    args: int
    length: int


def _run_tool(
    tool: str, cmd: list[str], *, ok: Sequence[int] = (0,), cwd: str | None = None
) -> str:
    """The command's stdout; ToolError when it cannot start or exits outside `ok`."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=cwd)
    except OSError as exc:
        raise ToolError(f"{tool} not runnable: {exc.strerror or exc}") from exc
    if result.returncode in ok:
        return result.stdout
    detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
    reason = f"{tool} exited {result.returncode}"
    raise ToolError(f"{reason}: {detail[-1].strip()}" if detail else reason)


def _git_output(args: list[str]) -> str:
    return _run_tool("git", ["git", "-c", "core.quotePath=false", *args])


# ── Changed lines ──


def _base_ref() -> str | None:
    """The first base ref that resolves: env overrides, then DELTA_BASE_CANDIDATES."""
    candidates = [os.environ.get(ARCH_BASE_ENV, "")]
    if github_base := os.environ.get("GITHUB_BASE_REF"):
        candidates.append(f"origin/{github_base}")
    candidates.extend(DELTA_BASE_CANDIDATES)
    for ref in candidates:
        if ref and _git_lines(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]):
            return ref
    return None


def _delta_base() -> str | None:
    """merge-base(base ref, HEAD); HEAD without a base ref; None before the first commit."""
    if not _git_lines(["rev-parse", "--verify", "--quiet", "HEAD"]):
        return None
    ref = _base_ref()
    merge_base = _git_lines(["merge-base", ref, "HEAD"]) if ref else []
    return merge_base[0] if merge_base else "HEAD"


def _diff_path(header: str) -> str | None:
    """The new-side path of a `+++ b/<path>` header; None for a deleted file."""
    name = header.removeprefix("+++ ").rstrip("\t")
    if name == "/dev/null":
        return None
    if len(name) > 1 and name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return name.removeprefix("b/")


def _parse_diff_ranges(diff: str) -> dict[str, LineRanges]:
    """`{path: [(start, end)]}` of the new-side lines in a `git diff -U0` (a/ b/ prefixes).

    File headers are read only between `diff --git` and the first hunk, so an added line
    whose text starts with `++ ` is never taken for one. A pure deletion (`+N,0`) adds no
    range, but its file is still listed.
    """
    ranges: dict[str, LineRanges] = {}
    path: str | None = None
    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, in_header = None, True
        elif in_header and line.startswith("+++ "):
            path = _diff_path(line)
            if path is not None:
                ranges[path] = []
        elif line.startswith("@@"):
            in_header = False
            match = _HUNK_RE.match(line)
            count = int(match[2] or 1) if match else 0
            if match and path is not None and count:
                start = int(match[1])
                ranges[path].append((start, start + count - 1))
    return ranges


def _changed_scope(base: str | None) -> dict[str, LineRanges]:
    """Changed lines per path relative to this project: `git diff <base>` plus untracked.

    Covers work committed on the branch and uncommitted work alike. Untracked files, and
    every file before the first commit, are in scope whole. Renames count as new files.
    """
    listing = ["ls-files", "--others", "--exclude-standard", "--", "."]
    scope: dict[str, LineRanges] = {}
    if base is None:
        listing.insert(1, "--cached")
    else:
        diff = _git_output([
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
        ])
        scope = _parse_diff_ranges(diff)
    for path in _git_output(listing).splitlines():
        scope[path] = list(WHOLE_FILE)
    return scope


def _in_ranges(line: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in ranges)


def _scoped_files(scope: dict[str, LineRanges], targets: Iterable[str]) -> list[str]:
    """Changed Python files under `targets` that still exist."""
    return sorted(
        path for path in scope if _matches_python_target(path, targets) and Path(path).is_file()
    )


# ── Lint residue ──


def _ruff_findings(report: str, scope: dict[str, LineRanges], root: Path) -> list[str]:
    """`path:line: CODE message` for each ruff JSON diagnostic on a changed line."""
    findings: list[str] = []
    try:
        for item in json.loads(report):
            filename = Path(item["filename"]).resolve()
            path = filename.relative_to(root.resolve(), walk_up=True).as_posix()
            row = int(item["location"]["row"])
            if _in_ranges(row, scope.get(path, [])):
                code = f"{item['code']} " if item.get("code") else ""
                findings.append(f"{path}:{row}: {code}{item['message']}")
    except (ValueError, KeyError, TypeError) as exc:
        raise ToolError(f"unreadable ruff output: {exc!r}") from exc
    return findings


def _lint_residue(scope: dict[str, LineRanges]) -> list[str]:
    """Lint the fix pass could not fix, on changed lines of changed project files."""
    files = _scoped_files(scope, (*QUALITY_SOURCES, TEST_DIR))
    if not files:
        return []
    cmd = ["uv", "run", "ruff", "check", "--no-fix", "--output-format=json", *files]
    return _ruff_findings(_run_tool("ruff", cmd, ok=(0, 1)), scope, Path.cwd())


# ── Complexity delta ──


def _lizard_row(row: list[str]) -> tuple[str, str, FunctionMetrics] | None:
    """(file, long_name, metrics) from one `lizard --csv` row; None for anything else.

    Columns: nloc, ccn, tokens, params, length, location, file, name, long_name, start, end.
    """
    if len(row) < 11 or not all(row[i].isdigit() for i in (1, 3, 4, 9)):
        return None
    ccn, args, length, start = (int(row[i]) for i in (1, 3, 4, 9))
    return row[6], row[8], FunctionMetrics(row[7], start, ccn, args, length)


def _parse_lizard_csv(text: str) -> dict[str, dict[str, FunctionMetrics]]:
    """`{file: {key: metrics}}` from `lizard --csv`, keyed by long_name (the signature).

    A signature survives a function moving within its file; a start line does not. A
    repeated signature (two classes' `__init__( self )`) is keyed `#2`, `#3` in file order.
    """
    functions: dict[str, dict[str, FunctionMetrics]] = {}
    for row in csv.reader(text.splitlines()):
        parsed = _lizard_row(row)
        if parsed is None:
            continue
        path, long_name, metrics = parsed
        in_file = functions.setdefault(path, {})
        key, copy = long_name, 1
        while key in in_file:
            copy += 1
            key = f"{long_name}#{copy}"
        in_file[key] = metrics
    return functions


def _base_twin(
    key: str,
    now: FunctionMetrics,
    current: dict[str, FunctionMetrics],
    base: dict[str, FunctionMetrics],
) -> FunctionMetrics | None:
    """The base version of a current function: same signature, else the same unique name.

    The name fallback keeps a signature-only edit (a new annotation) on a legacy function
    from reading as a brand-new function.
    """
    if key in base:
        return base[key]
    twins = [fn for fn in base.values() if fn.name == now.name]
    unique_now = sum(fn.name == now.name for fn in current.values()) == 1
    return twins[0] if unique_now and len(twins) == 1 else None


def _function_regressions(
    path: str, now: FunctionMetrics, was: FunctionMetrics | None
) -> list[str]:
    """One line per limit `now` exceeds where `was` is absent or measured lower."""
    lines: list[str] = []
    for label, field, limit in COMPLEXITY_LIMITS:
        value = getattr(now, field)
        before = None if was is None else getattr(was, field)
        if value > limit and (before is None or value > before):
            shown = "new" if before is None else before
            lines.append(f"{path}:{now.line}: {now.name} {label} {shown}→{value} (limit {limit})")
    return lines


def _complexity_delta(
    current: dict[str, dict[str, FunctionMetrics]],
    base: dict[str, dict[str, FunctionMetrics]],
) -> list[str]:
    """Functions over a limit now that are new, or worse than their base version."""
    findings: list[str] = []
    for path, functions in current.items():
        base_functions = base.get(path, {})
        for key, now in functions.items():
            was = _base_twin(key, now, functions, base_functions)
            findings.extend(_function_regressions(path, now, was))
    return findings


def _lizard_functions(
    files: list[str], cwd: str | None = None
) -> dict[str, dict[str, FunctionMetrics]]:
    # lizard with no file arguments walks the working directory; never let it.
    if not files:
        return {}
    return _parse_lizard_csv(_run_tool("lizard", ["uvx", LIZARD, "--csv", *files], cwd=cwd))


def _write_base_sources(files: list[str], base: str, root: Path) -> list[str]:
    """Write each file's `base` version under `root`; returns those that existed at base."""
    written: list[str] = []
    for path in files:
        shown = subprocess.run(
            ["git", "show", f"{base}:./{path}"], capture_output=True, check=False
        )
        if shown.returncode != 0:
            continue  # absent at base: every function in it is new
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(shown.stdout)
        written.append(path)
    return written


def _complexity_regressions(scope: dict[str, LineRanges], base: str | None) -> list[str]:
    """Complexity this change introduced or worsened, over the complexity gate's targets."""
    files = _scoped_files(scope, (*APP_SOURCES, TEST_DIR))
    current = _lizard_functions(files)
    if not current:
        return []
    with tempfile.TemporaryDirectory(prefix="harness-base-") as tmp:
        written = _write_base_sources(files, base, Path(tmp)) if base else []
        before = _lizard_functions(written, cwd=tmp)
    return _complexity_delta(current, before)


# ── Dead-code delta ──


def _vulture_findings(output: str, scope: dict[str, LineRanges]) -> list[str]:
    """vulture's own `path:line: message` lines that sit on a changed line."""
    findings: list[str] = []
    for line in output.splitlines():
        match = _VULTURE_LINE_RE.match(line)
        if match and _in_ranges(int(match["line"]), scope.get(match["path"], [])):
            findings.append(line)
    return findings


def _deadcode_residue(scope: dict[str, LineRanges]) -> list[str]:
    """Dead code on changed lines. vulture still reads all of src/: deadness is global."""
    if not _scoped_files(scope, APP_SOURCES):
        return []
    output = _run_tool("vulture", _deadcode_gate().cmd, ok=(0, 3))  # 3: dead code found
    return _vulture_findings(output, scope)


# ── Stop-hook verdict ──


def _delta_result(gate: str, measure: Callable[[], list[str]]) -> DeltaResult:
    try:
        return DeltaResult(gate, measure())
    except ToolError as exc:
        return DeltaResult(gate, [], str(exc))


def _run_delta_gates(scope: dict[str, LineRanges], base: str | None) -> list[DeltaResult]:
    """Lint residue, complexity delta, and dead-code delta; read-only, in parallel."""
    gates: list[tuple[str, Callable[[], list[str]]]] = [
        ("Lint", lambda: _lint_residue(scope)),
        ("Complexity", lambda: _complexity_regressions(scope, base)),
        ("Dead code", lambda: _deadcode_residue(scope)),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(gates)) as executor:
        return list(executor.map(lambda gate: _delta_result(*gate), gates))


def _cap_findings(findings: list[str]) -> list[str]:
    """At most HOOK_FINDING_LIMIT findings, then one line counting the rest.

    `--verbose` lifts the cap, which is what the counting line tells the reader to run.
    """
    if VERBOSE or len(findings) <= HOOK_FINDING_LIMIT:
        return list(findings)
    rest = len(findings) - HOOK_FINDING_LIMIT
    return [*findings[:HOOK_FINDING_LIMIT], f"… +{rest} more — run `{STOP_HOOK_RERUN}`"]


def _stop_hook_payload(results: list[DeltaResult]) -> str:
    """The stderr block an agent reads: failed gates, then their findings; '' when clean."""
    failed = [result for result in results if result.findings]
    if not failed:
        return ""
    header = f"stop-hook failed: {', '.join(result.gate for result in failed)}"
    findings = [line for result in failed for line in result.findings]
    return "\n".join([header, *_cap_findings(findings)])


def _payload_digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stop_hook_exit(payload: str, failed_tools: int, event: dict[str, Any], stored: str) -> int:
    """2 blocks on findings; 1 for a tool failure or a repeated block; 0 when clean.

    A repeat is the stored digest of the same payload while the agent is already
    continuing because of a stop hook (`stop_hook_active`): blocking again would loop.
    A payload that changed blocks again.
    """
    if payload:
        repeat = event.get("stop_hook_active") is True and stored == _payload_digest(payload)
        return 1 if repeat else 2
    return 1 if failed_tools else 0


def _loop_guard_key(prefix: str) -> str:
    """A file-name-safe key for this project within its repository."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", prefix).strip("-") or "root"


def _loop_guard_path() -> Path | None:
    git_path = _git_lines(["rev-parse", "--git-path", "harness"])
    if not git_path:
        return None
    return Path(git_path[0]).resolve() / f"stop-hook-{_loop_guard_key(_git_prefix())}"


def _read_digest(path: Path | None) -> str:
    try:
        return path.read_text(encoding="utf-8").strip() if path else ""
    except OSError:
        return ""


def _update_loop_guard(path: Path | None, code: int, payload: str) -> None:
    """Remember a block's digest; forget it once the stop is clean."""
    if path is None:
        return
    if code == 2:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_payload_digest(payload), encoding="utf-8")
    elif code == 0:
        path.unlink(missing_ok=True)


def _report_stop_hook(results: list[DeltaResult], event: dict[str, Any]) -> int:
    """Print the verdict to stderr (nothing when clean) and return the exit code."""
    payload = _stop_hook_payload(results)
    problems = [f"stop-hook: {r.gate} could not run: {r.problem}" for r in results if r.problem]
    guard = _loop_guard_path()
    code = _stop_hook_exit(payload, len(problems), event, _read_digest(guard))
    for line in problems:
        print(line, file=sys.stderr)
    if payload:
        print(payload, file=sys.stderr)
    if payload and code == 1:
        print(LOOP_GUARD_NOTICE, file=sys.stderr)
    _update_loop_guard(guard, code, payload)
    return code


def _hook_event() -> dict[str, Any]:
    """The agent's hook JSON from stdin; `{}` for a terminal or empty/invalid input."""
    try:
        if sys.stdin.isatty():
            return {}
        received, _ = _read_stdin(HOOK_STDIN_TIMEOUT)
        event = json.loads(received)
    except (AttributeError, OSError, ValueError):
        return {}
    return event if isinstance(event, dict) else {}


def _fix_and_format(files: list[str]) -> None:
    """Fix, then format, `files` in place, silently; what is left surfaces as lint residue."""
    if not files:
        return
    for args in (["check", "--fix"], ["format"]):
        try:
            subprocess.run(["uv", "run", "ruff", *args, *files], capture_output=True, check=False)
        except OSError:
            return


def _agents_md_stale() -> bool:
    """True when CLAUDE.md exists and AGENTS.md is missing or differs from it."""
    claude, agents = Path("CLAUDE.md"), Path("AGENTS.md")
    if not claude.is_file():
        return False
    return not agents.is_file() or agents.read_bytes() != claude.read_bytes()


def _mirror_claude_md() -> None:
    Path("AGENTS.md").write_bytes(Path("CLAUDE.md").read_bytes())


def _sync_agents_md_after_edit() -> None:
    """Stop hook: an uncommitted CLAUDE.md edit carries into AGENTS.md, silently.

    CLAUDE.md is canonical. An edit to AGENTS.md alone is left for pre-commit to report.
    """
    if _git_lines(["status", "--porcelain", "--", "CLAUDE.md"]) and _agents_md_stale():
        _mirror_claude_md()


def cmd_stop_hook() -> None:
    """Post-edit, then changed-lines lint, complexity delta, dead-code delta.

    Silent on success. Findings exit 2 with a capped stderr payload; a tool that could
    not run exits 1; the same findings on a stop the agent is already continuing from
    exit 1 (loop guard). `check` and `ci` keep the whole-tree gates.
    """
    event = _hook_event()  # stdin belongs to the hook event; read it before anything else
    _fix_and_format(_changed_py_files())
    _sync_agents_md_after_edit()
    base = _delta_base()
    try:
        scope = _changed_scope(base)
    except ToolError as exc:
        print(f"stop-hook: changed lines could not run: {exc}", file=sys.stderr)
        sys.exit(1)
    code = _report_stop_hook(_run_delta_gates(scope, base), event)
    if VERBOSE and code == 0:
        print(f"stop-hook: clean ({len(scope)} changed path(s) vs {base or 'no commits'})")
    if code:
        sys.exit(code)


def _hook_target(event: dict[str, Any], root: Path) -> str | None:
    """The project .py file a PostToolUse event names, relative to `root`; else None."""
    tool_input = event.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path:
        return None
    resolved = (root / file_path).resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None  # outside this project: another harness owns it
    return relative if _is_project_python_file(relative) and resolved.is_file() else None


def _post_edit_hook() -> None:
    """Fix and format the one file a PostToolUse event names. Never blocks.

    Prints one additionalContext line when the file changed, so the agent re-reads it
    before its next edit; otherwise nothing.
    """
    target = _hook_target(_hook_event(), Path.cwd())
    if target is None:
        return
    path = Path(target)
    try:
        before = path.read_bytes()
        _fix_and_format([target])
        changed = path.read_bytes() != before
    except OSError:
        return
    if changed:
        context = {
            "hookEventName": "PostToolUse",
            "additionalContext": POST_EDIT_NOTICE.format(target),
        }
        print(
            json.dumps({"hookSpecificOutput": context}, separators=(",", ":"), ensure_ascii=False)
        )


def cmd_post_edit() -> None:
    """Format if source files have uncommitted changes; `--hook`: the file a hook event names."""
    if "--hook" in sys.argv:
        _post_edit_hook()
        return
    files = _changed_py_files()
    if not files:
        return
    run("Fix lint errors", ["uv", "run", "ruff", "check", "--fix", *files], no_exit=True)
    run("Format code", ["uv", "run", "ruff", "format", *files], no_exit=True)


# ── Stages ────────────────────────────────────────────────────────


def _hook_wired(wiring: HookWiring) -> bool:
    """True when the settings file has a handler for this hook under its event."""
    try:
        data = _read_json_object(Path(wiring.path))
    except (OSError, ValueError):
        return False
    hooks = data.get("hooks")
    groups = hooks.get(wiring.event) if isinstance(hooks, dict) else None
    if not isinstance(groups, list):
        return False
    return any(
        _is_harness_handler(handler, wiring.marker)
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("hooks"), list)
        for handler in group["hooks"]
    )


def _check_stop_hooks_present() -> None:
    """Warn when the Claude/Codex Stop or Claude PostToolUse wiring is missing."""
    for wiring in HOOK_WIRINGS:
        label = f"{wiring.event} hook wiring"
        if _hook_wired(wiring):
            print(f"  {GREEN}✓{RESET} {label} ({wiring.path})")
        else:
            print(f"  {RED}⚠{RESET} Missing {label}: {wiring.path}")


def _first_diff_line(a: str, b: str) -> int:
    """Return 1-based line number of the first line that differs."""
    al, bl = a.splitlines(), b.splitlines()
    for i in range(min(len(al), len(bl))):
        if al[i] != bl[i]:
            return i + 1
    return min(len(al), len(bl)) + 1


def _check_agents_md_drift() -> None:
    """Fail if AGENTS.md differs from CLAUDE.md (byte-compare)."""
    claude = Path("CLAUDE.md")
    agents = Path("AGENTS.md")
    if not claude.exists():
        print(f"  {RED}✗{RESET} agents-md-drift: CLAUDE.md not found")
        sys.exit(1)
    if not agents.exists():
        print(f"  {RED}✗{RESET} agents-md-drift: AGENTS.md missing — run `harness sync-agents-md`")
        sys.exit(1)
    a, b = claude.read_bytes(), agents.read_bytes()
    if a == b:
        print(f"  {GREEN}✓{RESET} agents-md-drift")
        return
    line = _first_diff_line(
        a.decode("utf-8", errors="replace"),
        b.decode("utf-8", errors="replace"),
    )
    print(
        f"  {RED}✗{RESET} agents-md-drift: AGENTS.md differs from CLAUDE.md "
        f"(first diff at line {line}) — run `harness sync-agents-md`"
    )
    sys.exit(1)


def cmd_sync_agents_md() -> None:
    """Overwrite AGENTS.md with CLAUDE.md contents."""
    if not Path("CLAUDE.md").exists():
        print(f"  {RED}✗{RESET} sync-agents-md: CLAUDE.md not found")
        sys.exit(1)
    _mirror_claude_md()
    print(f"  {GREEN}✓{RESET} sync-agents-md: AGENTS.md ← CLAUDE.md")


def _sync_agents_md_staged() -> None:
    """pre-commit: a staged CLAUDE.md carries AGENTS.md into the same commit.

    The `git add` inherits git's hook environment on purpose: GIT_INDEX_FILE is the
    index this commit is being built from.
    """
    staged = _git_lines(["diff", "--cached", "--name-only", "--", "CLAUDE.md"])
    if not staged or not _agents_md_stale():
        return
    _mirror_claude_md()
    added = subprocess.run(
        ["git", "add", "--", "AGENTS.md"], capture_output=True, text=True, check=False
    )
    if added.returncode != 0:
        print(f"  {RED}✗{RESET} sync-agents-md: git add AGENTS.md failed")
        print(added.stderr, end="")
        sys.exit(1)
    print(f"  {GREEN}✓{RESET} sync-agents-md: AGENTS.md ← CLAUDE.md (staged)")


def cmd_agents_md_drift() -> None:
    """Run the AGENTS.md / CLAUDE.md drift check."""
    _check_agents_md_drift()


def _agents_md_drift_gate() -> Gate:
    return Gate(
        "Agents drift",
        ["uv", "run", "harness", "agents-md-drift"],
        "run `harness sync-agents-md`",
    )


def cmd_check() -> None:
    """Fix, format, typecheck, and test the full repo."""
    print("\n=== Quality Checks ===\n")
    try:
        cmd_fix()
        cmd_format()
        cmd_typecheck()
        cmd_test()
        _check_stop_hooks_present()
        _check_arch_config_guard(warn_only=True)
        _check_agents_md_drift()
    finally:
        _check_suppressions_baseline()


def cmd_pre_commit() -> None:
    """Fix/format staged files, typecheck, and mirror a staged CLAUDE.md; tests run at pre-push."""
    print("\n=== Pre-commit Checks ===\n")
    _check_arch_config_guard(warn_only=True, staged=True)
    _sync_agents_md_staged()
    files = _staged_py_files()
    if files or _git_lines(["diff", "--cached", "--name-only", "--", "AGENTS.md", "CLAUDE.md"]):
        _check_agents_md_drift()
    if not files:
        print("No staged Python files — skipping checks")
        return

    cmd_fix(files)
    cmd_format(files)
    cmd_typecheck()


def cmd_ci() -> None:
    """Run full read-only verification.

    Read-only gates run as a parallel batch (lint, format check, typecheck, audit,
    complexity, deadcode, agents-md drift, acceptance, arch) — captured and printed in
    submission order, run to completion so one pass surfaces every failure. Coverage and
    CRAP run after the batch: coverage is captured, CRAP is advisory unless --enforce.
    """
    print("\n=== CI Checks ===\n")
    gates = [
        _lint_gate(),
        _format_check_gate(),
        _typecheck_gate(),
        _audit_gate(),
        _complexity_gate(),
        _deadcode_gate(),
        _agents_md_drift_gate(),
        *_acceptance_gates_or_warn(),
        *_arch_gates_or_warn(),
    ]
    all_ok = run_gates_parallel(gates)
    cmd_coverage()  # self-skips; sequential, after the batch
    cmd_crap()  # reads .coverage/coverage.xml; advisory unless --enforce
    all_ok = _check_arch_config_guard() and all_ok
    all_ok = _check_suppressions_baseline(no_exit=True) and all_ok
    _exit_if_failed(all_ok)


def cmd_pre_push() -> None:
    """Read-only push gate: the offline checks pre-commit and stop-hook do not run.

    pre-commit covers fix/format/typecheck on staged files; stop-hook covers the
    change's delta. This fills the gap with the deterministic, offline gates none of
    them run — tests, lint, format check, agents-md drift, acceptance, arch —
    validating the whole pushed tree (after merges/rebases/--no-verify, which pre-commit
    may never have seen) before it leaves the machine. Tests run first and alone: they
    write caches (.hypothesis/, bytecode). Network (audit) and advisory (coverage/CRAP)
    gates stay in ci.
    """
    print("\n=== Pre-push Checks ===\n")
    if not _check_branch_guard():
        sys.exit(1)
    arch_config_ok = _check_arch_config_guard(include_pre_push_refs=True)
    tests_ok = _check_tests()
    gates = [
        _lint_gate(),
        _format_check_gate(),
        _agents_md_drift_gate(),
        *_acceptance_gates_or_warn(),
        *_arch_gates_or_warn(),
    ]
    _exit_if_failed(run_gates_parallel(gates) and arch_config_ok and tests_ok)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(data, indent=2)}\n", encoding="utf-8")


def _json_object_child(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    child = data.get(key)
    if child is None:
        child = {}
        data[key] = child
    if not isinstance(child, dict):
        raise ValueError(f"{path}:{key} must contain a JSON object")
    return child


def _json_list_child(data: dict[str, Any], key: str, path: Path) -> list[Any]:
    child = data.get(key)
    if child is None:
        child = []
        data[key] = child
    if not isinstance(child, list):
        raise ValueError(f"{path}:{key} must contain a JSON array")
    return child


def _is_harness_handler(handler: object, marker: str) -> bool:
    """True for a command handler that already runs this harness hook (any form)."""
    return (
        isinstance(handler, dict)
        and handler.get("type") == "command"
        and isinstance(handler.get("command"), str)
        and marker in handler["command"]
    )


def _install_hook(wiring: HookWiring) -> None:
    """Inject/refresh one hook in its settings file, preserving every other hook.

    Idempotent: an existing handler carrying the wiring's marker (current or legacy)
    is replaced in place and any duplicates are dropped, so re-running never
    accumulates entries.
    """
    path = Path(wiring.path)
    data = _read_json_object(path)
    if wiring.path == CLAUDE_SETTINGS and "$schema" not in data:
        data["$schema"] = CLAUDE_SETTINGS_SCHEMA

    hooks = _json_object_child(data, "hooks", path)
    event_groups = _json_list_child(hooks, wiring.event, path)
    installed = False

    for group in event_groups:
        if not isinstance(group, dict):
            continue
        group_hooks = group.get("hooks")
        if not isinstance(group_hooks, list):
            continue
        next_group_hooks: list[Any] = []
        for handler in group_hooks:
            if _is_harness_handler(handler, wiring.marker):
                if not installed:
                    next_group_hooks.append(dict(wiring.handler))
                    installed = True
                continue
            next_group_hooks.append(handler)
        group["hooks"] = next_group_hooks

    if not installed:
        matcher = {} if wiring.matcher is None else {"matcher": wiring.matcher}
        event_groups.append({**matcher, "hooks": [dict(wiring.handler)]})

    _write_json_object(path, data)


def _git_hook_path(name: str) -> Path:
    """Resolve a git hook path via `git rev-parse` so worktrees / core.hooksPath work."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", f"hooks/{name}"],
            capture_output=True,
            text=True,
            check=False,
            env=_env_without_git(),
        )
    except FileNotFoundError:
        return Path(f".git/hooks/{name}")

    hook = result.stdout.strip()
    if result.returncode == 0 and hook:
        return Path(hook)
    return Path(f".git/hooks/{name}")


def _install_git_hook(name: str) -> None:
    """Install a git hook shim that runs the matching `harness <name>`."""
    hook = _git_hook_path(name)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\nuv run harness {name}\n", encoding="utf-8")
    hook.chmod(0o755)


def cmd_hooks() -> None:
    """Install git pre-commit + pre-push hooks and the Claude/Codex agent hook wiring."""
    _install_git_hook("pre-commit")
    _install_git_hook("pre-push")
    for wiring in HOOK_WIRINGS:
        _install_hook(wiring)
    print("Installed pre-commit, pre-push, Claude/Codex Stop, and Claude PostToolUse hooks")


def cmd_clean() -> None:
    """Remove cache and build artifacts."""
    print("\n=== Cleaning Up ===\n")
    for name in [
        ".ruff_cache",
        ".pytest_cache",
        ".import_linter_cache",
        "build",
        "dist",
        "htmlcov",
        "mutants",
    ]:
        p = Path(name)
        if p.is_dir():
            shutil.rmtree(p)
    for name in [".coverage", "coverage.xml"]:
        p = Path(name)
        if p.is_file():
            p.unlink()
    for p in Path().glob("*.egg-info"):
        if p.is_dir():
            shutil.rmtree(p)
    for p in Path().rglob("__pycache__"):
        shutil.rmtree(p)
    run("Ruff clean", ["uv", "run", "ruff", "clean"])


# ── CLI dispatch ──────────────────────────────────────────────────

TASKS: dict[str, tuple[Callable[..., None], str]] = {
    "fix": (cmd_fix, "Fix lint errors with ruff"),
    "format": (cmd_format, "Format code with ruff"),
    "lint": (cmd_lint, "Lint code with ruff (read-only)"),
    "typecheck": (cmd_typecheck, "Type-check with basedpyright"),
    "test": (cmd_test, "Run tests, or syntax check when no tests exist"),
    "check": (cmd_check, "Fix + format + typecheck + test (full repo)"),
    "pre-commit": (cmd_pre_commit, "Staged fix/format + typecheck; mirrors a staged CLAUDE.md"),
    "pre-push": (
        cmd_pre_push,
        "Read-only push gate: branch guard, tests, lint, format check, agents-md drift, "
        "acceptance, arch",
    ),
    "ci": (
        cmd_ci,
        "Full verification: lint, typecheck, tests, acceptance, coverage, crap, arch, "
        "agents-md drift",
    ),
    "audit": (cmd_audit, "Audit dependencies for known vulnerabilities"),
    "acceptance": (cmd_acceptance, "Run acceptance scenarios (behave)"),
    "coverage": (cmd_coverage, "Tests with coverage threshold (--min=N)"),
    "mutation": (cmd_mutation, "Mutation testing (mutmut, advisory)"),
    "crap": (cmd_crap, "CRAP complexity x coverage gate (advisory)"),
    "suppressions": (cmd_suppressions, "Show or update suppression baseline"),
    "complexity": (cmd_complexity, "Cyclomatic complexity gate (lizard, CCN 15, args 8)"),
    "deadcode": (cmd_deadcode, "Detect unused (dead) code with vulture (app sources only)"),
    "arch": (cmd_arch, "Architecture checks (import-linter)"),
    "arch-config-guard": (cmd_arch_config_guard, "Block unreviewed arch config changes"),
    "branch-guard": (cmd_branch_guard, "Refuse pushes to protected branches (main/master)"),
    "post-edit": (cmd_post_edit, "Format changed files (--hook: the file a PostToolUse names)"),
    "stop-hook": (
        cmd_stop_hook,
        "post-edit, then changed-lines lint, complexity delta, deadcode delta; "
        "silent on success, exit 2 with findings",
    ),
    "agents-md-drift": (cmd_agents_md_drift, "Fail if AGENTS.md differs from CLAUDE.md"),
    "sync-agents-md": (cmd_sync_agents_md, "Overwrite AGENTS.md from CLAUDE.md"),
    "setup-hooks": (
        cmd_hooks,
        "Install git pre-commit + pre-push hooks and Claude/Codex agent hook wiring",
    ),
    "clean": (cmd_clean, "Remove cache and build artifacts"),
}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if not args:
        cmd_check()
        return

    task_name = args[0]
    if task_name not in TASKS:
        print(f"Unknown command: {task_name}")
        sys.exit(1)

    TASKS[task_name][0]()


if __name__ == "__main__":
    main()
