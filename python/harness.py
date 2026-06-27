#!/usr/bin/env python3
"""Project development tasks. Zero dependencies — stdlib only."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

# ── Configuration ─────────────────────────────────────────────────

APP_SOURCES = ("src",)
QUALITY_SOURCES = ("src", "harness.py")
TEST_DIR = "tests"
LIZARD = "lizard@1.22.2"
VULTURE = "vulture@2.16"
VULTURE_MIN_CONFIDENCE = "60"
VULTURE_ALLOWLIST = "vulture_allowlist.py"
COMPLEXITY_MAX_ARGS = 8

# ── Hook wiring (installed by `setup-hooks`) ──────────────────────
# Claude reads .claude/settings.json and runs the harness directly; Codex reads
# .codex/hooks.json and goes through the codex-stop-hook.sh wrapper (which turns
# the exit code into the block/continue JSON Codex expects). Keep both forms in
# sync with the committed template files so re-running the installer is a no-op.
CLAUDE_SETTINGS_SCHEMA = "https://json.schemastore.org/claude-code-settings.json"
CLAUDE_STOP_COMMAND = "cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook"
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


@dataclasses.dataclass(frozen=True)
class GateResult:
    """The captured outcome of one gate command; safe to build off the main thread."""

    description: str
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclasses.dataclass(frozen=True)
class Gate:
    """A read-only gate's label and command, shared by standalone cmd_* and the batch."""

    description: str
    cmd: list[str]


def run_capture(description: str, cmd: list[str]) -> GateResult:
    """Run a command with output captured; the thread-safe unit for the parallel batch."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return GateResult(description, cmd, result.returncode, result.stdout, result.stderr)


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
    if not no_exit:
        sys.exit(result.returncode)


def run(description: str, cmd: list[str], *, no_exit: bool = False, stream: bool = False) -> None:
    """Run command silently; show output only on failure.

    Pass stream=True for long-running commands (tests, coverage) so their live
    output is shown instead of being captured — captured silence looks like a hang.
    """
    if VERBOSE or stream:
        print(f"  -> {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0 and not no_exit:
            sys.exit(result.returncode)
        return

    print_gate_result(run_capture(description, cmd), no_exit=no_exit)


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
        results = list(executor.map(lambda gate: run_capture(gate.description, gate.cmd), gates))

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


def _parse_line_for_suppressions(line: str) -> list[tuple[str, list[str]]]:
    """Return all (kind, rules) matches found on a single line."""
    matches: list[tuple[str, list[str]]] = []
    for kind, pat in _SUPPRESSION_PATTERNS:
        m = pat.search(line)
        if m:
            rules = [r.strip() for r in m.group(1).split(",") if r.strip()] if m.group(1) else []
            matches.append((kind, rules))
    return matches


def _scan_suppressions(roots: Iterable[str] | None = None) -> dict[str, list[list[str]]]:
    """Scan Python files for suppression comments. Returns {kind: [rules...]}."""
    results: dict[str, list[list[str]]] = {}
    actual_roots = roots if roots is not None else _quality_targets()
    for py_file in _iter_python_files(actual_roots):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            for kind, rules in _parse_line_for_suppressions(line):
                results.setdefault(kind, []).append(rules)
    return results


def _print_suppressions_report() -> None:
    """Print a report-only summary of suppressions found in source."""
    results = _scan_suppressions()
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
    """Return project .py files with uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    changed: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) <= 3 or "D" in line[:2]:
            continue
        path = _porcelain_path(line)
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
    return Gate("Lint check", ["uv", "run", "ruff", "check", *target])


def cmd_lint(files: list[str] | None = None) -> None:
    gate = _lint_gate(files)
    run(gate.description, gate.cmd)


def _format_check_gate() -> Gate:
    return Gate("Format check", ["uv", "run", "ruff", "format", "--check", "."])


def _typecheck_gate() -> Gate:
    return Gate("Type check", ["uv", "run", "basedpyright", *_quality_targets()])


def cmd_typecheck() -> None:
    gate = _typecheck_gate()
    run(gate.description, gate.cmd)


def cmd_test() -> None:
    if _has_tests():
        run(
            "Run tests",
            ["uv", "run", "python", "-m", "unittest", "discover", "-s", TEST_DIR, "-q"],
            stream=True,
        )
        return

    files = [str(path) for path in _iter_python_files(_quality_targets(include_tests=False))]
    if not files:
        warn("Syntax check: no Python files found; skipped")
        return
    run("Syntax check", ["uv", "run", "python", "-m", "py_compile", *files])


def cmd_coverage() -> None:
    """Run tests under coverage with threshold + uncovered listing."""
    if not _has_tests():
        warn(f"Coverage: no {TEST_DIR}/test*.py files; skipped")
        return

    min_pct = int(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--min=")), "0"))
    run(
        "Coverage (run)",
        ["uv", "run", "coverage", "run", "-m", "unittest", "discover", "-s", TEST_DIR, "-q"],
        stream=True,
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
    return [Gate("Acceptance (behave)", ["uv", "run", "behave", str(features_dir), "--no-color"])]


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
    return Gate("Dep audit", ["uv", "run", "--with", "pip-audit", "pip-audit"])


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
            "15",
            "-a",
            str(COMPLEXITY_MAX_ARGS),
            "-L",
            "100",
            "-i",
            "0",
        ],
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
    )


def cmd_deadcode() -> None:
    gate = _deadcode_gate()
    run(gate.description, gate.cmd)


def cmd_post_edit() -> None:
    """Format if source files have uncommitted changes."""
    files = _changed_py_files()
    if not files:
        return
    run("Fix lint errors", ["uv", "run", "ruff", "check", "--fix", *files], no_exit=True)
    run("Format code", ["uv", "run", "ruff", "format", *files], no_exit=True)


def cmd_stop_hook() -> None:
    """Run stop-time checks after agent edits."""
    print("\n=== Stop Hook Checks ===\n")
    cmd_post_edit()  # mutating — sequential, first
    all_ok = run_gates_parallel([_complexity_gate(), _deadcode_gate()])  # read-only batch
    cmd_crap()  # streaming advisory — after the batch
    _exit_if_failed(all_ok)


# ── Stages ────────────────────────────────────────────────────────


def _check_hooks_present() -> None:
    """Warn when required hook scripts are missing (drift detection)."""
    required = [
        ".claude/scripts/session-start.sh",
        ".claude/scripts/ups-classify.sh",
        ".claude/scripts/pre-bash-gate.sh",
        ".claude/scripts/pre-edit-gate.sh",
    ]
    missing = [p for p in required if not Path(p).exists()]
    if missing:
        print(f"  {RED}⚠{RESET} Missing hook scripts: {', '.join(missing)}")


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
    claude = Path("CLAUDE.md")
    agents = Path("AGENTS.md")
    if not claude.exists():
        print(f"  {RED}✗{RESET} sync-agents-md: CLAUDE.md not found")
        sys.exit(1)
    agents.write_bytes(claude.read_bytes())
    print(f"  {GREEN}✓{RESET} sync-agents-md: AGENTS.md ← CLAUDE.md")


def cmd_agents_md_drift() -> None:
    """Run the AGENTS.md / CLAUDE.md drift check."""
    _check_agents_md_drift()


def cmd_check() -> None:
    """Fix, format, typecheck, and test the full repo."""
    print("\n=== Quality Checks ===\n")
    try:
        cmd_fix()
        cmd_format()
        cmd_typecheck()
        cmd_test()
        _check_hooks_present()
        _check_agents_md_drift()
    finally:
        _print_suppressions_report()


def cmd_pre_commit() -> None:
    """Staged checks + tests if source files staged."""
    files = _staged_py_files()
    if not files:
        print("No staged Python files — skipping checks")
        return

    print("\n=== Pre-commit Checks ===\n")
    cmd_fix(files)
    cmd_format(files)
    cmd_typecheck()
    _check_agents_md_drift()

    if any(_is_quality_python_file(f) for f in files):
        cmd_test()


def cmd_ci() -> None:
    """Run full read-only verification.

    Read-only gates run as a parallel batch (lint, format check, typecheck, audit,
    complexity, deadcode, acceptance, arch) — captured and printed in submission order,
    run to completion so one pass surfaces every failure. Coverage and CRAP run after
    the batch: coverage streams (a long command), CRAP is advisory unless --enforce.
    """
    print("\n=== CI Checks ===\n")
    gates = [
        _lint_gate(),
        _format_check_gate(),
        _typecheck_gate(),
        _audit_gate(),
        _complexity_gate(),
        _deadcode_gate(),
        *_acceptance_gates_or_warn(),
        *_arch_gates_or_warn(),
    ]
    all_ok = run_gates_parallel(gates)
    cmd_coverage()  # streams; self-skips; sequential, after the batch
    cmd_crap()  # reads .coverage/coverage.xml; advisory unless --enforce
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
    gates = [
        _lint_gate(),
        _format_check_gate(),
        *_acceptance_gates_or_warn(),
        *_arch_gates_or_warn(),
    ]
    _exit_if_failed(run_gates_parallel(gates))


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

TASKS: dict[str, tuple[Callable[..., None], str]] = {
    "fix": (cmd_fix, "Fix lint errors with ruff"),
    "format": (cmd_format, "Format code with ruff"),
    "lint": (cmd_lint, "Lint code with ruff (read-only)"),
    "typecheck": (cmd_typecheck, "Type-check with basedpyright"),
    "test": (cmd_test, "Run tests, or syntax check when no tests exist"),
    "check": (cmd_check, "Fix + format + typecheck + test (full repo)"),
    "pre-commit": (cmd_pre_commit, "Staged checks + tests"),
    "pre-push": (cmd_pre_push, "Read-only push gate: lint, format check, acceptance, arch"),
    "ci": (cmd_ci, "Full verification: lint, typecheck, tests, acceptance, coverage, crap, arch"),
    "audit": (cmd_audit, "Audit dependencies for known vulnerabilities"),
    "acceptance": (cmd_acceptance, "Run acceptance scenarios (behave)"),
    "coverage": (cmd_coverage, "Tests with coverage threshold (--min=N)"),
    "mutation": (cmd_mutation, "Mutation testing (mutmut, advisory)"),
    "crap": (cmd_crap, "CRAP complexity x coverage gate (advisory)"),
    "complexity": (cmd_complexity, "Cyclomatic complexity gate (lizard, CCN 15, args 8)"),
    "deadcode": (cmd_deadcode, "Detect unused (dead) code with vulture (app sources only)"),
    "arch": (cmd_arch, "Architecture checks (import-linter)"),
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
