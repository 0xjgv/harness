"""Given steps for scoping.feature.

Builds a throwaway git repository (never the developer's own remotes) whose only
resolvable base ref is its local `main`, plus a `.venv/bin/ruff` stub so the
scenarios observe which paths the harness hands the linter — and which findings it
keeps — without needing ruff, a network, or a uv project. No `pyproject.toml` is
written, so `_tool` resolves its `.venv/bin/<tool>` tier and lands on the stub.

The stub answers `--output-format=json` runs by printing a findings file the step
wrote, so a scenario can place a violation on an exact line and assert whether the
line-level filter kept it. Every other invocation echoes its argv.

The generic `When I run ...` / `Then ...` steps live in crap_steps.py; behave
loads every module under steps/ into one registry.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from behave import given

GIT = shutil.which("git") or "git"

FINDINGS_FILE = "ruff-findings.json"
RUFF_STUB = (
    "#!/bin/sh\n"
    'case "$*" in\n'
    '  *--output-format=json*) cat "$(dirname "$0")/../../{findings}" ;;\n'
    '  *) echo "ruff-stub $*" ;;\n'
    "esac\n"
    "exit 0\n"
)

LEGACY = "import os\n\nVALUE = 1\n"


def _git(context, *args):
    subprocess.run([GIT, *args], cwd=str(context.tmp), check=True, capture_output=True, text=True)


def _write_findings(context, findings):
    (context.tmp / FINDINGS_FILE).write_text(json.dumps(findings))


def _finding(path, row, code):
    return {
        "filename": path,
        "code": code,
        "message": f"stub {code}",
        "location": {"row": row, "column": 1},
        "end_location": {"row": row, "column": 2},
    }


def _make_git_project(context, root=None):
    context.tmp = root or Path(tempfile.mkdtemp(prefix="scoping-"))
    venv_bin = context.tmp / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub = venv_bin / "ruff"
    stub.write_text(RUFF_STUB.format(findings=FINDINGS_FILE))
    stub.chmod(0o755)
    _write_findings(context, [])

    src = context.tmp / "src"
    src.mkdir()
    (src / "changed.py").write_text("CHANGED = 1\n")
    (src / "untouched.py").write_text("UNTOUCHED = 1\n")
    (src / "legacy.py").write_text(LEGACY)

    _git(context, "init", "-b", "main")
    _git(context, "config", "user.email", "harness@example.invalid")
    _git(context, "config", "user.name", "Harness Test")
    _git(context, "add", "-A")
    _git(context, "commit", "-m", "base")


@given("a git project with a lint tool stub and no changes")
def step_clean_git_project(context):
    _make_git_project(context)


@given("a git project with a lint tool stub and one changed Python file")
def step_git_project_with_one_change(context):
    _make_git_project(context)
    (context.tmp / "src" / "changed.py").write_text("CHANGED = 2\n")


TEST_MODULE = (
    "import unittest\n\n\n"
    "class TestIt(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        self.assertTrue(True)\n"
)


def _make_git_project_with_tests(context):
    """The lint-stub project plus a real unittest module for changed.py and
    untouched.py — not for legacy.py, so a change to it maps to no test."""
    context.tmp = Path(tempfile.mkdtemp(prefix="scoping-"))
    tests = context.tmp / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_changed.py").write_text(TEST_MODULE)
    (tests / "test_untouched.py").write_text(TEST_MODULE)
    _make_git_project(context, root=context.tmp)


@given("a git project with a test module per source file and no changes")
def step_clean_git_project_with_tests(context):
    _make_git_project_with_tests(context)


@given("a git project with a test module per source file and one changed Python file")
def step_git_project_with_tests_and_one_change(context):
    _make_git_project_with_tests(context)
    (context.tmp / "src" / "changed.py").write_text("CHANGED = 2\n")


@given("a git project with a test module per source file and one changed untested Python file")
def step_git_project_with_tests_and_untested_change(context):
    _make_git_project_with_tests(context)
    (context.tmp / "src" / "legacy.py").write_text(LEGACY + "# appended\n")


@given("a legacy file with a comment appended to its last line")
def step_legacy_with_appended_comment(context):
    _make_git_project(context)
    legacy = context.tmp / "src" / "legacy.py"
    legacy.write_text(LEGACY + "# appended\n")
    context.appended_row = len(LEGACY.splitlines()) + 1


@given('the linter reports "{code}" on line {row:d} of src/legacy.py')
def step_linter_reports(context, code, row):
    _write_findings(context, [_finding("src/legacy.py", row, code)])
