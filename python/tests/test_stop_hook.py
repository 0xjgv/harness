"""Stop hook and PostToolUse hook: pure helpers, then a few runs of the real CLI."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import harness

HARNESS = Path(harness.__file__).resolve()
GIT = shutil.which("git") or "git"
# No git hook variables and no base or guard overrides from the ambient shell.
ENV = {
    key: value
    for key, value in os.environ.items()
    if not key.startswith(("GIT_", "HARNESS_")) and key != "GITHUB_BASE_REF"
}


def git(root, *args):
    config = ["-c", "user.email=h@example.com", "-c", "user.name=H", "-c", "commit.gpgsign=false"]
    cmd = [GIT, "-C", str(root), *config, *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True, env=ENV).stdout


def run_harness(project, *args, stdin=""):
    return subprocess.run(
        [sys.executable, str(HARNESS), *args],
        cwd=project,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env=ENV,
        timeout=120,
    )


def write(project, relative, text):
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@contextmanager
def project_repo(subdir=""):
    """A fresh git repo on `main` and its project directory (optionally `subdir`)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q", "-b", "main")
        yield root, root / subdir


def commit(root):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change")


def branchy(name, branches):
    """A function whose CCN is `branches + 1`."""
    body = "".join(f"    if value == {i}:\n        return {i}\n" for i in range(branches))
    return f"def {name}(value):\n{body}    return -1\n"


def module(*blocks):
    """A ruff-formatted module exporting its functions (so vulture sees them used)."""
    names = ", ".join(f'"{name}"' for name in re.findall(r"^def (\w+)", "".join(blocks), re.M))
    return "\n\n".join([f"__all__ = [{names}]\n", *blocks])


HELPER = "def helper():\n    return 1\n"


# ── Pure helpers ──────────────────────────────────────────────────


class TestParseDiffRanges(unittest.TestCase):
    def test_parses_new_side_ranges_per_file(self):
        diff = (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,0 +2 @@ x\n"
            "+y\n"
            "@@ -10,2 +11,3 @@ def f():\n"
            "+++ looks like a header but is an added line\n"
            "@@ -20,4 +22,0 @@\n"
            "diff --git a/old.py b/old.py\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "diff --git a/sp ace.py b/sp ace.py\n"
            "--- /dev/null\n"
            "+++ b/sp ace.py\t\n"
            "@@ -0,0 +1,4 @@\n"
            "diff --git a/only_deleted.py b/only_deleted.py\n"
            "--- a/only_deleted.py\n"
            "+++ b/only_deleted.py\n"
            "@@ -3 +2,0 @@\n"
        )
        self.assertEqual(
            harness._parse_diff_ranges(diff),
            {"src/app.py": [(2, 2), (11, 13)], "sp ace.py": [(1, 4)], "only_deleted.py": []},
        )


class TestStopHookVerdict(unittest.TestCase):
    def test_exit_codes(self):
        cases = [  # payload, failed tools, stop_hook_active, exit
            ("", 0, False, 0),
            ("", 1, False, 1),
            ("", 0, True, 0),
            ("P", 0, False, 2),
            ("P", 1, False, 2),  # a crashed gate never hides another gate's findings
            ("P", 0, True, 1),  # already blocked once on this stop
            ("P", 1, True, 1),
        ]
        for payload, failed, active, expected in cases:
            with self.subTest(payload=payload, failed=failed, active=active):
                self.assertEqual(harness._stop_hook_exit(payload, failed, active), expected)

    def test_a_tool_that_cannot_run_is_reported_and_exits_1(self):
        cmd = [sys.executable, "-c", "import sys; sys.exit('bad config')"]
        results = [harness._delta_result("Lint", lambda: [harness._run_tool("ruff", cmd)])]
        with redirect_stderr(io.StringIO()) as stderr:
            code = harness._report_stop_hook(results, {"stop_hook_active": True})
        message = "stop-hook: Lint could not run: ruff exited 1: bad config\n"
        self.assertEqual((code, stderr.getvalue()), (1, message))

    def test_payload_names_failed_gates_and_caps_findings(self):
        results = [
            harness.DeltaResult("Lint", [f"a.py:{n}: E1 x" for n in range(1, 24)]),
            harness.DeltaResult("Complexity", [], "boom"),
            harness.DeltaResult("Dead code", ["b.py:1: unused"]),
        ]
        lines = harness._stop_hook_payload(results).splitlines()
        self.assertEqual(lines[0], "stop-hook failed: Lint, Dead code")
        self.assertEqual(lines[1:21], [f"a.py:{n}: E1 x" for n in range(1, 21)])
        self.assertEqual(lines[21:], ["… +4 more — run `uv run harness stop-hook --verbose`"])
        self.assertEqual(harness._stop_hook_payload(results[1:2]), "")
        with mock.patch.object(harness, "VERBOSE", True):
            self.assertEqual(len(harness._stop_hook_payload(results).splitlines()), 25)


class TestComplexityFindings(unittest.TestCase):
    def test_reports_touched_functions_over_a_limit(self):
        def row(name, start, end, ccn=1, args=1, length=5):
            location = f"{name}@{start}-{end}@src/a.py"
            fields = [1, ccn, 9, args, length, location, "src/a.py", name, name, start, end]
            return ",".join(map(str, fields))

        report = "\n".join([
            "NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end",
            row("touched", 10, 20, ccn=16),
            row("untouched", 30, 40, ccn=16),
            row("small", 21, 22),
            row("wide", 24, 130, ccn=17, args=9, length=107),
        ])
        self.assertEqual(
            harness._complexity_findings(report, {"src/a.py": [(18, 25)]}),
            [
                "src/a.py:10: touched CCN 16 (limit 15)",
                "src/a.py:24: wide CCN 17 (limit 15)",
                "src/a.py:24: wide args 9 (limit 8)",
                "src/a.py:24: wide length 107 (limit 100)",
            ],
        )


class TestHookTarget(unittest.TestCase):
    def test_resolves_project_python_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "src/app.py", "")
            write(root, "src/data.txt", "")
            cases = [
                ({"tool_input": {"file_path": str(root / "src" / "app.py")}}, "src/app.py"),
                ({"tool_input": {"file_path": "src/app.py"}}, "src/app.py"),
                ({"tool_input": {"file_path": "src/data.txt"}}, None),
                ({"tool_input": {"file_path": "../elsewhere/src/app.py"}}, None),
                ({"tool_input": {"file_path": ""}}, None),
                ({"tool_input": {"file_path": 3}}, None),
                ({"tool_input": "src/app.py"}, None),
                ({}, None),
            ]
            for event, expected in cases:
                with self.subTest(event=event):
                    self.assertEqual(harness._hook_target(event, root), expected)


class TestPrePushTests(unittest.TestCase):
    def test_tests_run_without_git_hook_env(self):
        result = harness.GateResult("Tests", ["t"], 0, "", "")
        with (
            mock.patch.dict(os.environ, {"GIT_DIR": "/repo/.git", "KEEP": "1"}),
            mock.patch.object(harness, "_check_branch_guard", return_value=True),
            mock.patch.object(harness, "_check_arch_config_guard", return_value=True),
            mock.patch.object(harness, "run_capture", return_value=result) as run_capture,
            mock.patch.object(harness, "run_gates_parallel", return_value=True),
            redirect_stdout(io.StringIO()),
        ):
            harness.cmd_pre_push()

        env = run_capture.call_args.kwargs["env"]
        self.assertEqual((env["KEEP"], "GIT_DIR" in env), ("1", False))


# ── End to end: the real CLI on throwaway repos ────────────────────


class TestEndToEnd(unittest.TestCase):
    def test_clean_tree_is_silent(self):
        with project_repo() as (root, project):
            write(project, "src/app.py", module(HELPER))
            commit(root)
            result = run_harness(project, "stop-hook")

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_only_the_new_complex_function_blocks_and_only_once(self):
        # The project sits in a subdirectory; the change is committed on a feature branch.
        legacy = branchy("legacy", 20)
        with project_repo("proj") as (root, project):
            write(project, "src/app.py", module(legacy, HELPER))
            commit(root)
            git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(root, "checkout", "-q", "-b", "feature")
            write(project, "src/app.py", module(legacy, HELPER, branchy("busy", 20)))
            commit(root)
            first = run_harness(project, "stop-hook")
            active = run_harness(project, "stop-hook", stdin='{"stop_hook_active": true}')

        payload = ["stop-hook failed: Complexity", "src/app.py:52: busy CCN 21 (limit 15)"]
        self.assertEqual(
            (first.returncode, first.stdout, first.stderr.splitlines()), (2, "", payload)
        )
        notice = "harness: already blocked once on this stop; not blocking again"
        self.assertEqual((active.returncode, active.stderr.splitlines()), (1, [*payload, notice]))

    def test_post_edit_hook_reports_a_reformat_only(self):
        with project_repo() as (_, project):
            write(project, "src/app.py", "x=1\n")
            write(project, "src/clean.py", "x = 1\n")
            messy, clean, outside, garbage = (
                run_harness(project, "post-edit", "--hook", stdin=stdin)
                for stdin in (
                    json.dumps({"tool_input": {"file_path": str(project / "src" / "app.py")}}),
                    json.dumps({"tool_input": {"file_path": "src/clean.py"}}),
                    json.dumps({"tool_input": {"file_path": str(HARNESS)}}),
                    "{not json",
                )
            )
            fixed = (project / "src" / "app.py").read_text(encoding="utf-8")

        self.assertEqual(
            (messy.returncode, messy.stdout),
            (
                0,
                '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":'
                '"harness: reformatted src/app.py; re-read it before editing it again"}}\n',
            ),
        )
        self.assertEqual(fixed, "x = 1\n")
        for result in (clean, outside, garbage):
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_pre_commit_stages_the_mirror_of_a_staged_claude_md(self):
        with project_repo() as (root, project):
            write(project, "CLAUDE.md", "v1\n")
            write(project, "AGENTS.md", "v1\n")
            commit(root)
            write(project, "CLAUDE.md", "v2\n")
            git(root, "add", "CLAUDE.md")
            result = run_harness(project, "pre-commit")
            staged = git(root, "diff", "--cached", "--name-only").split()
            mirror = (project / "AGENTS.md").read_text(encoding="utf-8")

        self.assertEqual(
            (result.returncode, staged, mirror), (0, ["AGENTS.md", "CLAUDE.md"], "v2\n")
        )


if __name__ == "__main__":
    unittest.main()
