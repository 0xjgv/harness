#!/usr/bin/env python3
"""Project development tasks. Zero dependencies — stdlib only."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

# ── Configuration ─────────────────────────────────────────────────

SRC_DIR = "src"
TEST_DIR = "tests"

# ── Output ────────────────────────────────────────────────────────

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
VERBOSE = "--verbose" in sys.argv


def run(description: str, cmd: list[str], *, no_exit: bool = False) -> None:
    """Run command silently; show output only on failure."""
    if VERBOSE:
        print(f"  -> {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            if no_exit:
                return
            sys.exit(result.returncode)
        return

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        extra = _parse_unittest_summary(result.stderr) if "unittest" in cmd else ""
        print(f"  {GREEN}✓{RESET} {description}{extra}")
    else:
        print(f"  {RED}✗{RESET} {description}")
        print(f"{RED}Command failed: {' '.join(cmd)}{RESET}")
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")
        if no_exit:
            return
        sys.exit(result.returncode)


def _parse_unittest_summary(output: str) -> str:
    """Extract '(N tests, X.Xs)' from unittest output."""
    m = re.search(r"Ran (\d+) tests? in ([\d.]+s)", output)
    return f" ({m.group(1)} tests, {m.group(2)})" if m else ""


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
    actual_roots = roots if roots is not None else (SRC_DIR, TEST_DIR)
    for dir_name in actual_roots:
        for py_file in sorted(Path(dir_name).rglob("*.py")):
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
    """Return staged .py files under src/ and tests/, excluding deleted files."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=d", "--relative"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [
        f
        for f in result.stdout.strip().splitlines()
        if f.endswith(".py") and f.startswith((f"{SRC_DIR}/", f"{TEST_DIR}/"))
    ]


def _changed_py_files() -> list[str]:
    """Return .py files with uncommitted changes under src/ and tests/."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [
        line[3:]
        for line in result.stdout.strip().splitlines()
        if len(line) > 3
        and line[3:].endswith(".py")
        and line[3:].startswith((f"{SRC_DIR}/", f"{TEST_DIR}/"))
    ]


# ── Commands ──────────────────────────────────────────────────────


def cmd_fix(files: list[str] | None = None) -> None:
    target = files or ["."]
    run("Fix lint errors", ["uv", "run", "ruff", "check", "--fix", *target])


def cmd_format(files: list[str] | None = None) -> None:
    target = files or ["."]
    run("Format code", ["uv", "run", "ruff", "format", *target])


def cmd_lint(files: list[str] | None = None) -> None:
    target = files or ["."]
    run("Lint check", ["uv", "run", "ruff", "check", *target])


def cmd_format_check() -> None:
    run("Format check", ["uv", "run", "ruff", "format", "--check", "."])


def cmd_typecheck() -> None:
    run("Type check", ["uv", "run", "basedpyright", SRC_DIR])


def cmd_test() -> None:
    run("Run tests", ["uv", "run", "python", "-m", "unittest", "discover", "-s", TEST_DIR, "-q"])


def cmd_test_cov() -> None:
    run(
        "Run tests with coverage",
        ["uv", "run", "coverage", "run", "-m", "unittest", "discover", "-s", TEST_DIR, "-q"],
    )
    run(
        "Coverage report",
        ["uv", "run", "coverage", "report", "--show-missing"],
    )


def cmd_audit() -> None:
    run("Dep audit", ["uv", "run", "--with", "pip-audit", "pip-audit"])


def cmd_post_edit() -> None:
    """Format if source files have uncommitted changes (Claude Code hook)."""
    if not _changed_py_files():
        return
    run("Fix lint errors", ["uv", "run", "ruff", "check", "--fix", "."], no_exit=True)
    run("Format code", ["uv", "run", "ruff", "format", "."], no_exit=True)


# ── Stages ────────────────────────────────────────────────────────


def cmd_check() -> None:
    """Fix, format, typecheck, and test the full repo."""
    print("\n=== Quality Checks ===\n")
    try:
        cmd_fix()
        cmd_format()
        cmd_typecheck()
        cmd_test()
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

    if any(f.startswith(f"{SRC_DIR}/") for f in files):
        cmd_test()


def cmd_ci() -> None:
    """Full verification: lint, format check, typecheck, tests with coverage."""
    print("\n=== CI Checks ===\n")
    cmd_lint()
    cmd_format_check()
    cmd_typecheck()
    cmd_audit()
    cmd_test_cov()


def cmd_hooks() -> None:
    """Install git pre-commit hook."""
    hook = Path(".git/hooks/pre-commit")
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nuv run harness pre-commit\n", encoding="utf-8")
    hook.chmod(0o755)
    print("Installed pre-commit hook")


def cmd_clean() -> None:
    """Remove cache and build artifacts."""
    print("\n=== Cleaning Up ===\n")
    for name in [".ruff_cache", "build", "dist", "htmlcov"]:
        p = Path(name)
        if p.is_dir():
            shutil.rmtree(p)
    for name in [".coverage"]:
        p = Path(name)
        if p.is_file():
            p.unlink()
    for p in Path().rglob("__pycache__"):
        shutil.rmtree(p)
    run("Ruff clean", ["uv", "run", "ruff", "clean"])


# ── CLI dispatch ──────────────────────────────────────────────────

TASKS: dict[str, tuple[Callable[..., None], str]] = {
    "fix": (cmd_fix, "Fix lint errors with ruff"),
    "format": (cmd_format, "Format code with ruff"),
    "lint": (cmd_lint, "Lint code with ruff (read-only)"),
    "typecheck": (cmd_typecheck, "Type-check with basedpyright"),
    "test": (cmd_test, "Run tests with unittest"),
    "check": (cmd_check, "Fix + format + typecheck + test (full repo)"),
    "pre-commit": (cmd_pre_commit, "Staged checks + tests"),
    "ci": (cmd_ci, "Lint + format check + typecheck + tests with coverage"),
    "audit": (cmd_audit, "Audit dependencies for known vulnerabilities"),
    "post-edit": (cmd_post_edit, "Format if source files changed (Claude Code hook)"),
    "setup-hooks": (cmd_hooks, "Install git pre-commit hook"),
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
