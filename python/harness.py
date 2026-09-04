#!/usr/bin/env python3
"""Project development tasks. Zero dependencies — stdlib only.

Scoping rule: fix/format/lint/typecheck NEVER run against the whole tree by
default. They only ever touch a git-derived file list — working tree + index +
untracked files + the diff against the base ref — and, within those files, only
the *lines* this change actually touched, mapped from the diff's `@@` hunks.
Adopting this harness into an existing repo must not rewrite, or fail on, that
repo's pre-existing code: touching one line in a legacy file must not surface
every violation already sitting in it. Pass `--whole-file` to keep the file scope
but check every line, `--all` for a whole-tree run, `--base=<ref>` to pick the
diff base.

Count-ratcheted gates (complexity, CRAP, coverage, dead code, suppressions) stay
whole-tree on purpose: scoping them would make their counts meaningless. Each
reads its floor from `.harness-baseline`; with no floor recorded they report
instead of blocking, so retrofitting into an existing repo is green on day one.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import difflib
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

# ── Configuration ─────────────────────────────────────────────────

APP_SOURCES = ("src",)
QUALITY_SOURCES = ("src", "harness.py")
TEST_DIR = "tests"
# The argv tail that runs this project's test suite, handed to the interpreter
# (`python <TEST_COMMAND>`) and to coverage (`coverage run <TEST_COMMAND>`) alike,
# so both stay one edit apart. Defaults to stdlib unittest so a repo with no test
# dependencies still runs. A pytest repo edits this one line and nothing else, to
# the tuple ("-m", "pytest", "-q").
TEST_COMMAND = ("-m", "unittest", "discover", "-s", TEST_DIR, "-q")
LIZARD = "lizard"
VULTURE = "vulture"
VULTURE_MIN_CONFIDENCE = "60"
VULTURE_ALLOWLIST = "vulture_allowlist.py"
COMPLEXITY_MAX_ARGS = 8
COMPLEXITY_MAX_CCN = 15
CRAP_MAX_DEFAULT = 30.0
BASELINE_FILE = ".harness-baseline"
# A tolerance so high the gate can never trip: how a count-ratcheted gate runs when
# it has no committed floor, and how it measures instead of judging.
REPORT_ONLY_LIMIT = 1_000_000
SUPPRESSION_BASELINE_PREFIX = "suppressions."
ARCH_CONFIGS = (".importlinter",)
# Console scripts whose PyPI distribution is named differently from the command.
# `uvx <name>` resolves a *distribution*, so these need `uvx --from <dist> <name>`
# or uv reports "<name> was not found in the package registry". Every other tool
# the harness runs (ruff, basedpyright, coverage, behave, vulture, lizard) ships a
# console script matching its distribution name and needs no entry here.
TOOL_DISTRIBUTIONS = {"lint-imports": "import-linter"}
ARCH_CONFIG_ALLOW_ENV = "HARNESS_ALLOW_ARCH_CONFIG"
GHERKIN_ALLOW_ENV = "HARNESS_ALLOW_NO_FEATURE"
AGENTS_MD_ALLOW_ENV = "HARNESS_ALLOW_AGENTS_MD_DRIFT"
# Line-similarity floor below which `sync-agents-md` refuses to overwrite AGENTS.md.
# Above it the destination is plausibly a stale copy of CLAUDE.md (the template case,
# where the copy is the whole point); below it, it is somebody's own document.
AGENTS_MD_SYNC_MIN_RATIO = 0.5

# ── Hook wiring (installed by `setup-hooks`) ──────────────────────
# Claude reads .claude/settings.json and runs the harness directly; Codex reads
# .codex/hooks.json and goes through the codex-stop-hook.sh wrapper (which turns
# the exit code into the block/continue JSON Codex expects). Keep both forms in
# sync with the committed template files so re-running the installer is a no-op.
CLAUDE_SETTINGS_SCHEMA = "https://json.schemastore.org/claude-code-settings.json"
CLAUDE_STOP_COMMAND = "cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook || exit 2"
CODEX_STOP_COMMAND = (
    'cd "$(git rev-parse --show-toplevel)" && '
    ".codex/hooks/codex-stop-hook.sh uv run harness stop-hook"
)
CLAUDE_STOP_HOOK: dict[str, Any] = {"type": "command", "command": CLAUDE_STOP_COMMAND}
CODEX_STOP_HOOK: dict[str, Any] = {
    "type": "command",
    "command": CODEX_STOP_COMMAND,
    "timeout": 300,
    "statusMessage": "Running stop-hook checks",
}

# ── Output ────────────────────────────────────────────────────────

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
VERBOSE = "--verbose" in sys.argv
ALL_FILES = "--all" in sys.argv
WHOLE_FILE = "--whole-file" in sys.argv
BASE_OVERRIDE = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--base=")), None)

# Ruff codes a line-scoped filter would hide, because the change that causes them
# happens somewhere the code is not reported: an import this edit orphaned (F401),
# a definition it shadowed (F811). Both are reported on a line the edit never wrote.
#
# `I001` deliberately does NOT belong here, though it looks like it should: ruff
# reports an unsorted import block spanning the *whole* block, so an edit inside the
# imports already intersects it under the ordinary line filter. Exempting it as well
# only means a pre-existing unsorted block turns red the moment you append a comment
# somewhere else in the file — and `fix` cannot resort it without rewriting untouched
# lines, so the gate would be red with no way out. Extend this set only on a concrete
# false negative; every addition costs adoptability.
WHOLE_FILE_CODES = frozenset({"F401", "F811"})


def _tool(name: str, *, read_only: bool = False) -> list[str]:
    """Argv prefix that runs a dev tool, in descending order of fidelity.

    1. `uv run` when this is a uv project *and* the tool is already installed in
       `.venv/bin` — the version then comes from the project's own dev
       dependencies, and `uv run` additionally puts the project's own packages on
       the import path (behave and coverage need that). Gated on the binary
       already existing, because `uv run` on a missing tool does not fall through:
       it exits 2 with "Failed to spawn", and on the way there it *creates*
       `.venv`, which a read-only stage must never do. `--no-sync` for read-only
       stages is the second belt: plain `uv run` implicitly syncs, i.e. installs
       packages, and `pre-push`/`ci` must not mutate anything.
    2. `.venv/bin/<tool>` when there is no `pyproject.toml` — a pip-installed
       virtualenv is what a not-yet-migrated repo actually has.
    3. `uvx <tool>` as the last resort, so the runner still starts in a repo with
       neither — including a `pyproject.toml` with no `[project]` table, where
       `uv sync` cannot install dev dependencies at all, so tier 1 would fail
       forever. `uvx` resolves a distribution name, so tools whose console script
       differs from their distribution (see TOOL_DISTRIBUTIONS) get `--from`.
       When `uv.lock` records the distribution, `--from <dist>==<version>` pins
       the fallback to the release `uv run` would have used, so the gate reports
       the same findings whether or not the venv happens to be synced. No lock,
       or no entry for the tool, means unpinned — never a failure.
    """
    local = Path(".venv", "bin", name)
    if not local.exists():
        distribution = TOOL_DISTRIBUTIONS.get(name, name)
        version = _lock_versions().get(distribution)
        spec = f"{distribution}=={version}" if version else distribution
        pin = ["--from", spec] if version or distribution != name else []
        return ["uvx", *pin, name]
    if Path("pyproject.toml").exists():
        return ["uv", "run", *(["--no-sync"] if read_only else []), name]
    return [str(local)]


def _lock_versions() -> dict[str, str]:
    """`{distribution: version}` from `uv.lock`, or `{}` when it is absent or unreadable.

    The lock holds exact versions where `pyproject.toml` holds ranges, so it is the
    only place the uvx fallback can learn what `uv run` would have run. Any failure
    to read it — no file, bad TOML, no `package` table — yields `{}`: the pin is a
    fidelity gain, never a reason for the runner to stop starting.
    """
    return _read_lock_versions(Path("uv.lock").resolve())


@functools.cache
def _read_lock_versions(lock: Path) -> dict[str, str]:
    """Parse one lock file once per run; keyed on the path so a cwd change re-reads."""
    try:
        packages = tomllib.loads(lock.read_text(encoding="utf-8"))["package"]
        return {package["name"]: package["version"] for package in packages}
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        return {}


def _python(*, read_only: bool = False) -> list[str]:
    """Interpreter argv for running tests, resolved on the same tiers as `_tool`.

    Only the last resort differs: `uvx python` would resolve a PyPI *distribution*
    named "python", not an interpreter, so a repo with neither `.venv` nor a uv
    project falls back to the interpreter already running this file.
    """
    argv = _tool("python", read_only=read_only)
    return [sys.executable] if argv[0] == "uvx" else argv


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
    """A read-only gate's label and command, shared by standalone cmd_* and the batch.

    `runner` is the escape hatch for a gate whose verdict is *not* its command's exit
    code — the line-scoped gates parse a tool's JSON and judge only the findings that
    land on changed lines. `cmd` stays populated for those too, because it is what
    `--verbose` echoes and what the failure body names.
    """

    description: str
    cmd: list[str]
    hint: str | None = None
    runner: Callable[[], GateResult] | None = None


def run_capture(description: str, cmd: list[str], hint: str | None = None) -> GateResult:
    """Run a command with output captured; the thread-safe unit for the parallel batch."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
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
    runner: Callable[[], GateResult] | None = None,
) -> bool:
    """Run command silently; show output only on failure. Returns True on success.

    Pass stream=True only when live output is part of the command contract.
    Pass runner to have a `Gate` compute its own result (line-scoped gates do);
    `cmd` is then only echoed under --verbose, never executed here.
    """
    if runner is not None:
        if VERBOSE:
            print(f"  -> {' '.join(cmd)}")
        result = runner()
        print_gate_result(result, no_exit=no_exit)
        return result.ok

    if VERBOSE or stream:
        # Output already streamed to the terminal, so the captured bodies stay empty —
        # but the ✓/✗ line still prints. `--verbose` is the flag you reach for when
        # something is wrong; it must not be the one that hides which gate failed.
        print(f"  -> {' '.join(cmd)}")
        streamed = subprocess.run(cmd, check=False)
        print_gate_result(
            GateResult(description, cmd, streamed.returncode, "", "", hint), no_exit=no_exit
        )
        return streamed.returncode == 0

    result = run_capture(description, cmd, hint)
    print_gate_result(result, no_exit=no_exit)
    return result.ok


def _execute_gate(gate: Gate) -> GateResult:
    """Produce a gate's result: its own runner if it has one, else its exit code."""
    if gate.runner is not None:
        return gate.runner()
    return run_capture(gate.description, gate.cmd, gate.hint)


def run_gates_parallel(gates: list[Gate]) -> tuple[bool, list[str]]:
    """Run read-only gates concurrently, then print each result in submission order.

    Returns (all_ok, failed_descriptions). Unlike the fail-fast standalone gates, this
    runs all gates to completion so one pass surfaces every failure; the caller exits
    non-zero afterward. Output is captured and printed in submission order (not as
    they finish) so a parallel run reads the same every time — matching the monorepo
    Makefile's buffered, deterministic dump. VERBOSE falls back to a sequential run
    so the live `-> cmd` echoes stay ordered.
    """
    if not gates:
        return True, []

    if VERBOSE:
        all_ok = True
        failed: list[str] = []
        for gate in gates:
            print(f"  -> {' '.join(gate.cmd)}")
            if gate.runner is not None:
                result = gate.runner()
            else:
                streamed = subprocess.run(gate.cmd, check=False)
                result = GateResult(
                    gate.description, gate.cmd, streamed.returncode, "", "", gate.hint
                )
            print_gate_result(result, no_exit=True)
            all_ok = all_ok and result.ok
            if not result.ok:
                failed.append(gate.description)
        return all_ok, failed

    max_workers = min(len(gates), os.cpu_count() or 4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_execute_gate, gates))

    all_ok = True
    failed = []
    for result in results:
        print_gate_result(result, no_exit=True)
        all_ok = all_ok and result.ok
        if not result.ok:
            failed.append(result.description)
    return all_ok, failed


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


def _advisory_glyph(*, enforce: bool) -> str:
    """Red ✗ only for a gate that actually blocks (--enforce); green ⚠ otherwise.

    A red ✗ on a gate that exits 0 misreports severity — reserve it for runs that
    exit non-zero.
    """
    return f"{RED}✗{RESET}" if enforce else f"{GREEN}⚠{RESET}"


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


def _is_quality_python_file(path: str) -> bool:
    """Return true for non-test quality targets."""
    return _matches_python_target(path, QUALITY_SOURCES)


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


def _baseline_floor(key: str) -> int | None:
    """The committed floor for a ratcheted metric, or None when there is none.

    None means "never measured here" — no `.harness-baseline` at all, or a file
    that does not carry this key. Every gate reading a floor must then run
    report-only: retrofitting the harness into an existing repo has to be green on
    day one, and a floor of 0 inferred from a missing number is not a floor, it is
    a demand that the repo already be perfect.
    """
    baseline = _read_baseline()
    if baseline is None:
        return None
    return baseline.get(key)


@dataclasses.dataclass(frozen=True)
class Measurement:
    """A metric's value, or the reason there isn't one. Exactly one field is set.

    Three states, deliberately not collapsed into `int | None`:
      * `value` — measured, including a legitimate 0.
      * `unavailable` — the metric does not apply to this repo (no tests, no app
        sources). The baseline key is dropped, and the gate goes report-only.
      * `error` — the measuring tool ran and failed. `--update-baseline` aborts and
        writes nothing: a floor recorded from a broken run is worse than no floor,
        because every downstream gate trusts it.
    """

    value: int | None = None
    unavailable: str = ""
    error: str = ""


def _coverage_min_default() -> int:
    explicit = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--min=")), None)
    if explicit is not None:
        try:
            return int(explicit)
        except ValueError:
            print(f"  {RED}✗{RESET} --min={explicit!r} is not an integer")
            sys.exit(1)
    baseline = _read_baseline()
    return 0 if baseline is None else baseline.get("coverage.min", 0)


def _measured_coverage() -> Measurement:
    """Total coverage percent as coverage.py reports it.

    The test run's exit code is load-bearing: a suite where every module raises
    ImportError still produces a coverage number (a very low one), and recording
    that as the floor bakes a broken run into the ratchet. `cmd_coverage` calls the
    same run a failure, so `--update-baseline` must too.
    """
    if not _has_tests():
        return Measurement(unavailable=f"no {TEST_DIR}/test*.py files")
    if not _artifact_is_fresh(Path(".coverage"), _quality_targets()):
        run_res = subprocess.run(
            # read_only here too, matching the `coverage report` call below and
            # `cmd_coverage`. Only `--update-baseline` reaches this, which is already a
            # mutating command, so syncing would be defensible — but coverage resolving
            # two different ways in one file is exactly what produced the `ci`-creates-
            # a-`.venv` bug, so the file now has one answer for one tool.
            [*_tool("coverage", read_only=True), "run", *TEST_COMMAND],
            capture_output=True,
            text=True,
            check=False,
        )
        if run_res.returncode != 0:
            return Measurement(
                error=f"the test run under coverage failed (exit {run_res.returncode})"
            )
    res = subprocess.run(
        # `--fail-under=0` so an adopting repo's own `[tool.coverage.report]
        # fail_under` cannot turn a successful measurement into exit 2.
        [*_tool("coverage", read_only=True), "report", "--format=total", "--fail-under=0"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    if res.returncode != 0 or not lines:
        return Measurement(error=f"`coverage report` produced no total (exit {res.returncode})")
    try:
        return Measurement(value=int(float(lines[-1])))
    except ValueError:
        return Measurement(error=f"`coverage report` printed a non-numeric total: {lines[-1]!r}")


def _lizard_warning_count(stdout: str) -> int | None:
    """Read the `Warning cnt` column out of lizard's final summary row."""
    lines = stdout.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("Total nloc"):
            continue
        for row in lines[index + 1 :]:
            fields = row.split()
            if len(fields) < 6 or set(row.strip()) <= {"-"}:
                continue
            try:
                return int(fields[5])
            except ValueError:
                return None
    return None


def _measured_complexity_violations() -> Measurement:
    """Count of functions lizard flags at the template's thresholds."""
    if not _app_targets(include_tests=True):
        return Measurement(unavailable="no app sources")
    # `-i` high enough that lizard exits 0 and still prints the summary row.
    res = subprocess.run(
        _complexity_argv(REPORT_ONLY_LIMIT), capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        return Measurement(error=f"lizard failed to run (exit {res.returncode})")
    count = _lizard_warning_count(res.stdout)
    if count is None:
        return Measurement(error="lizard printed no summary row to count warnings from")
    return Measurement(value=count)


def _measured_crap_violations() -> Measurement:
    """Count of functions above the default CRAP threshold."""
    if not _has_tests():
        return Measurement(unavailable=f"no {TEST_DIR}/test*.py files")
    measurement = _crap_measure(CRAP_MAX_DEFAULT)
    if measurement.problem:
        # `code == 0` is a benign skip (no app sources); anything else is a tool failure.
        if measurement.code:
            return Measurement(error=measurement.problem)
        return Measurement(unavailable=measurement.problem)
    return Measurement(value=len(measurement.offenders))


def _run_deadcode() -> tuple[Measurement, list[str]]:
    """Run vulture once, returning its finding count and the finding lines.

    vulture prints exactly one line per finding and exits 1 when it found any, so
    the count is `len(stdout lines)`. A non-zero exit with no stdout is vulture
    itself failing (missing tool, bad path), not a clean-but-noisy tree.
    """
    if not _app_targets():
        return Measurement(unavailable="no app sources"), []
    res = subprocess.run(_deadcode_argv(), capture_output=True, text=True, check=False)
    findings = [line for line in res.stdout.splitlines() if line.strip()]
    if res.returncode != 0 and not findings:
        detail = res.stderr.strip().splitlines()
        reason = f": {detail[-1]}" if detail else ""
        return Measurement(error=f"vulture failed to run (exit {res.returncode}){reason}"), []
    return Measurement(value=len(findings)), findings


def _measured_deadcode_findings() -> Measurement:
    """Count of vulture findings over the app sources."""
    return _run_deadcode()[0]


RATCHETED_KEYS = (
    "coverage.min",
    "complexity.max_violations",
    "crap.max_violations",
    "deadcode.max_findings",
)


def _measure_ratcheted() -> dict[str, Measurement]:
    """Measure every ratcheted metric, stopping at the first one that failed.

    Sequential and short-circuiting on purpose: a broken tool usually breaks the
    metrics after it too (CRAP re-runs the coverage suite), so the first failure is
    the one worth reporting, and the later ones would only be noise.
    """
    measured: dict[str, Measurement] = {}
    for key, measure in (
        ("coverage.min", _measured_coverage),
        ("complexity.max_violations", _measured_complexity_violations),
        ("crap.max_violations", _measured_crap_violations),
        ("deadcode.max_findings", _measured_deadcode_findings),
    ):
        measured[key] = measure()
        if measured[key].error:
            break
    return measured


def _write_baseline(results: dict[str, list[list[str]]]) -> dict[str, int]:
    """Merge measured floors over the existing baseline; unknown keys are preserved.

    Every key the harness measures is rewritten (so a metric that improved
    ratchets down); every key it does not recognise is carried through untouched.

    A metric that does not apply here has its key *removed*, never carried forward:
    the shipped template's own numbers (`coverage.min 100`) must not survive into an
    adopting repo's first baseline. A metric that could not be measured aborts the
    whole write — see `Measurement`.
    """
    baseline = _read_baseline() or {}
    counts = {f"{SUPPRESSION_BASELINE_PREFIX}{kind}": 0 for kind, _ in _SUPPRESSION_PATTERNS}
    counts.update(_suppression_counts(results))
    baseline.update(counts)

    measurements = _measure_ratcheted()
    broken = [(key, m.error) for key, m in measurements.items() if m.error]
    if broken:
        print(f"  {RED}✗{RESET} {BASELINE_FILE} not written — could not measure:")
        for key, reason in broken:
            print(f"    {key}: {reason}")
        print("  ↳ fix: make the measurement pass, then rerun `suppressions --update-baseline`")
        sys.exit(1)

    for key, measured in measurements.items():
        if measured.value is None:
            if baseline.pop(key, None) is not None:
                warn(f"{key}: dropped — {measured.unavailable}")
            continue
        baseline[key] = measured.value

    lines = [f"{key} {baseline[key]}" for key in sorted(baseline)]
    Path(BASELINE_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return baseline


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
        baseline = _write_baseline(results)
        total = sum(len(v) for v in results.values())
        recorded = ", ".join(
            [f"suppressions {total}"]
            + [f"{key} {baseline[key]}" for key in RATCHETED_KEYS if key in baseline]
        )
        print(f"  {GREEN}✓{RESET} {BASELINE_FILE}: {recorded}")
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


def _restage_fixed_files(files: list[str]) -> None:
    """Re-stage files after fix/format rewrote the working tree.

    `cmd_fix`/`cmd_format` edit files on disk, but the commit records the INDEX —
    without this, the commit ships the unfixed blob. Known caveat: if a file is only
    partially staged, `git add` also stages its unstaged hunks (the same trade-off
    lint-staged makes).
    """
    existing = [f for f in files if Path(f).exists()]
    if not existing:
        return
    subprocess.run(["git", "add", "--", *existing], check=False)
    print(f"  {GREEN}✓{RESET} Re-staged {len(existing)} fixed file(s)")


def _changed_py_files() -> list[str]:
    """Return project .py files with uncommitted changes, relative to this template.

    `git status --porcelain` returns paths relative to the repo root regardless of
    cwd, so a template living in a subdirectory (this repo's own monorepo-style
    layout) needs its repo-root prefix stripped before matching `_is_project_python_file`.
    The `-- .` pathspec also scopes results to this template's subtree, matching the
    existing arch-guard git helpers.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "."],
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


# ── Line-level diff scoping ───────────────────────────────────────
# File-level scoping is not enough on its own: touch one line of a legacy module
# and every pre-existing violation in it lands on your change. So each scoped file
# also carries the set of post-image line ranges this change wrote, parsed straight
# out of `git diff --unified=0`'s `@@` headers, and a finding is only yours if it
# intersects one of them.
#
# `LineRanges` has three meaningful states, and they are not interchangeable:
#   None — no base version of this file (untracked, or added on this branch). Every
#          line is new, so the whole file is in scope.
#   []   — the file changed only by deletion. Nothing on the post-image is yours;
#          only WHOLE_FILE_CODES can still fire.
#   list — merged, inclusive, 1-based ranges on the post-image.

LineRanges = list[tuple[int, int]] | None

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _parse_hunks(diff_lines: Iterable[str]) -> list[tuple[int, int]]:
    """Parse `@@` headers into post-image (start, end) inclusive ranges.

    A pure-deletion hunk (`+N,0`) writes no post-image line and contributes nothing.
    """
    ranges: list[tuple[int, int]] = []
    for line in diff_lines:
        match = _HUNK_RE.match(line)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if count == 0:
            continue
        ranges.append((start, start + count - 1))
    return ranges


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and coalesce ranges, joining ones that touch or abut."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _in_ranges(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    """True when [start, end] overlaps any range."""
    return any(low <= end and start <= high for low, high in ranges)


def _resolved_base_ref() -> str | None:
    """The diff base, but only when git can actually resolve it."""
    base = _base_ref()
    if base is None or not _git_lines(["rev-parse", "--verify", base]):
        return None
    return base


def _files_without_a_base_version() -> set[str]:
    """Paths whose every line is new: untracked, or added since the base ref."""
    paths = _git_lines(["ls-files", "--others", "--exclude-standard", "--", "."])
    paths += _git_lines(["diff", "--cached", "--diff-filter=A", "--name-only", "--", "."])
    base = _resolved_base_ref()
    if base is not None:
        paths += _git_lines([
            "diff",
            "--diff-filter=A",
            "--name-only",
            f"{base}...HEAD",
            "--",
            ".",
        ])
    prefix = _git_prefix()
    return {_normalize_changed_path(path, prefix) for path in paths}


def _changed_line_ranges(path: str, new_files: set[str]) -> LineRanges:
    """Post-image line ranges this change wrote in `path`. See `LineRanges`."""
    if path in new_files:
        return None
    hunks: list[tuple[int, int]] = []
    base = _resolved_base_ref()
    if base is not None:
        hunks += _parse_hunks(_git_lines(["diff", "--unified=0", f"{base}...HEAD", "--", path]))
    hunks += _parse_hunks(_git_lines(["diff", "--unified=0", "HEAD", "--", path]))
    return _merge_ranges(hunks)


def _scoped_ranges(files: Iterable[str]) -> dict[str, LineRanges]:
    """Map each scoped file to the lines this change wrote in it."""
    new_files = _files_without_a_base_version()
    return {path: _changed_line_ranges(path, new_files) for path in files}


def _line_scoped() -> bool:
    """False when the caller asked for whole files (`--all`, `--whole-file`)."""
    return not (ALL_FILES or WHOLE_FILE)


def _norm_path(path: str) -> str:
    """Make a tool-emitted absolute path relative to cwd, so it matches our scope keys."""
    try:
        return os.path.relpath(path, Path.cwd())
    except (ValueError, OSError):
        return path


def _scoped_label(label: str, kept: int, total: int) -> str:
    """Append the kept/total count only when line scoping actually dropped something."""
    return label if kept == total else f"{label} ({kept}/{total} on changed lines)"


# ── Line-scoped ruff ──────────────────────────────────────────────


def _ruff_json_argv(files: Iterable[str]) -> list[str]:
    """`ruff check` in JSON mode. `--no-fix` so the JSON reports truth, not intent;
    `--force-exclude` so `per-file-ignores`/`exclude` still apply to explicit paths."""
    return [
        *_tool("ruff", read_only=True),
        "check",
        "--output-format=json",
        "--no-fix",
        "--force-exclude",
        *files,
    ]


def _ruff_findings(cmd: list[str]) -> tuple[list[dict[str, Any]] | None, str]:
    """Run ruff in JSON mode. Returns (findings, "") or (None, why-it-failed).

    Never exits: this runs inside a worker thread of the parallel gate batch.
    """
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    text = result.stdout.strip()
    if not text:
        return None, f"ruff emitted no JSON (exit {result.returncode})\n{result.stderr}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"ruff JSON did not parse (exit {result.returncode}): {exc}\n{result.stderr}"
    if not isinstance(parsed, list):
        return None, f"ruff JSON was not a list of findings (exit {result.returncode})"
    return parsed, ""


def _filter_findings(
    findings: list[dict[str, Any]], ranges_by_file: dict[str, LineRanges]
) -> list[dict[str, Any]]:
    """Keep findings on changed lines, plus WHOLE_FILE_CODES anywhere in a scoped file."""
    kept: list[dict[str, Any]] = []
    for finding in findings:
        path = _norm_path(finding.get("filename", ""))
        if path not in ranges_by_file:
            continue
        ranges = ranges_by_file[path]
        if ranges is None or finding.get("code") in WHOLE_FILE_CODES:
            kept.append(finding)
            continue
        if not ranges:
            continue
        row = (finding.get("location") or {}).get("row", 0)
        end = (finding.get("end_location") or {}).get("row", row)
        if _in_ranges(row, end, ranges):
            kept.append(finding)
    return kept


def _render_findings(findings: Iterable[dict[str, Any]]) -> str:
    lines = []
    for finding in findings:
        location = finding.get("location") or {}
        lines.append(
            f"{_norm_path(finding.get('filename', '?'))}"
            f":{location.get('row', 0)}:{location.get('column', 0)}: "
            f"{finding.get('code') or '?'} {finding.get('message', '')}"
        )
    return "\n".join(lines) + ("\n" if lines else "")


def _scoped_lint_result(label: str, files: list[str], hint: str | None) -> GateResult:
    """Lint the scoped files, then judge only the findings on changed lines."""
    cmd = _ruff_json_argv(files)
    ranges = _scoped_ranges(files)
    findings, problem = _ruff_findings(cmd)
    if findings is None:
        return GateResult(label, cmd, 1, "", problem, hint)
    kept = _filter_findings(findings, ranges)
    return GateResult(
        _scoped_label(label, len(kept), len(findings)),
        cmd,
        1 if kept else 0,
        _render_findings(kept),
        "",
        hint,
    )


def _scoped_format_result(label: str, files: list[str], hint: str | None) -> GateResult:
    """`ruff format --check`, narrowed to changed lines with ruff's own `--range`.

    Format is the one gate that scopes exactly: ruff formats a line range natively,
    so there is no approximation here and no residue.
    """
    base = [*_tool("ruff", read_only=True), "format", "--check"]
    ranges_by_file = _scoped_ranges(files)
    failures: list[str] = []
    for path in files:
        ranges = ranges_by_file.get(path, [])
        if ranges is None:
            argvs = [[*base, path]]
        elif not ranges:
            continue
        else:
            argvs = [[*base, f"--range={start}-{end}", path] for start, end in ranges]
        for argv in argvs:
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                failures.append(result.stdout + result.stderr)
                break
    return GateResult(label, [*base, *files], 1 if failures else 0, "".join(failures), "", hint)


def _format_scoped(files: list[str], *, no_exit: bool) -> bool:
    """Rewrite only the changed line ranges, via `ruff format --range`."""
    base = [*_tool("ruff"), "format"]
    ranges_by_file = _scoped_ranges(files)
    problems: list[str] = []
    for path in files:
        ranges = ranges_by_file.get(path, [])
        if ranges is None:
            argvs = [[*base, path]]
        elif not ranges:
            continue
        else:
            # Descending, so an earlier range's reformat cannot shift a later one's
            # line numbers out from under it.
            argvs = [[*base, f"--range={start}-{end}", path] for start, end in reversed(ranges)]
        for argv in argvs:
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                problems.append(result.stdout + result.stderr)
    outcome = GateResult("Format code", base + files, 1 if problems else 0, "".join(problems), "")
    print_gate_result(outcome, no_exit=no_exit)
    return outcome.ok


def _edits_stayed_in_scope(before: str, after: str, ranges: list[tuple[int, int]]) -> bool:
    """True when every line `ruff --fix` rewrote was one this change had already written.

    Line numbers are on the *pre-fix* image, which is the same image the hunk ranges
    describe. An insertion between lines i and i+1 is attributed to line i+1 — hunk
    ranges are merged with abutting neighbours, so an insertion at the edge of your
    change still counts as yours.
    """
    if not ranges:
        return False
    old, new = before.splitlines(), after.splitlines()
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(
        None, old, new, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        if not _in_ranges(i1 + 1, max(i2, i1 + 1), ranges):
            return False
    return True


def _fix_scoped(files: list[str], *, no_exit: bool) -> bool:
    """Auto-fix, then undo any file whose fix reached beyond the changed lines.

    Stated plainly: `ruff --fix` rewrites a whole file or nothing — it has no line
    range mode — so this cannot be as surgical as the lint filter. It is all-or-nothing
    per file, biased hard toward *not touching* code the change never wrote. A file
    whose fix strayed is restored byte-for-byte and its in-scope findings are reported
    for a human to fix by hand, which is the failure this trades for.
    """
    before = {
        path: Path(path).read_text(encoding="utf-8") for path in files if Path(path).is_file()
    }
    ranges_by_file = _scoped_ranges(files)
    subprocess.run(
        [*_tool("ruff"), "check", "--fix", "--force-exclude", *files],
        capture_output=True,
        text=True,
        check=False,
    )

    reverted: list[str] = []
    for path, original in before.items():
        ranges = ranges_by_file.get(path, [])
        if ranges is None:
            continue  # no base version — the whole file is this change's to rewrite
        current = Path(path).read_text(encoding="utf-8")
        if current == original or _edits_stayed_in_scope(original, current, ranges):
            continue
        Path(path).write_text(original, encoding="utf-8")
        reverted.append(path)

    if reverted:
        warn(
            f"Fix: reverted {len(reverted)} file(s) whose fix reached "
            f"untouched lines: {', '.join(reverted)}"
        )
    # Re-scope after the rewrite: an applied fix moves the lines the hunks describe.
    result = _scoped_lint_result("Fix lint errors", files, "run `harness fix`")
    print_gate_result(result, no_exit=no_exit)
    return result.ok


# ── Line-scoped basedpyright ──────────────────────────────────────


def _diagnostic_row_span(diagnostic: dict[str, Any]) -> tuple[int, int]:
    """basedpyright reports 0-based lines; the hunk ranges are 1-based."""
    span = diagnostic.get("range") or {}
    start = (span.get("start") or {}).get("line", 0) + 1
    end = (span.get("end") or {}).get("line", start - 1) + 1
    return start, max(end, start)


def _filter_diagnostics(
    diagnostics: list[dict[str, Any]], ranges_by_file: dict[str, LineRanges]
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        path = _norm_path(diagnostic.get("file", ""))
        if path not in ranges_by_file:
            continue
        ranges = ranges_by_file[path]
        if ranges is None:
            kept.append(diagnostic)
            continue
        if ranges and _in_ranges(*_diagnostic_row_span(diagnostic), ranges):
            kept.append(diagnostic)
    return kept


def _render_diagnostics(diagnostics: Iterable[dict[str, Any]]) -> str:
    lines = []
    for diagnostic in diagnostics:
        row, _end = _diagnostic_row_span(diagnostic)
        rule = diagnostic.get("rule")
        lines.append(
            f"{_norm_path(diagnostic.get('file', '?'))}:{row}: "
            f"{diagnostic.get('severity', 'error')}: {diagnostic.get('message', '')}"
            f"{f' ({rule})' if rule else ''}"
        )
    return "\n".join(lines) + ("\n" if lines else "")


def _scoped_typecheck_result(label: str, files: list[str], hint: str | None) -> GateResult:
    """Type-check the scoped files, then judge only the diagnostics on changed lines."""
    cmd = [*_tool("basedpyright", read_only=True), "--outputjson", *files]
    ranges = _scoped_ranges(files)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        # basedpyright failed before it could report; surface its raw output verbatim.
        return GateResult(label, cmd, result.returncode or 1, result.stdout, result.stderr, hint)
    diagnostics = payload.get("generalDiagnostics") or []
    kept = _filter_diagnostics(diagnostics, ranges)
    errors = [d for d in kept if d.get("severity") == "error"]
    return GateResult(
        _scoped_label(label, len(kept), len(diagnostics)),
        cmd,
        1 if errors else 0,
        _render_diagnostics(kept),
        "",
        hint,
    )


# ── Scoping ───────────────────────────────────────────────────────


def _present(gates: Iterable[Gate | None]) -> list[Gate]:
    """Drop the gates that resolved to "nothing to do"."""
    return [gate for gate in gates if gate is not None]


def _scoped_py_files(*, staged: bool = False) -> list[str]:
    """Project .py files this change touches, deduplicated and order-stable.

    Reuses `_changed_paths` — the same plumbing the arch-config and gherkin
    guards walk — so every scoped gate sees exactly one definition of "changed".
    Non-existent paths are dropped: the base diff can name a file the working
    tree has since deleted, and handing that to ruff is a hard error.
    """
    seen: dict[str, None] = {}
    for path in _changed_paths(staged=staged):
        if _is_project_python_file(path) and Path(path).is_file():
            seen.setdefault(path, None)
    return list(seen)


def _resolve_targets(
    files: list[str] | None,
    action: str,
    *,
    whole_tree: list[str] | None = None,
) -> list[str] | None:
    """Targets for a scoped gate; None means "nothing to do, skip this gate".

    An explicit `files` list (pre-commit's staged set) is honoured verbatim.
    Otherwise `--all` widens to the whole tree and the default is the git-derived
    changed set.
    """
    if files is not None:
        return files or None
    if ALL_FILES:
        return whole_tree if whole_tree is not None else ["."]
    targets = _scoped_py_files()
    if not targets:
        # Two very different reasons produce the same empty scope. Say which one.
        unresolved = _unresolved_requested_base()
        if unresolved is not None:
            warn(f"{action}: skipped — diff base {unresolved!r} does not resolve, nothing checked")
        else:
            warn(f"{action}: no changed Python files (use --all for the whole tree); skipped")
        return None
    return targets


# ── Commands ──────────────────────────────────────────────────────


def cmd_fix(files: list[str] | None = None, *, no_exit: bool = False) -> bool:
    target = _resolve_targets(files, "Fix")
    if target is None:
        return True
    if not _line_scoped():
        return run("Fix lint errors", [*_tool("ruff"), "check", "--fix", *target], no_exit=no_exit)
    return _fix_scoped(target, no_exit=no_exit)


def cmd_format(files: list[str] | None = None, *, no_exit: bool = False) -> bool:
    target = _resolve_targets(files, "Format")
    if target is None:
        return True
    if not _line_scoped():
        return run("Format code", [*_tool("ruff"), "format", *target], no_exit=no_exit)
    return _format_scoped(target, no_exit=no_exit)


def _lint_gate(files: list[str] | None = None) -> Gate | None:
    target = _resolve_targets(files, "Lint")
    if target is None:
        return None
    if not _line_scoped():
        return Gate(
            "Lint check", [*_tool("ruff", read_only=True), "check", *target], "run `harness fix`"
        )
    return Gate(
        "Lint check",
        _ruff_json_argv(target),
        "run `harness fix`",
        runner=lambda: _scoped_lint_result("Lint check", target, "run `harness fix`"),
    )


def cmd_lint(files: list[str] | None = None) -> None:
    for gate in _present([_lint_gate(files)]):
        run(gate.description, gate.cmd, hint=gate.hint, runner=gate.runner)


def _format_check_gate(files: list[str] | None = None) -> Gate | None:
    target = _resolve_targets(files, "Format check")
    if target is None:
        return None
    hint = "run `harness format`"
    if not _line_scoped():
        return Gate(
            "Format check", [*_tool("ruff", read_only=True), "format", "--check", *target], hint
        )
    return Gate(
        "Format check",
        [*_tool("ruff", read_only=True), "format", "--check", *target],
        hint,
        runner=lambda: _scoped_format_result("Format check", target, hint),
    )


TYPECHECK_HINT = "fix the type; ignores are counted by the suppression ratchet"


def _typecheck_gate(files: list[str] | None = None) -> Gate | None:
    target = _resolve_targets(files, "Typecheck", whole_tree=_quality_targets())
    if target is None:
        return None
    if not _line_scoped():
        cmd = [*_tool("basedpyright", read_only=True), *target]
        return Gate("Type check", cmd, TYPECHECK_HINT)
    return Gate(
        "Type check",
        [*_tool("basedpyright", read_only=True), "--outputjson", *target],
        TYPECHECK_HINT,
        runner=lambda: _scoped_typecheck_result("Type check", target, TYPECHECK_HINT),
    )


def cmd_typecheck(files: list[str] | None = None, *, no_exit: bool = False) -> bool:
    gate = _typecheck_gate(files)
    if gate is None:
        return True
    return run(gate.description, gate.cmd, no_exit=no_exit, hint=gate.hint, runner=gate.runner)


def cmd_test(*, no_exit: bool = False) -> bool:
    if _has_tests():
        return run("Tests", [*_python(), *TEST_COMMAND], no_exit=no_exit)

    files = [str(path) for path in _iter_python_files(_quality_targets(include_tests=False))]
    if not files:
        warn("Syntax check: no Python files found; skipped")
        return True
    return run("Syntax check", [*_python(), "-m", "py_compile", *files], no_exit=no_exit)


def cmd_coverage() -> None:
    """Run tests under coverage with threshold + uncovered listing."""
    if not _has_tests():
        warn(f"Coverage: no {TEST_DIR}/test*.py files; skipped")
        return

    min_pct = _coverage_min_default()
    # read_only throughout: `ci` calls this, and `ci` must not create or sync `.venv`.
    run("Coverage (run)", [*_tool("coverage", read_only=True), "run", *TEST_COMMAND])
    run(
        f"Coverage >= {min_pct}%",
        [
            *_tool("coverage", read_only=True),
            "report",
            "--show-missing",
            f"--fail-under={min_pct}",
        ],
    )


def _feature_files_dir() -> Path:
    return Path(TEST_DIR) / "features"


def _has_feature_files() -> bool:
    """Return true when the template has at least one `.feature` acceptance scenario."""
    features_dir = _feature_files_dir()
    return features_dir.exists() and any(features_dir.rglob("*.feature"))


def _acceptance_gates_or_warn() -> list[Gate]:
    """Build the acceptance gate, or warn + return [] when there are no scenarios."""
    features_dir = _feature_files_dir()
    if not _has_feature_files():
        warn(f"Acceptance: no .feature files in {features_dir}/ (add one to enable this gate)")
        return []
    return [
        Gate(
            "Acceptance (behave)",
            [*_tool("behave", read_only=True), str(features_dir), "--no-color"],
            "align implementation with the `.feature`, not vice versa",
        )
    ]


def cmd_acceptance() -> None:
    """Run behave scenarios. Empty features dir warns + exits 0."""
    for gate in _acceptance_gates_or_warn():
        run(gate.description, gate.cmd)


def cmd_mutation() -> None:
    """Mutation testing. Not configured by default — warn and skip."""
    warn("Mutation testing not configured — add mutmut to your dev dependencies to enable")


def _arch_gates_or_warn() -> list[Gate]:
    """Build the import-linter gate, or warn + return [] when no .importlinter exists."""
    if not Path(".importlinter").exists():
        warn("Arch: no .importlinter — skipped")
        return []
    return [Gate("Arch (import-linter)", _tool("lint-imports", read_only=True))]


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


_FALLBACK_BASE_CANDIDATES = ("origin/HEAD", "origin/main", "main")


def _resolve_fallback_base() -> str | None:
    """Return the first candidate base ref that resolves, or None if none do."""
    for candidate in _FALLBACK_BASE_CANDIDATES:
        if _git_lines(["rev-parse", "--verify", candidate]):
            return candidate
    return None


def _requested_base_ref() -> str | None:
    """The base ref someone explicitly asked for, or None if nobody did.

    `--base=<ref>` wins, then HARNESS_ARCH_BASE, then GITHUB_BASE_REF — which is
    only set for pull_request events.
    """
    if BASE_OVERRIDE:
        return BASE_OVERRIDE
    if base := os.environ.get("HARNESS_ARCH_BASE"):
        return base
    if github_base := os.environ.get("GITHUB_BASE_REF"):
        return f"origin/{github_base}"
    return None


def _base_ref() -> str | None:
    """The single ref every changed-path consumer diffs against, or None.

    An explicit request wins; a direct push to main (e.g. `push: branches: [main]`)
    sets neither env var and falls through to `_resolve_fallback_base`. Without that
    fallback an arch-config change arriving by direct push would pass ci clean. On
    main itself the merge-base diff against these is empty, so the fallback adds no
    false positives.
    """
    return _requested_base_ref() or _resolve_fallback_base()


def _unresolved_requested_base() -> str | None:
    """The explicitly requested base ref, when git cannot resolve it; else None.

    The distinction this draws is the whole point: a *fallback* base that does not
    resolve is the documented shallow-checkout case (no `origin/main` to diff
    against) and must stay a silent, green skip, or the harness cannot be adopted
    into one. A base someone *asked for* and that does not exist is a typo, and
    scoping to zero files because of it is a green gate that verified nothing.
    """
    base = _requested_base_ref()
    if base is None or _git_lines(["rev-parse", "--verify", base]):
        return None
    return base


def _check_base_ref(*, warn_only: bool = False, no_exit: bool = False) -> bool:
    """Report a requested-but-unresolvable diff base. Silent when there is nothing wrong."""
    base = _unresolved_requested_base()
    if base is None:
        return True
    detail = f"Diff base {base!r} does not resolve — scoped gates saw zero files"
    fix = "fix: fetch it (`fetch-depth: 0`) or pass a base ref that exists"
    if warn_only:
        print(f"  {GREEN}⚠{RESET} {detail}")
        print(f"  ↳ {fix}")
        return True
    print(f"  {RED}✗{RESET} {detail}")
    print(f"  ↳ {fix}")
    if not no_exit:
        sys.exit(1)
    return False


def _changed_paths_from_base() -> list[str]:
    base = _base_ref()
    if base is None or not _git_lines(["rev-parse", "--verify", base]):
        return []
    return _git_lines(["diff", "--name-only", "--diff-filter=d", f"{base}...HEAD", "--", "."])


def _changed_paths_from_pre_push_stdin() -> list[str]:
    if sys.stdin.isatty():
        return []
    text = sys.stdin.read()
    if not text.strip():
        return []
    zero = "0" * 40
    paths: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        _, local_sha, _, remote_sha = parts[:4]
        if local_sha == zero:
            continue
        if remote_sha == zero:
            paths.extend(
                _git_lines([
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    local_sha,
                    "--",
                    ".",
                ])
            )
        else:
            paths.extend(
                _git_lines([
                    "diff",
                    "--name-only",
                    "--diff-filter=d",
                    remote_sha,
                    local_sha,
                    "--",
                    ".",
                ])
            )
    return paths


def _changed_paths(*, staged: bool = False, include_pre_push_stdin: bool = False) -> list[str]:
    """Return normalized changed paths in this template, relative to its root.

    Shared git-diff plumbing for every changed-path guard (arch config, gherkin-first)
    so they walk the working tree + index + untracked files + base-branch diff (and
    optionally the pre-push stdin refs) exactly once, the same way.
    """
    paths: list[str] = []
    if staged:
        paths.extend(_git_lines(["diff", "--cached", "--name-only", "--diff-filter=d", "--", "."]))
    else:
        paths.extend(_git_lines(["diff", "--name-only", "--diff-filter=d", "--", "."]))
        paths.extend(_git_lines(["diff", "--cached", "--name-only", "--diff-filter=d", "--", "."]))
        paths.extend(_git_lines(["ls-files", "--others", "--exclude-standard", "--", "."]))
        paths.extend(_changed_paths_from_base())
    if include_pre_push_stdin:
        paths.extend(_changed_paths_from_pre_push_stdin())

    prefix = _git_prefix()
    return [_normalize_changed_path(path, prefix) for path in paths]


def _changed_arch_configs(
    *, staged: bool = False, include_pre_push_stdin: bool = False
) -> list[str]:
    protected = set(ARCH_CONFIGS)
    changed = {
        path
        for path in _changed_paths(staged=staged, include_pre_push_stdin=include_pre_push_stdin)
        if path in protected
    }
    return sorted(changed)


def _check_arch_config_guard(
    *,
    warn_only: bool = False,
    staged: bool = False,
    include_pre_push_stdin: bool = False,
) -> bool:
    changed = _changed_arch_configs(staged=staged, include_pre_push_stdin=include_pre_push_stdin)
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


def _is_production_source_path(path: str) -> bool:
    """Return true for a changed path under APP_SOURCES, excluding the test dir.

    Deliberately scoped to `src/` — not the runner itself (`harness.py`, only in
    QUALITY_SOURCES) and not `tests/` — so editing the harness or adding tests never
    trips the guard, only a real production-source change does.
    """
    if path == TEST_DIR or path.startswith(f"{TEST_DIR}/"):
        return False
    return any(path == src or path.startswith(f"{src}/") for src in APP_SOURCES)


def _gherkin_guard_decision(changed_paths: Iterable[str]) -> bool:
    """Pure trigger logic: true when the guard should fire.

    Fires when at least one changed path is production source and none is a
    `.feature` file. Kept separate from the git/env plumbing in
    `_check_gherkin_guard` so it is unit-testable without shelling out to git.
    """
    paths = list(changed_paths)
    has_production_change = any(_is_production_source_path(p) for p in paths)
    has_feature_change = any(p.endswith(".feature") for p in paths)
    return has_production_change and not has_feature_change


def _check_gherkin_guard(
    *,
    warn_only: bool = False,
    staged: bool = False,
    include_pre_push_stdin: bool = False,
) -> bool:
    if not _has_feature_files():
        # No acceptance suite adopted yet — retrofitting the harness into a repo
        # with no `.feature` files must never block, so this passes silently (no
        # output at all) — a second "no .feature files" line here would just
        # duplicate the one `_acceptance_gates_or_warn` already prints.
        return True

    changed = _changed_paths(staged=staged, include_pre_push_stdin=include_pre_push_stdin)
    if not _gherkin_guard_decision(changed):
        print(f"  {GREEN}✓{RESET} Gherkin-first")
        return True

    joined = ", ".join(p for p in changed if _is_production_source_path(p))
    fix_hint = (
        f"add a scenario under {TEST_DIR}/features/, or set {GHERKIN_ALLOW_ENV}=1 after review"
    )
    if os.environ.get(GHERKIN_ALLOW_ENV) == "1":
        print(f"  {GREEN}⚠{RESET} Gherkin-first override: {joined}")
        return True
    if warn_only:
        print(
            f"  {GREEN}⚠{RESET} Gherkin-first: production source changed "
            f"with no .feature: {joined}"
        )
        print(f"  ↳ fix: {fix_hint}")
        return True
    print(f"  {RED}✗{RESET} Gherkin-first: production source changed with no .feature: {joined}")
    print(f"  ↳ fix: {fix_hint}")
    return False


def cmd_gherkin_guard() -> None:
    ok = _check_gherkin_guard(warn_only="--warn" in sys.argv, staged="--staged" in sys.argv)
    if not ok:
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


@dataclasses.dataclass(frozen=True)
class CrapMeasurement:
    """The outcome of scoring CRAP, or why no scoring happened."""

    offenders: list[tuple[float, int, float, str]]
    problem: str = ""
    detail: str = ""
    code: int = 0
    """0 with a `problem` set means a benign skip; nonzero means a tool failed."""


def _crap_measure(max_crap: float) -> CrapMeasurement:
    """Score every function's CRAP against `max_crap`, refreshing coverage if stale."""
    cov_data = Path(".coverage")
    if not _artifact_is_fresh(cov_data, _quality_targets()):
        cmd_coverage()

    # Emit coverage XML quietly; cmd_coverage must have populated .coverage.
    subprocess.run(
        [*_tool("coverage", read_only=True), "xml", "-o", "coverage.xml", "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    cov_file = Path("coverage.xml")
    if not cov_file.exists():
        return CrapMeasurement([], "coverage XML not found after coverage run")

    cov_map = _parse_coverage_xml(cov_file)
    targets = _app_targets()
    if not targets:
        return CrapMeasurement([], "no app sources; skipped")

    # `-i` high: here lizard is a measuring tape, not a gate. Left at its default
    # (`-i 0`, CCN 15) it exits 1 on any complex function, and CRAP would report
    # "lizard failed to run" for exactly the repos that need scoring most.
    lizard_res = subprocess.run(
        [*_tool(LIZARD, read_only=True), *targets, "-i", "1000000"],
        capture_output=True,
        text=True,
        check=False,
    )
    if lizard_res.returncode != 0:
        # Lizard could not run (uvx missing, network failure, lizard crash).
        # Reporting "all functions below max" would be a silent false-pass.
        return CrapMeasurement(
            [],
            f"lizard failed to run (exit {lizard_res.returncode})",
            lizard_res.stderr.strip(),
            lizard_res.returncode,
        )
    # Function name capture allows the empty string so we can detect (and skip)
    # anonymous functions explicitly rather than silently dropping them.
    line_re = re.compile(r"^\s*(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+([^@\s]*)@(\d+)-(\d+)@(.+)$")
    # Keyed by location: lizard lists a flagged function twice — once in the
    # per-file table and once in its warnings table — and counting it twice would
    # inflate the ratchet count.
    scored: dict[str, tuple[float, int, float, str]] = {}
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
        loc = f"{func}@{start}-{end}@{path}"
        if crap > max_crap:
            scored[loc] = (crap, ccn, cov, loc)
    return CrapMeasurement(sorted(scored.values(), reverse=True))


def cmd_crap() -> None:
    """CRAP = ccn^2 * (1-cov)^3 + ccn per function. Advisory — lizard + coverage XML."""
    if not _has_tests():
        warn("CRAP: no tests; skipped")
        return

    max_crap = float(
        next(
            (a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--max=")),
            str(CRAP_MAX_DEFAULT),
        )
    )
    enforce = "--enforce" in sys.argv
    measurement = _crap_measure(max_crap)
    if measurement.problem:
        if not measurement.code:
            warn(f"CRAP: {measurement.problem}")
            return
        suffix = "" if enforce else " (advisory)"
        print(f"  {_advisory_glyph(enforce=enforce)} CRAP: {measurement.problem}{suffix}")
        if measurement.detail:
            print(measurement.detail)
        if enforce:
            sys.exit(measurement.code or 1)
        return

    offenders = measurement.offenders
    if not offenders:
        print(f"  {GREEN}✓{RESET} CRAP: all functions below {max_crap}")
        return

    # The baseline is a count floor: a repo adopting the harness starts wherever
    # it already is, and that number may only come down.
    # No floor recorded means no tolerance — safe here, unlike complexity/deadcode,
    # because CRAP is advisory: with no baseline it warns and still exits 0.
    floor = _baseline_floor("crap.max_violations") or 0
    if len(offenders) <= floor:
        print(
            f"  {GREEN}✓{RESET} CRAP: {len(offenders)} function(s) exceed "
            f"{max_crap} (baseline {floor})"
        )
        return

    mode_suffix = "" if enforce else " (advisory)"
    glyph = _advisory_glyph(enforce=enforce)
    print(
        f"  {glyph} CRAP: {len(offenders)} function(s) exceed "
        f"{max_crap} (baseline {floor}){mode_suffix}"
    )
    for crap, ccn, cov, loc in offenders[:20]:
        print(f"    CRAP={crap:6.1f}  CCN={ccn:3d}  cov={cov * 100:5.1f}%  {loc}")
    if enforce:
        sys.exit(1)


def _lockfile_gate() -> Gate | None:
    """The `uv lock --check` gate, or None when this repo has no uv.lock.

    A pip + requirements.txt repo has nothing to check, and `uv lock --check`
    would fail there for the wrong reason — absence of a lockfile is not drift.
    """
    if not Path("uv.lock").exists():
        warn("Lockfile sync: no uv.lock; skipped")
        return None
    return Gate(
        "Lockfile sync",
        ["uv", "lock", "--check"],
        "run `uv lock` to update uv.lock, then commit it",
    )


def _audit_gate_or_warn() -> Gate | None:
    """pip-audit over the project's *installed* environment, when there is one.

    Gated on `.venv` already existing, for exactly the reason `_tool` tier 1 is: `uv
    run` creates a missing `.venv` on its way to spawning the tool, and `ci`/`pre-push`
    must not mutate anything. `--no-sync` alone does not save it — measured: with
    `.venv` removed, `uv run --no-sync --with pip-audit pip-audit` still prints
    "Creating virtual environment at: .venv". So the presence check is the load-bearing
    half and `--no-sync` is the second belt, stopping the implicit install `uv run`
    would otherwise do into an environment that *does* exist.

    The cost, stated plainly: pip-audit then reports on an environment the harness
    deliberately did not sync, so a dependency bumped in `pyproject.toml` but never
    installed goes unaudited. That is the right trade — CI runs `uv sync` before
    `harness ci`, and a dev machine has a real `.venv` — and it beats both
    alternatives, since syncing breaks the read-only contract outright and dropping the
    audit from `ci` removes one of the only three things `ci` adds over `check`.
    """
    if not Path(".venv").exists():
        print(f"  {GREEN}⚠{RESET} Dep audit: skipped (no .venv — run `uv sync` first)")
        return None
    return Gate(
        "Dep audit",
        ["uv", "run", "--no-sync", "--with", "pip-audit", "pip-audit"],
        "bump the vulnerable dependency or escalate",
    )


def cmd_audit() -> None:
    gate = _audit_gate_or_warn()
    if gate is not None:
        run(gate.description, gate.cmd)


def _complexity_argv(max_violations: int) -> list[str]:
    """lizard argv at this template's thresholds, tolerating `max_violations` warnings.

    lizard's own `-i N` is the count ratchet: it exits 0 while the number of
    flagged functions stays at or below N, so lizard does the counting.
    """
    return [
        *_tool(LIZARD, read_only=True),
        *_app_targets(include_tests=True),
        "-C",
        str(COMPLEXITY_MAX_CCN),
        "-a",
        str(COMPLEXITY_MAX_ARGS),
        "-L",
        "100",
        "-i",
        str(max_violations),
    ]


def _complexity_gate() -> Gate:
    """The lizard gate at the committed floor, or report-only when there is none.

    With no floor recorded, `-i 0` would demand a legacy tree already be perfect —
    exactly the day-one red that stops the harness being adopted. Measure instead.
    """
    floor = _baseline_floor("complexity.max_violations")
    if floor is None:
        return Gate(
            f"Complexity (lizard, report-only: no {BASELINE_FILE} floor)",
            _complexity_argv(REPORT_ONLY_LIMIT),
            "run `harness suppressions --update-baseline` to record a floor",
        )
    return Gate(
        "Complexity (lizard)" if not floor else f"Complexity (lizard, baseline {floor})",
        _complexity_argv(floor),
        f"extract helpers or flatten branches until CCN <= {COMPLEXITY_MAX_CCN}; "
        "do not raise the threshold",
    )


def cmd_complexity() -> None:
    gate = _complexity_gate()
    run(gate.description, gate.cmd)


def _deadcode_argv() -> list[str]:
    """vulture argv over the app sources only.

    Never `tests/` — so code referenced solely by a test (a dead helper that still
    has a test) is reported, not masked. Confidence 60 is vulture's floor for unused
    functions/methods/classes. List legitimate dynamic references
    (decorator-registered handlers, getattr dispatch) in `vulture_allowlist.py`.
    """
    return [
        *_tool(VULTURE, read_only=True),
        *_app_targets(),
        *_existing([VULTURE_ALLOWLIST]),
        "--min-confidence",
        VULTURE_MIN_CONFIDENCE,
    ]


DEADCODE_LABEL = "Dead code (vulture)"
DEADCODE_HINT = "delete unused code, or allowlist genuine dynamic refs in {}"


def _check_deadcode(*, no_exit: bool = False) -> bool:
    """Count-ratcheted dead-code gate: findings may never exceed the committed floor.

    Count-based, so it sits outside the `Gate` abstraction (which is exit-code only)
    alongside CRAP and the suppression ratchet — vulture has no `-i N` the way lizard
    does. Without a recorded floor this is report-only: a legacy tree hands vulture
    four figures of findings, and hard-failing on that is the day-one red that gets
    the harness deleted instead of adopted.
    """
    measured, lines = _run_deadcode()
    if measured.error:
        print(f"  {RED}✗{RESET} {DEADCODE_LABEL}: {measured.error}")
        print(f"  ↳ fix: {DEADCODE_HINT.format(VULTURE_ALLOWLIST)}")
        if not no_exit:
            sys.exit(1)
        return False
    if measured.value is None:
        warn(f"{DEADCODE_LABEL}: {measured.unavailable}; skipped")
        return True

    findings = measured.value
    floor = _baseline_floor("deadcode.max_findings")
    if floor is None:
        warn(f"{DEADCODE_LABEL}: {findings} finding(s), report-only (no {BASELINE_FILE} floor)")
        return True
    if findings <= floor:
        suffix = " — run `harness suppressions --update-baseline` to ratchet down"
        print(
            f"  {GREEN}✓{RESET} {DEADCODE_LABEL}: {findings} "
            f"(baseline {floor}){suffix if findings < floor else ''}"
        )
        return True

    print(f"  {RED}✗{RESET} {DEADCODE_LABEL}: {findings} finding(s) > baseline {floor}")
    for line in lines[:20]:
        print(f"    {line}")
    print(f"  ↳ fix: {DEADCODE_HINT.format(VULTURE_ALLOWLIST)}")
    if not no_exit:
        sys.exit(1)
    return False


def cmd_deadcode() -> None:
    _check_deadcode()


def cmd_post_edit() -> None:
    """Format if source files have uncommitted changes."""
    files = _changed_py_files()
    if not files:
        return
    cmd_fix(files, no_exit=True)
    cmd_format(files, no_exit=True)


def cmd_stop_hook() -> None:
    """Run stop-time checks after agent edits.

    Claude Code treats a Stop-hook exit code of 2 as blocking and feeds stderr back
    to the model; any other non-zero exit is a non-blocking error the model never
    sees. Codex's wrapper already turns any non-zero exit into a block, so only the
    Claude path needs the stderr failure summary here.
    """
    print("\n=== Stop Hook Checks ===\n")
    cmd_post_edit()  # mutating — sequential, first
    _check_arch_config_guard(warn_only=True)
    _check_gherkin_guard(warn_only=True)
    all_ok, failed = run_gates_parallel([_complexity_gate()])  # read-only batch
    if not _check_deadcode(no_exit=True):  # count-ratcheted, so outside the Gate batch
        all_ok = False
        failed.append(DEADCODE_LABEL)
    if not all_ok:
        print(f"stop-hook failed: {', '.join(failed)}", file=sys.stderr)
        sys.exit(2)


# ── Stages ────────────────────────────────────────────────────────


def _check_stop_hooks_present() -> None:
    """Warn when Claude/Codex Stop hook wiring is missing."""
    for rel in (".claude/settings.json", ".codex/hooks.json"):
        text = Path(rel).read_text(encoding="utf-8") if Path(rel).exists() else ""
        if "Stop" in text and "stop-hook" in text:
            print(f"  {GREEN}✓{RESET} Stop hook wiring ({rel})")
        else:
            print(f"  {RED}⚠{RESET} Missing Stop hook wiring: {rel}")


def _first_diff_line(a: str, b: str) -> int:
    """Return 1-based line number of the first line that differs."""
    al, bl = a.splitlines(), b.splitlines()
    for i in range(min(len(al), len(bl))):
        if al[i] != bl[i]:
            return i + 1
    return min(len(al), len(bl)) + 1


def _agents_md_case_variant() -> str | None:
    """An on-disk AGENTS.md spelled in a different case, when one exists.

    On a case-insensitive filesystem (APFS, NTFS) `Path("AGENTS.md").write_bytes()`
    writes *through* to an existing `agents.md`: `Path.exists()` says yes, the copy
    silently replaces a file the harness never named, and the directory still lists
    the lowercase name afterwards. Reading the directory is the only way to see it.
    """
    try:
        names = [entry.name for entry in Path().iterdir()]
    except OSError:
        return None
    return next((n for n in names if n.lower() == "agents.md" and n != "AGENTS.md"), None)


def _sync_refusal(claude: Path, agents: Path) -> str | None:
    """Why overwriting `agents` from `claude` would destroy work, or None if it is safe.

    The rule is "the destination must be a plausibly stale copy of CLAUDE.md". Anything
    else — a differently-cased twin, a symlink, or a document too dissimilar to be a
    copy at all — is somebody's own file, and the harness does not get to delete it.
    """
    variant = _agents_md_case_variant()
    if variant is not None:
        return (
            f"{variant} exists under a different case on a case-insensitive filesystem"
            " — writing AGENTS.md would overwrite it"
        )
    if agents.is_symlink() or claude.is_symlink():
        return "AGENTS.md/CLAUDE.md are linked, not copied — the copy would replace the link"
    if not agents.exists():
        return None  # nothing there to destroy
    old = agents.read_bytes()
    if old == claude.read_bytes():
        return None  # already in sync; the copy is a no-op
    ratio = difflib.SequenceMatcher(
        None,
        claude.read_text(encoding="utf-8", errors="replace").splitlines(),
        agents.read_text(encoding="utf-8", errors="replace").splitlines(),
    ).ratio()
    if ratio < AGENTS_MD_SYNC_MIN_RATIO:
        return (
            f"AGENTS.md is only {ratio:.0%} similar to CLAUDE.md — too different to be a"
            f" stale copy; overwriting it would destroy {len(old.splitlines())} lines"
        )
    return None


def _check_agents_md_drift(*, no_exit: bool = False) -> bool:
    """Fail if AGENTS.md differs from CLAUDE.md (byte-compare).

    Opt out with `HARNESS_ALLOW_AGENTS_MD_DRIFT=1`. Byte-identical agent instructions are the
    right default *for this template* — Claude reads CLAUDE.md, Codex reads AGENTS.md,
    one document under two names. They are not obviously right for a repo that already
    had a hand-written AGENTS.md before the harness arrived: there the two files
    legitimately say different things, and welding them together is not a quality gate,
    it is a merge the harness chose on the human's behalf. Same accommodation
    `gherkin-guard` makes for a repo with no `.feature` files, for the same reason:
    a gate that fires on day one of an adoption gets the harness deleted, not obeyed.
    """
    if os.environ.get(AGENTS_MD_ALLOW_ENV) == "1":
        print(f"  {GREEN}⚠{RESET} agents-md-drift: skipped ({AGENTS_MD_ALLOW_ENV}=1)")
        return True
    claude = Path("CLAUDE.md")
    agents = Path("AGENTS.md")
    if not claude.exists():
        print(f"  {RED}✗{RESET} agents-md-drift: CLAUDE.md not found")
        if not no_exit:
            sys.exit(1)
        return False
    if not agents.exists():
        print(f"  {RED}✗{RESET} agents-md-drift: AGENTS.md missing — run `harness sync-agents-md`")
        if not no_exit:
            sys.exit(1)
        return False
    a, b = claude.read_bytes(), agents.read_bytes()
    if a == b:
        print(f"  {GREEN}✓{RESET} agents-md-drift")
        return True
    line = _first_diff_line(
        a.decode("utf-8", errors="replace"),
        b.decode("utf-8", errors="replace"),
    )
    print(
        f"  {RED}✗{RESET} agents-md-drift: AGENTS.md differs from CLAUDE.md "
        f"(first diff at line {line})"
    )
    # Never recommend `sync-agents-md` unconditionally here: on a brownfield adoption
    # this failure is guaranteed (the adopter has their own AGENTS.md, the template just
    # dropped its CLAUDE.md beside it), so an unconditional remedy would make data loss
    # the *default* path through the harness.
    print(
        "  ↳ fix: reconcile the two by hand, or `harness sync-agents-md` to overwrite"
        " AGENTS.md from CLAUDE.md (it refuses when AGENTS.md is not a stale copy),"
        f" or set {AGENTS_MD_ALLOW_ENV}=1 to keep a separate AGENTS.md"
    )
    if not no_exit:
        sys.exit(1)
    return False


def cmd_sync_agents_md() -> None:
    """Overwrite AGENTS.md with CLAUDE.md contents — unless that would destroy work.

    This used to be an unconditional copy and the drift check used to print it as the
    one remedy. On a brownfield adoption that pairing is silent data loss on the
    default path, so the copy now refuses every shape where the destination is not
    plausibly a stale copy of CLAUDE.md (see `_sync_refusal`).
    """
    claude = Path("CLAUDE.md")
    agents = Path("AGENTS.md")
    if not claude.exists():
        print(f"  {RED}✗{RESET} sync-agents-md: CLAUDE.md not found")
        sys.exit(1)
    if agents.exists() and claude.samefile(agents):
        print(f"  {GREEN}✓{RESET} sync-agents-md: AGENTS.md is CLAUDE.md (linked) — nothing to do")
        return
    refusal = _sync_refusal(claude, agents)
    if refusal is not None:
        print(f"  {RED}✗{RESET} sync-agents-md: {refusal}")
        print(
            "  ↳ fix: reconcile the two files by hand, or set"
            f" {AGENTS_MD_ALLOW_ENV}=1 to keep your own AGENTS.md and stop the drift check"
        )
        sys.exit(1)
    agents.write_bytes(claude.read_bytes())
    print(f"  {GREEN}✓{RESET} sync-agents-md: AGENTS.md ← CLAUDE.md")


def cmd_agents_md_drift() -> None:
    """Run the AGENTS.md / CLAUDE.md drift check."""
    _check_agents_md_drift()


def cmd_check() -> None:
    """Lockfile check, fix, format, typecheck, test, and every offline read-only gate.

    Invariant: `check` runs every gate that is offline, fast, and takes no build
    lock — `ci` adds only the network dependency audit, coverage, and CRAP
    (advisory). Arch (import-linter) qualifies here: it is a local dev dependency,
    runs offline, and takes no build lock, so it joins the read-only parallel batch
    below alongside complexity and acceptance, so a green `check`
    predicts a green `ci`. Accumulates every step's pass/fail instead of failing
    fast, so one run surfaces every problem — matching the bun/go/rust harnesses'
    `check` behavior — and ends with an `OK`/`FAIL` summary line.
    """
    start = time.monotonic()
    print("\n=== Quality Checks ===\n")
    _check_base_ref(warn_only=True)
    results = [
        all(
            run(gate.description, gate.cmd, hint=gate.hint, no_exit=True)
            for gate in _present([_lockfile_gate()])
        ),
        cmd_fix(no_exit=True),
        cmd_format(no_exit=True),
        cmd_typecheck(no_exit=True),
        cmd_test(no_exit=True),
    ]
    batch = [_complexity_gate(), *_acceptance_gates_or_warn(), *_arch_gates_or_warn()]
    _batch_ok, batch_failed = run_gates_parallel(batch)
    # One entry per gate, not one for the whole batch: collapsing the batch into a
    # single boolean makes the summary under-report by (failures - 1).
    results.extend([False] * len(batch_failed))
    results.extend([True] * (len(batch) - len(batch_failed)))
    results.append(_check_deadcode(no_exit=True))  # count-ratcheted; outside the Gate batch
    _check_stop_hooks_present()
    results.append(_check_arch_config_guard(warn_only=True))
    results.append(_check_gherkin_guard(warn_only=True))
    results.append(_check_agents_md_drift(no_exit=True))
    results.append(_check_suppressions_baseline(no_exit=True))

    elapsed = time.monotonic() - start
    passed = sum(1 for ok in results if ok)
    failed = len(results) - passed
    print()
    if failed:
        print(f"{RED}FAIL{RESET} {passed} passed, {failed} failed ({elapsed:.1f}s)")
        sys.exit(1)
    print(f"{GREEN}OK{RESET} {passed} passed ({elapsed:.1f}s)")


def cmd_pre_commit() -> None:
    """Staged checks + tests if source files staged.

    The arch-config and gherkin-first guards run before the staged-files early
    return: both are staged-mode and cheap, and a commit that stages only a
    non-source file (an arch config edit alone, a `.md`, a lockfile) must not
    bypass them.
    """
    print("\n=== Pre-commit Checks ===\n")
    if not _check_arch_config_guard(staged=True):
        sys.exit(1)
    if not _check_gherkin_guard(staged=True):
        sys.exit(1)

    files = _staged_py_files()
    if not files:
        print("No staged Python files — skipping checks")
        return

    cmd_fix(files)
    cmd_format(files)
    _restage_fixed_files(files)
    cmd_typecheck(files)
    _check_agents_md_drift()

    if any(_is_quality_python_file(f) for f in files):
        cmd_test()


def cmd_ci() -> None:
    """Run full read-only verification.

    Read-only gates run as a parallel batch (lint, format check, typecheck, audit,
    complexity, acceptance, arch) — captured and printed in submission order, run to
    completion so one pass surfaces every failure. The count-ratcheted gates run after
    it, sequentially: dead code, then coverage (captured) and CRAP (advisory unless
    --enforce).
    """
    print("\n=== CI Checks ===\n")
    base_ok = _check_base_ref(no_exit=True)
    gates = _present([
        _lint_gate(),
        _format_check_gate(),
        _typecheck_gate(),
        _audit_gate_or_warn(),
        _complexity_gate(),
        *_acceptance_gates_or_warn(),
        *_arch_gates_or_warn(),
    ])
    all_ok, _failed = run_gates_parallel(gates)
    all_ok = all_ok and base_ok
    all_ok = _check_deadcode(no_exit=True) and all_ok  # count-ratcheted; outside the batch
    cmd_coverage()  # self-skips; sequential, after the batch
    cmd_crap()  # reads .coverage/coverage.xml; advisory unless --enforce
    all_ok = _check_arch_config_guard() and all_ok
    all_ok = _check_gherkin_guard() and all_ok
    all_ok = _check_suppressions_baseline(no_exit=True) and all_ok
    _exit_if_failed(all_ok)


def cmd_pre_push() -> None:
    """Read-only push gate: the offline checks pre-commit and stop-hook do not run.

    pre-commit covers fix/format/typecheck/test on staged files; stop-hook adds
    complexity. This fills the gap with the deterministic, offline gates none of them
    run — lint, format check, acceptance, arch — validating the whole pushed tree
    (after merges/rebases/--no-verify, which pre-commit may never have seen) before it
    leaves the machine. Network (audit) and advisory (coverage/CRAP) gates stay in ci.
    """
    print("\n=== Pre-push Checks ===\n")
    base_ok = _check_base_ref(no_exit=True)
    arch_config_ok = _check_arch_config_guard(include_pre_push_stdin=True)
    gherkin_ok = _check_gherkin_guard(include_pre_push_stdin=True)
    gates = _present([
        _lint_gate(),
        _format_check_gate(),
        *_acceptance_gates_or_warn(),
        *_arch_gates_or_warn(),
    ])
    gates_ok, _failed = run_gates_parallel(gates)
    _exit_if_failed(gates_ok and base_ok and arch_config_ok and gherkin_ok)


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


def _is_stop_hook_handler(handler: object) -> bool:
    """True for a command handler that already runs our stop-hook (any form)."""
    return (
        isinstance(handler, dict)
        and handler.get("type") == "command"
        and isinstance(handler.get("command"), str)
        and "stop-hook" in handler["command"]
    )


def _install_stop_hook(path: Path, hook: dict[str, Any], *, claude_settings: bool = False) -> None:
    """Inject/refresh the Stop hook in a settings file, preserving every other hook.

    Idempotent: an existing stop-hook handler (current or legacy) is replaced in
    place and any duplicates are dropped, so re-running never accumulates entries.
    """
    data = _read_json_object(path)
    if claude_settings and "$schema" not in data:
        data["$schema"] = CLAUDE_SETTINGS_SCHEMA

    hooks = _json_object_child(data, "hooks", path)
    stop_groups = _json_list_child(hooks, "Stop", path)
    installed = False

    for group in stop_groups:
        if not isinstance(group, dict):
            continue
        group_hooks = group.get("hooks")
        if not isinstance(group_hooks, list):
            continue
        next_group_hooks: list[Any] = []
        for handler in group_hooks:
            if _is_stop_hook_handler(handler):
                if not installed:
                    next_group_hooks.append(dict(hook))
                    installed = True
                continue
            next_group_hooks.append(handler)
        group["hooks"] = next_group_hooks

    if not installed:
        stop_groups.append({"hooks": [dict(hook)]})

    _write_json_object(path, data)


def _git_hook_path(name: str) -> Path:
    """Resolve a git hook path via `git rev-parse` so worktrees / core.hooksPath work."""
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", f"hooks/{name}"],
            capture_output=True,
            text=True,
            check=False,
            env=git_env,
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
    """Install git pre-commit + pre-push hooks and the Claude/Codex Stop wiring."""
    _install_git_hook("pre-commit")
    _install_git_hook("pre-push")
    _install_stop_hook(Path(".codex/hooks.json"), CODEX_STOP_HOOK)
    _install_stop_hook(Path(".claude/settings.json"), CLAUDE_STOP_HOOK, claude_settings=True)
    print("Installed pre-commit, pre-push, and Claude/Codex Stop hooks")


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

TASKS: dict[str, tuple[Callable[..., object], str]] = {
    "fix": (cmd_fix, "Fix lint errors with ruff"),
    "format": (cmd_format, "Format code with ruff"),
    "lint": (cmd_lint, "Lint code with ruff (read-only)"),
    "typecheck": (cmd_typecheck, "Type-check with basedpyright"),
    "test": (cmd_test, "Run tests, or syntax check when no tests exist"),
    "check": (
        cmd_check,
        "Fix + format + typecheck on changed files (--all for the whole tree), test, every gate",
    ),
    "pre-commit": (cmd_pre_commit, "Staged checks + tests"),
    "pre-push": (cmd_pre_push, "Read-only push gate: lint, format check, acceptance, arch"),
    "ci": (cmd_ci, "Full verification: lint, typecheck, tests, acceptance, coverage, crap, arch"),
    "audit": (cmd_audit, "Audit dependencies for known vulnerabilities"),
    "acceptance": (cmd_acceptance, "Run acceptance scenarios (behave)"),
    "coverage": (cmd_coverage, "Tests with coverage threshold (--min=N)"),
    "mutation": (cmd_mutation, "Mutation testing (not configured by default; warns and skips)"),
    "crap": (cmd_crap, "CRAP complexity x coverage gate (advisory)"),
    "suppressions": (cmd_suppressions, "Show or update suppression baseline"),
    "complexity": (cmd_complexity, "Cyclomatic complexity gate (lizard, CCN 15, args 8)"),
    "deadcode": (cmd_deadcode, "Detect unused (dead) code with vulture (app sources only)"),
    "arch": (cmd_arch, "Architecture checks (import-linter)"),
    "arch-config-guard": (cmd_arch_config_guard, "Block unreviewed arch config changes"),
    "gherkin-guard": (
        cmd_gherkin_guard,
        "Block production-source changes with no matching `.feature`",
    ),
    "post-edit": (cmd_post_edit, "Format if source files changed"),
    "stop-hook": (cmd_stop_hook, "Format changed files, then run stop-hook checks"),
    "agents-md-drift": (cmd_agents_md_drift, "Fail if AGENTS.md differs from CLAUDE.md"),
    "sync-agents-md": (cmd_sync_agents_md, "Overwrite AGENTS.md from CLAUDE.md"),
    "setup-hooks": (
        cmd_hooks,
        "Install git pre-commit + pre-push hooks and Claude/Codex Stop wiring",
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
