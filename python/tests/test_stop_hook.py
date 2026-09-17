"""Stop hook, PostToolUse hook, and the hook-adjacent pre-commit/pre-push rules.

Pure helpers get unit tests. The exit contract gets a few end-to-end runs of the real
CLI against throwaway git repos: silent 0, exit 2 with a stderr payload, exit 1 for a
tool failure or a repeated block.
"""

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
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

import harness

HARNESS = Path(harness.__file__).resolve()
TEMPLATE = HARNESS.parent
GIT = shutil.which("git") or "git"
GIT_IDENTITY = ["-c", "user.email=harness@example.com", "-c", "user.name=Harness"]


def git(root, *args):
    return subprocess.run(
        [GIT, "-C", str(root), *GIT_IDENTITY, "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
        env=harness_env(),
    ).stdout


def harness_env():
    """No git hook variables and no base or guard overrides from the ambient shell."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "HARNESS_")) and key != "GITHUB_BASE_REF"
    }


def branchy(name, branches):
    """A function whose CCN is `branches + 1`."""
    body = "".join(f"    if value == {i}:\n        return {i}\n" for i in range(branches))
    return f"def {name}(value):\n{body}    return -1\n"


def module(*blocks):
    """A ruff-formatted module exporting its top-level names (so vulture sees them used)."""
    found = re.findall(r"^(?:def )?([A-Za-z_]\w*)(?=\(| =)", "".join(blocks), re.M)
    names = ", ".join(f'"{name}"' for name in found)
    return "\n\n".join([f"__all__ = [{names}]\n", *blocks])


HELPER = "def helper():\n    return 1\n"


@contextmanager
def project_repo(subdir=""):
    """A git repo whose project (optionally in `subdir`) has one committed src module."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q", "-b", "main")
        project = root / subdir
        (project / "src").mkdir(parents=True)
        yield root, project


def commit(root, message="change"):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def write(project, relative, text):
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_harness(project, *args, stdin=""):
    return subprocess.run(
        [sys.executable, str(HARNESS), *args],
        cwd=project,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env=harness_env(),
        timeout=120,
    )


def feature_branch(root, project, base_text):
    """Commit `base_text` as src/app.py on main, mark it origin/main, branch off."""
    write(project, "src/app.py", base_text)
    commit(root, "base")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "checkout", "-q", "-b", "feature")


# ── Pure helpers ──────────────────────────────────────────────────


class TestParseDiffRanges(unittest.TestCase):
    DIFF = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index 1..2 100644\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,0 +2 @@ x\n"
        "+y\n"
        "@@ -10,2 +11,3 @@ def f():\n"
        "+++ looks like a header but is an added line\n"
        "+b\n"
        "+c\n"
        "@@ -20,4 +22,0 @@\n"
        "-gone\n"
        "diff --git a/old.py b/old.py\n"
        "deleted file mode 100644\n"
        "--- a/old.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-x\n"
        "diff --git a/sp ace.py b/sp ace.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/sp ace.py\t\n"
        "@@ -0,0 +1,4 @@\n"
        "+a\n"
        "diff --git a/only_deleted.py b/only_deleted.py\n"
        "--- a/only_deleted.py\n"
        "+++ b/only_deleted.py\n"
        "@@ -3 +2,0 @@\n"
        "-x\n"
    )

    def test_parses_ranges_per_file(self):
        self.assertEqual(
            harness._parse_diff_ranges(self.DIFF),
            {
                "src/app.py": [(2, 2), (11, 13)],
                "sp ace.py": [(1, 4)],
                "only_deleted.py": [],
            },
        )

    @given(
        hunks=st.lists(
            st.tuples(st.integers(min_value=1, max_value=10_000), st.integers(0, 50)),
            max_size=20,
        )
    )
    def test_hunk_headers_round_trip(self, hunks):
        body = "".join(f"@@ -1 +{start},{count} @@\n" for start, count in hunks)
        diff = f"diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n{body}"
        expected = [(start, start + count - 1) for start, count in hunks if count]
        self.assertEqual(harness._parse_diff_ranges(diff), {"f.py": expected})


class TestLizardCsv(unittest.TestCase):
    CSV = (
        "NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end\n"
        '3,1,66,3,4,"run@97-100@src/a.py","src/a.py","run",'
        '"run( a : int , b : str | None = None )",97,100\n'
        '2,1,13,1,2,"ok@4-5@src/a.py","src/a.py","ok","ok( self )",4,5\n'
        '2,2,13,1,2,"ok@9-10@src/a.py","src/a.py","ok","ok( self )",9,10\n'
        "garbage\n"
    )

    def test_keys_by_long_name_and_numbers_repeats(self):
        parsed = harness._parse_lizard_csv(self.CSV)
        self.assertEqual(
            list(parsed["src/a.py"]),
            ["run( a : int , b : str | None = None )", "ok( self )", "ok( self )#2"],
        )
        self.assertEqual(
            parsed["src/a.py"]["run( a : int , b : str | None = None )"],
            harness.FunctionMetrics("run", 97, 1, 3, 4),
        )
        self.assertEqual(parsed["src/a.py"]["ok( self )#2"].ccn, 2)


def fn(name="busy", *, ccn=1, args=1, length=5, line=1):
    return harness.FunctionMetrics(name, line, ccn, args, length)


class TestComplexityDelta(unittest.TestCase):
    def delta(self, now, was=None, *, now_key="busy( v )", was_key="busy( v )"):
        base = {} if was is None else {"a.py": {was_key: was}}
        return harness._complexity_delta({"a.py": {now_key: now}}, base)

    def test_new_function_over_limit(self):
        self.assertEqual(self.delta(fn(ccn=17, line=3)), ["a.py:3: busy CCN new→17 (limit 15)"])

    def test_worse_than_base(self):
        self.assertEqual(self.delta(fn(ccn=17), fn(ccn=14)), ["a.py:1: busy CCN 14→17 (limit 15)"])
        self.assertEqual(self.delta(fn(ccn=21), fn(ccn=20)), ["a.py:1: busy CCN 20→21 (limit 15)"])

    def test_unchanged_better_or_under_limit_pass(self):
        self.assertEqual(self.delta(fn(ccn=20), fn(ccn=20)), [])
        self.assertEqual(self.delta(fn(ccn=18), fn(ccn=20)), [])
        self.assertEqual(self.delta(fn(ccn=15)), [])

    def test_args_and_length_limits(self):
        self.assertEqual(
            self.delta(fn(args=9, length=101)),
            [
                "a.py:1: busy args new→9 (limit 8)",
                "a.py:1: busy length new→101 (limit 100)",
            ],
        )

    def test_signature_edit_falls_back_to_the_unique_name(self):
        now, was = fn(ccn=20), fn(ccn=20)
        self.assertEqual(self.delta(now, was, now_key="busy( v : int )"), [])

    def test_ambiguous_name_counts_as_new(self):
        current = {"a.py": {"busy( v : int )": fn(ccn=20), "busy( self )": fn(ccn=2)}}
        base = {"a.py": {"busy( v )": fn(ccn=20)}}
        self.assertEqual(
            harness._complexity_delta(current, base), ["a.py:1: busy CCN new→20 (limit 15)"]
        )


SCOPE = {"src/a.py": [(2, 3)], "src/new.py": list(harness.WHOLE_FILE)}


class TestToolOutputFilters(unittest.TestCase):
    def test_ruff_findings_keep_changed_lines(self):
        root = Path(tempfile.gettempdir())

        def diagnostic(path, row, code, message):
            filename = str(root / path)
            return {
                "filename": filename,
                "location": {"row": row},
                "code": code,
                "message": message,
            }

        report = json.dumps([
            diagnostic("src/a.py", 3, "F821", "Undefined name `x`"),
            diagnostic("src/a.py", 9, "F821", "old"),
            diagnostic("src/new.py", 1, None, "SyntaxError: bad"),
        ])
        self.assertEqual(
            harness._ruff_findings(report, SCOPE, root),
            ["src/a.py:3: F821 Undefined name `x`", "src/new.py:1: SyntaxError: bad"],
        )

    def test_unreadable_ruff_output_is_a_tool_error(self):
        for report in ("not json", '[{"filename": "x"}]'):
            with self.subTest(report=report), self.assertRaises(harness.ToolError):
                harness._ruff_findings(report, SCOPE, Path.cwd())

    def test_vulture_findings_keep_changed_lines(self):
        output = (
            "src/a.py:2: unused function 'f' (60% confidence)\n"
            "src/a.py:7: unused function 'g' (60% confidence)\n"
            "src/other.py:2: unused import 'os' (90% confidence)\n"
        )
        self.assertEqual(
            harness._vulture_findings(output, SCOPE),
            ["src/a.py:2: unused function 'f' (60% confidence)"],
        )


class TestRunTool(unittest.TestCase):
    def test_missing_tool_is_a_tool_error(self):
        with self.assertRaisesRegex(harness.ToolError, "^nope not runnable"):
            harness._run_tool("nope", ["/nonexistent/harness-tool"])

    def test_unexpected_exit_names_the_last_stderr_line(self):
        cmd = [sys.executable, "-c", "import sys; sys.stderr.write('a\\nboom\\n'); sys.exit(4)"]
        with self.assertRaisesRegex(harness.ToolError, "^py exited 4: boom$"):
            harness._run_tool("py", cmd)
        self.assertEqual(harness._run_tool("py", cmd, ok=(4,)), "")


class TestPayloadAndExit(unittest.TestCase):
    def test_payload_names_failed_gates_and_caps_findings(self):
        results = [
            harness.DeltaResult("Lint", [f"a.py:{n}: E1 x" for n in range(1, 24)]),
            harness.DeltaResult("Complexity", []),
            harness.DeltaResult("Dead code", ["b.py:1: unused"], ""),
        ]
        lines = harness._stop_hook_payload(results).splitlines()
        self.assertEqual(lines[0], "stop-hook failed: Lint, Dead code")
        self.assertEqual(len(lines), 22)
        self.assertEqual(lines[20], "a.py:20: E1 x")
        self.assertEqual(lines[21], "… +4 more — run `uv run harness stop-hook --verbose`")

    def test_verbose_lifts_the_cap(self):
        findings = [f"a.py:{n}: x" for n in range(30)]
        with mock.patch.object(harness, "VERBOSE", True):
            self.assertEqual(harness._cap_findings(findings), findings)

    def test_twenty_findings_need_no_more_line(self):
        findings = [f"a.py:{n}: x" for n in range(20)]
        self.assertEqual(harness._cap_findings(findings), findings)

    def test_clean_results_have_no_payload(self):
        results = [harness.DeltaResult("Lint", []), harness.DeltaResult("Complexity", [], "boom")]
        self.assertEqual(harness._stop_hook_payload(results), "")

    def test_exit_codes(self):
        digest = harness._payload_digest("P")
        active = {"stop_hook_active": True}
        cases = [
            ("", 0, {}, "", 0),
            ("", 1, {}, "", 1),
            ("P", 0, {}, "", 2),
            ("P", 1, {}, "", 2),  # a crashed gate never hides another gate's findings
            ("P", 0, active, "", 2),
            ("P", 0, {}, digest, 2),  # a new turn blocks again on the same findings
            ("P", 0, active, digest, 1),
            ("Q", 0, active, digest, 2),  # the findings changed: block again
            ("P", 0, {"stop_hook_active": "true"}, digest, 2),
        ]
        for payload, failed, event, stored, expected in cases:
            with self.subTest(payload=payload, failed=failed, event=event, stored=stored):
                self.assertEqual(harness._stop_hook_exit(payload, failed, event, stored), expected)

    def test_loop_guard_key(self):
        self.assertEqual(harness._loop_guard_key(""), "root")
        self.assertEqual(harness._loop_guard_key("python"), "python")
        self.assertEqual(harness._loop_guard_key("apps/my app"), "apps-my-app")


class TestHookInput(unittest.TestCase):
    def test_hook_target_resolves_inside_the_project_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "src/app.py", "")
            write(root, "src/data.txt", "")
            write(root, "docs/tool.py", "")
            absolute = str(root / "src" / "app.py")
            cases = [
                ({"tool_input": {"file_path": absolute}}, "src/app.py"),
                ({"tool_input": {"file_path": "src/app.py"}}, "src/app.py"),
                ({"tool_input": {"file_path": "src/missing.py"}}, None),
                ({"tool_input": {"file_path": "src/data.txt"}}, None),
                ({"tool_input": {"file_path": "docs/tool.py"}}, None),
                ({"tool_input": {"file_path": "../elsewhere/src/app.py"}}, None),
                ({"tool_input": {"file_path": ""}}, None),
                ({"tool_input": {"file_path": 3}}, None),
                ({"tool_input": "src/app.py"}, None),
                ({}, None),
            ]
            for event, expected in cases:
                with self.subTest(event=event):
                    self.assertEqual(harness._hook_target(event, root), expected)

    def test_hook_event_tolerates_bad_input(self):
        cases = [
            (b'{"stop_hook_active": true}', {"stop_hook_active": True}),
            (b"", {}),
            (b"not json", {}),
            (b"[1, 2]", {}),
        ]
        for data, expected in cases:
            read_fd, write_fd = os.pipe()
            os.write(write_fd, data)
            os.close(write_fd)
            with (
                self.subTest(data=data),
                os.fdopen(read_fd, "rb") as reader,
                mock.patch.object(harness.sys, "stdin", reader),
            ):
                self.assertEqual(harness._hook_event(), expected)

    def test_hook_event_ignores_a_stdin_without_a_descriptor(self):
        with mock.patch.object(harness.sys, "stdin", io.StringIO("{}")):
            self.assertEqual(harness._hook_event(), {})


class TestHookWiring(unittest.TestCase):
    def test_installing_into_the_committed_settings_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for wiring in harness.HOOK_WIRINGS:
                write(root, wiring.path, (TEMPLATE / wiring.path).read_text(encoding="utf-8"))
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                for wiring in harness.HOOK_WIRINGS:
                    harness._install_hook(wiring)
            finally:
                os.chdir(old_cwd)
            for wiring in harness.HOOK_WIRINGS:
                with self.subTest(path=wiring.path, event=wiring.event):
                    self.assertEqual(
                        (root / wiring.path).read_bytes(), (TEMPLATE / wiring.path).read_bytes()
                    )

    def test_install_replaces_a_legacy_handler_and_keeps_others(self):
        settings = {
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "uv run harness stop-hook"}]}],
                "PostToolUse": [
                    {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo hi"}]}
                ],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                write(Path(tmp), harness.CLAUDE_SETTINGS, json.dumps(settings))
                for _ in range(2):
                    for wiring in harness.HOOK_WIRINGS[:2]:
                        harness._install_hook(wiring)
                data = json.loads(Path(harness.CLAUDE_SETTINGS).read_text(encoding="utf-8"))
            finally:
                os.chdir(old_cwd)

        self.assertEqual(data["hooks"]["Stop"], [{"hooks": [harness.CLAUDE_STOP_HOOK]}])
        self.assertEqual(
            data["hooks"]["PostToolUse"],
            [
                {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo hi"}]},
                {"matcher": "Edit|Write", "hooks": [harness.CLAUDE_POST_EDIT_HOOK]},
            ],
        )

    def test_presence_check_flags_missing_post_tool_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop_only = {"hooks": {"Stop": [{"hooks": [harness.CLAUDE_STOP_HOOK]}]}}
            write(root, harness.CLAUDE_SETTINGS, json.dumps(stop_only))
            write(root, ".codex/hooks.json", "not json")
            old_cwd = Path.cwd()
            os.chdir(root)
            output = io.StringIO()
            try:
                with redirect_stdout(output):
                    harness._check_stop_hooks_present()
            finally:
                os.chdir(old_cwd)

        text = output.getvalue()
        self.assertIn("✓\x1b[0m Stop hook wiring (.claude/settings.json)", text)
        self.assertIn("Missing PostToolUse hook wiring: .claude/settings.json", text)
        self.assertIn("Missing Stop hook wiring: .codex/hooks.json", text)


# ── Command wiring ────────────────────────────────────────────────


class TestCommandOrder(unittest.TestCase):
    def test_stop_hook_reads_event_then_formats_syncs_and_gates(self):
        calls = []

        def record(label, value=None):
            def side_effect(*args):
                calls.append(" ".join([label, *map(str, args)]))
                return value

            return side_effect

        with (
            mock.patch.object(harness, "_hook_event", side_effect=record("event", {})),
            mock.patch.object(harness, "_changed_py_files", return_value=["src/a.py"]),
            mock.patch.object(harness, "_fix_and_format", side_effect=record("fix")),
            mock.patch.object(harness, "_sync_agents_md_after_edit", side_effect=record("sync")),
            mock.patch.object(harness, "_delta_base", return_value="abc"),
            mock.patch.object(harness, "_changed_scope", side_effect=record("scope", {})),
            mock.patch.object(harness, "_run_delta_gates", side_effect=record("gates", [])),
            mock.patch.object(harness, "_report_stop_hook", return_value=0),
        ):
            harness.cmd_stop_hook()

        self.assertEqual(calls, ["event", "fix ['src/a.py']", "sync", "scope abc", "gates {} abc"])

    def test_pre_commit_no_longer_runs_tests(self):
        with (
            mock.patch.object(harness, "_check_arch_config_guard"),
            mock.patch.object(harness, "_sync_agents_md_staged"),
            mock.patch.object(harness, "_staged_py_files", return_value=["src/app.py"]),
            mock.patch.object(harness, "cmd_fix"),
            mock.patch.object(harness, "cmd_format"),
            mock.patch.object(harness, "cmd_typecheck"),
            mock.patch.object(harness, "_check_agents_md_drift"),
            mock.patch.object(harness, "cmd_test") as cmd_test,
            mock.patch.object(harness, "_check_tests") as check_tests,
            redirect_stdout(io.StringIO()),
        ):
            harness.cmd_pre_commit()

        cmd_test.assert_not_called()
        check_tests.assert_not_called()

    def test_pre_push_runs_tests_without_git_hook_env(self):
        gate = harness.Gate("Tests", ["uv", "run", "python", "-m", "unittest"])
        result = harness.GateResult("Tests", gate.cmd, 0, "", "")
        hook_env = {"GIT_DIR": "/repo/.git", "GIT_INDEX_FILE": "/repo/.git/index", "KEEP": "1"}
        with (
            mock.patch.dict(os.environ, hook_env),
            mock.patch.object(harness, "_check_branch_guard", return_value=True),
            mock.patch.object(harness, "_check_arch_config_guard", return_value=True),
            mock.patch.object(harness, "_test_gate", return_value=gate),
            mock.patch.object(harness, "run_capture", return_value=result) as run_capture,
            mock.patch.object(harness, "run_gates_parallel", return_value=True),
            redirect_stdout(io.StringIO()),
        ):
            harness.cmd_pre_push()

        env = run_capture.call_args.kwargs["env"]
        self.assertEqual(run_capture.call_args.args[:2], ("Tests", gate.cmd))
        self.assertEqual(env["KEEP"], "1")
        self.assertFalse([key for key in env if key.startswith("GIT_")])

    def test_pre_push_fails_when_tests_fail(self):
        with (
            mock.patch.object(harness, "_check_branch_guard", return_value=True),
            mock.patch.object(harness, "_check_arch_config_guard", return_value=True),
            mock.patch.object(harness, "_check_tests", return_value=False),
            mock.patch.object(harness, "run_gates_parallel", return_value=True),
            redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            harness.cmd_pre_push()

        self.assertEqual(raised.exception.code, 1)


# ── AGENTS.md auto-sync ───────────────────────────────────────────


@contextmanager
def docs_repo():
    """A repo with CLAUDE.md and AGENTS.md committed in sync; cwd is the repo."""
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q", "-b", "main")
        write(root, "CLAUDE.md", "v1\n")
        write(root, "AGENTS.md", "v1\n")
        commit(root, "docs")
        os.chdir(root)
        try:
            with mock.patch.dict(os.environ, harness_env(), clear=True):
                yield root
        finally:
            os.chdir(old_cwd)


class TestAgentsMdSync(unittest.TestCase):
    def test_stop_hook_mirrors_an_uncommitted_claude_md(self):
        with docs_repo() as root:
            mirror = root / "AGENTS.md"
            mirror.write_text("v1 hand edit\n", encoding="utf-8")
            write(root, "CLAUDE.md", "v2\n")
            harness._sync_agents_md_after_edit()
            self.assertEqual(mirror.read_text(encoding="utf-8"), "v2\n")

            # After the first sync AGENTS.md is itself modified; later edits still sync.
            write(root, "CLAUDE.md", "v3\n")
            git(root, "add", "CLAUDE.md")
            harness._sync_agents_md_after_edit()
            self.assertEqual(mirror.read_text(encoding="utf-8"), "v3\n")

    def test_stop_hook_leaves_an_agents_md_only_edit(self):
        with docs_repo() as root:
            write(root, "AGENTS.md", "hand edit\n")
            harness._sync_agents_md_after_edit()
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "hand edit\n")

    def test_pre_commit_stages_the_mirror_of_a_staged_claude_md(self):
        with docs_repo() as root:
            write(root, "CLAUDE.md", "v2\n")
            git(root, "add", "CLAUDE.md")
            output = io.StringIO()
            with redirect_stdout(output):
                harness.cmd_pre_commit()
            staged = git(root, "diff", "--cached", "--name-only").split()

        self.assertEqual(staged, ["AGENTS.md", "CLAUDE.md"])
        self.assertIn("AGENTS.md ← CLAUDE.md (staged)", output.getvalue())

    def test_pre_commit_fails_on_a_mirror_edited_alone(self):
        with docs_repo() as root:
            write(root, "AGENTS.md", "hand edit\n")
            git(root, "add", "AGENTS.md")
            with (
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as raised,
            ):
                harness.cmd_pre_commit()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("AGENTS.md differs from CLAUDE.md", output.getvalue())

    def test_pre_commit_fails_on_a_hand_edited_mirror(self):
        with docs_repo() as root:
            write(root, "AGENTS.md", "hand edit\n")
            write(root, "src/app.py", "VALUE = 1\n")
            git(root, "add", "AGENTS.md", "src/app.py")
            with (
                mock.patch.object(harness, "cmd_fix"),
                mock.patch.object(harness, "cmd_format"),
                mock.patch.object(harness, "cmd_typecheck"),
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as raised,
            ):
                harness.cmd_pre_commit()
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "hand edit\n")

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("AGENTS.md differs from CLAUDE.md", output.getvalue())


# ── End to end: the real CLI on throwaway repos ────────────────────


class TestStopHookEndToEnd(unittest.TestCase):
    def test_clean_tree_is_silent(self):
        with project_repo() as (root, project):
            write(project, "src/app.py", module(HELPER))
            commit(root)
            result = run_harness(project, "stop-hook")

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_untouched_legacy_function_does_not_block(self):
        # The project sits in a subdirectory and the change is committed on a branch.
        with project_repo("proj") as (root, project):
            feature_branch(root, project, module(branchy("busy", 20), HELPER))
            write(project, "src/app.py", module(branchy("busy", 20), HELPER + "\n\nVALUE = 2\n"))
            commit(root)
            result = run_harness(project, "stop-hook")

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_new_complex_function_blocks_then_the_loop_guard_releases(self):
        with project_repo("proj") as (root, project):
            feature_branch(root, project, module(HELPER))
            write(project, "src/app.py", module(HELPER, branchy("busy", 20)))
            commit(root)
            active = json.dumps({"stop_hook_active": True})
            first = run_harness(project, "stop-hook", stdin=active)
            second = run_harness(project, "stop-hook", stdin=active)
            state = Path(git(root, "rev-parse", "--absolute-git-dir").strip()) / "harness"
            state_files = sorted(path.name for path in state.iterdir())

            write(project, "src/app.py", module(HELPER))  # fixed: clean stop forgets it
            third = run_harness(project, "stop-hook", stdin=active)
            state_after = sorted(path.name for path in state.iterdir())

        self.assertEqual((first.returncode, first.stdout), (2, ""))
        self.assertEqual(
            first.stderr.splitlines(),
            ["stop-hook failed: Complexity", "src/app.py:8: busy CCN new→21 (limit 15)"],
        )
        self.assertEqual((second.returncode, second.stdout), (1, ""))
        self.assertEqual(
            second.stderr.splitlines(), [*first.stderr.splitlines(), harness.LOOP_GUARD_NOTICE]
        )
        self.assertEqual(state_files, ["stop-hook-proj"])
        self.assertEqual((third.returncode, third.stderr), (0, ""))
        self.assertEqual(state_after, [])

    def test_worse_blocks_and_better_passes(self):
        with project_repo() as (root, project):
            feature_branch(root, project, module(branchy("busy", 16)))
            write(project, "src/app.py", module(branchy("busy", 18)))
            worse = run_harness(project, "stop-hook")
            write(project, "src/app.py", module(branchy("busy", 15)))
            better = run_harness(project, "stop-hook")

        self.assertEqual(worse.returncode, 2)
        self.assertIn("src/app.py:4: busy CCN 17→19 (limit 15)", worse.stderr)
        self.assertEqual((better.returncode, better.stderr), (0, ""))

    def test_lint_blocks_on_changed_lines_only(self):
        lint = "def lint():\n    return missing_name\n"
        with project_repo() as (root, project):
            write(project, "src/app.py", module(HELPER))
            commit(root)
            write(project, "src/app.py", module(HELPER, lint))
            changed = run_harness(project, "stop-hook")
            commit(root)
            write(project, "src/app.py", module(HELPER, lint, "VALUE = 2\n"))
            unchanged = run_harness(project, "stop-hook")

        self.assertEqual(changed.returncode, 2)
        self.assertEqual(
            changed.stderr.splitlines(),
            ["stop-hook failed: Lint", "src/app.py:9: F821 Undefined name `missing_name`"],
        )
        self.assertEqual((unchanged.returncode, unchanged.stderr), (0, ""))

    def test_tool_failure_exits_1_not_2(self):
        with project_repo() as (root, project):
            write(project, "src/app.py", module(HELPER))
            commit(root)
            write(project, "ruff.toml", "[lint]\nselect = 5\n")
            write(project, "src/app.py", module(HELPER, "VALUE = 2\n"))
            result = run_harness(project, "stop-hook")

        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertRegex(result.stderr, r"^stop-hook: Lint could not run: ruff exited 2: ")
        self.assertNotIn("stop-hook failed", result.stderr)


class TestPostEditHookEndToEnd(unittest.TestCase):
    def test_reformatted_file_asks_for_a_re_read(self):
        with project_repo() as (_, project):
            write(project, "src/app.py", "x=1\n")
            write(project, "src/clean.py", "x = 1\n")
            event = {"tool_input": {"file_path": str(project / "src" / "app.py")}}
            messy = run_harness(project, "post-edit", "--hook", stdin=json.dumps(event))
            event = {"tool_input": {"file_path": "src/clean.py"}}
            clean = run_harness(project, "post-edit", "--hook", stdin=json.dumps(event))
            event = {"tool_input": {"file_path": str(TEMPLATE / "harness.py")}}
            outside = run_harness(project, "post-edit", "--hook", stdin=json.dumps(event))
            garbage = run_harness(project, "post-edit", "--hook", stdin="{not json")
            fixed = (project / "src" / "app.py").read_text(encoding="utf-8")

        self.assertEqual(messy.returncode, 0)
        self.assertEqual(
            messy.stdout,
            '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":'
            '"harness: reformatted src/app.py; re-read it before editing it again"}}\n',
        )
        self.assertEqual(fixed, "x = 1\n")
        for result in (clean, outside, garbage):
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
