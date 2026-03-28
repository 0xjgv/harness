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
    from collections.abc import Callable

# ── Configuration ─────────────────────────────────────────────────

SRC_DIR = "src"
TEST_DIR = "tests"

# ── Output ────────────────────────────────────────────────────────

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
VERBOSE = "--verbose" in sys.argv


def run(description: str, cmd: list[str]) -> None:
    """Run command silently; show output only on failure."""
    if VERBOSE:
        print(f"  -> {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            sys.exit(result.returncode)
        return

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        extra = _parse_pytest_summary(result.stdout) if "pytest" in cmd else ""
        print(f"  {GREEN}✓{RESET} {description}{extra}")
    else:
        print(f"  {RED}✗{RESET} {description}")
        print(f"{RED}Command failed: {' '.join(cmd)}{RESET}")
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")
        sys.exit(result.returncode)


def _parse_pytest_summary(output: str) -> str:
    """Extract '(N tests, X.Xs)' from pytest output."""
    m = re.search(r"(\d+) passed.*?in ([\d.]+s)", output)
    return f" ({m.group(1)} tests, {m.group(2)})" if m else ""


# ── Git helpers ───────────────────────────────────────────────────


def _staged_py_files() -> list[str]:
    """Return staged .py files under src/ and tests/, excluding deleted files."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=d"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [
        f
        for f in result.stdout.strip().splitlines()
        if f.endswith(".py") and f.startswith((f"{SRC_DIR}/", f"{TEST_DIR}/"))
    ]


# ── Commands ──────────────────────────────────────────────────────


def cmd_install() -> None:
    print("\n=== Installing Dependencies ===\n")
    run("Sync dependencies", ["uv", "sync"])


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
    run("Run tests", ["uv", "run", "pytest", "-x", "-q"])


def cmd_test_cov() -> None:
    run("Run tests with coverage", ["uv", "run", "pytest", "--cov", "--cov-report=term-missing"])


# ── Stages ────────────────────────────────────────────────────────


def cmd_check() -> None:
    """Fix, format, typecheck, and test the full repo."""
    print("\n=== Quality Checks ===\n")
    cmd_fix()
    cmd_format()
    cmd_typecheck()
    cmd_test()


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
    for name in [".pytest_cache", ".ruff_cache", "build", "dist", "htmlcov"]:
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
    "install": (cmd_install, "Install dependencies with uv"),
    "fix": (cmd_fix, "Fix lint errors with ruff"),
    "format": (cmd_format, "Format code with ruff"),
    "lint": (cmd_lint, "Lint code with ruff (read-only)"),
    "typecheck": (cmd_typecheck, "Type-check with basedpyright"),
    "check": (cmd_check, "Fix + format + typecheck + test (full repo)"),
    "pre-commit": (cmd_pre_commit, "Staged checks + tests"),
    "ci": (cmd_ci, "Lint + format check + typecheck + tests with coverage"),
    "test": (cmd_test, "Run tests with pytest"),
    "test-cov": (cmd_test_cov, "Run tests with coverage"),
    "setup-hooks": (cmd_hooks, "Install git pre-commit hook"),
    "clean": (cmd_clean, "Remove cache and build artifacts"),
}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if not args or args[0] == "help":
        print("Usage: uv run harness <command> [--verbose]\n")
        print("Commands:")
        for name, (_, desc) in TASKS.items():
            print(f"  {name:<14} {desc}")
        sys.exit(0)

    task_name = args[0]
    if task_name not in TASKS:
        print(f"Unknown command: {task_name}")
        sys.exit(1)

    TASKS[task_name][0]()


if __name__ == "__main__":
    main()
