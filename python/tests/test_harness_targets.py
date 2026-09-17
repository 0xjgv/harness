"""Tests for harness target discovery and no-test behavior."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

import harness


@contextmanager
def temp_project(*, with_tests=False):
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "harness.py").write_text("# noqa: E501\n", encoding="utf-8")
        if with_tests:
            (root / "tests").mkdir()
            (root / "tests" / "test_app.py").write_text(
                "import unittest\n\n"
                "class TestApp(unittest.TestCase):\n"
                "    def test_smoke(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
        os.chdir(root)
        try:
            yield root
        finally:
            os.chdir(old_cwd)


GIT = shutil.which("git") or "git"
GIT_IDENTITY = ["-c", "user.email=harness@example.com", "-c", "user.name=Harness"]
GUARD_ENV_VARS = (
    "HARNESS_ALLOW_PROTECTED_PUSH",
    "HARNESS_ALLOW_ARCH_CONFIG",
    "HARNESS_PRE_PUSH_REFS",
)


@contextmanager
def clean_env(**overrides):
    """Environment without the guard overrides an ambient shell may have set."""
    env = {k: v for k, v in os.environ.items() if k not in GUARD_ENV_VARS}
    env.update(overrides)
    with mock.patch.dict(os.environ, env, clear=True):
        yield


@contextmanager
def stdin_pipe(data=b"", *, close=True):
    """Stand in for the git hook's stdin: a pipe, optionally left open after `data`."""
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb")
    try:
        if data:
            os.write(write_fd, data)
        if close:
            os.close(write_fd)
        with mock.patch.object(harness.sys, "stdin", reader):
            yield
    finally:
        if not close:
            os.close(write_fd)
        reader.close()


def git(root, *args):
    subprocess.run(
        [GIT, "-C", str(root), *GIT_IDENTITY, "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
    )


@contextmanager
def temp_git_repo():
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-q")
        os.chdir(root)
        try:
            yield root
        finally:
            os.chdir(old_cwd)


class TestTargetHelpers(unittest.TestCase):
    def test_quality_and_app_targets_filter_existing_paths(self):
        with temp_project(with_tests=True):
            self.assertEqual(harness._quality_targets(), ["src", "harness.py", "tests"])
            self.assertEqual(harness._quality_targets(include_tests=False), ["src", "harness.py"])
            self.assertEqual(harness._app_targets(), ["src"])
            self.assertEqual(harness._app_targets(include_tests=True), ["src", "tests"])

    def test_iter_python_files_walks_files_and_directories(self):
        with temp_project(with_tests=True):
            files = {str(path) for path in harness._iter_python_files(["harness.py", "src"])}

        self.assertEqual(files, {"harness.py", "src/app.py"})

    def test_has_tests_requires_test_file(self):
        with temp_project(with_tests=False) as root:
            self.assertFalse(harness._has_tests())
            (root / "tests").mkdir()
            (root / "tests" / "helper.py").write_text("HELPER = True\n", encoding="utf-8")
            self.assertFalse(harness._has_tests())
            (root / "tests" / "test_app.py").write_text("def test_app(): pass\n", encoding="utf-8")
            self.assertTrue(harness._has_tests())

    def test_project_file_predicates(self):
        self.assertTrue(harness._is_project_python_file("src/app.py"))
        self.assertTrue(harness._is_project_python_file("tests/test_app.py"))
        self.assertTrue(harness._is_project_python_file("harness.py"))
        self.assertFalse(harness._is_project_python_file("docs/tool.py"))
        self.assertFalse(harness._is_project_python_file("src/data.txt"))

    def test_default_suppression_scan_includes_harness(self):
        with temp_project(with_tests=False):
            results = harness._scan_suppressions()

        self.assertEqual(results.get("noqa"), [["E501"]])


class TestGitFileFiltering(unittest.TestCase):
    def test_staged_py_files_keep_project_paths_only(self):
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="src/app.py\ntests/test_app.py\nharness.py\ndocs/tool.py\nsrc/data.txt\n",
            stderr="",
        )
        with mock.patch.object(harness.subprocess, "run", return_value=result):
            self.assertEqual(
                harness._staged_py_files(),
                ["src/app.py", "tests/test_app.py", "harness.py"],
            )

    def test_changed_py_files_skip_deleted_and_keep_rename_target(self):
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                " M src/app.py\n"
                " D src/deleted.py\n"
                "?? tests/test_app.py\n"
                "R  old.py -> harness.py\n"
                " M docs/tool.py\n"
            ),
            stderr="",
        )
        with (
            mock.patch.object(harness.subprocess, "run", return_value=result),
            mock.patch.object(harness, "_git_prefix", return_value=""),
        ):
            self.assertEqual(
                harness._changed_py_files(),
                ["src/app.py", "tests/test_app.py", "harness.py"],
            )


class TestNoTestBehavior(unittest.TestCase):
    def test_test_command_falls_back_to_py_compile_without_tests(self):
        with temp_project(with_tests=False), mock.patch.object(harness, "run") as run_mock:
            harness.cmd_test()

        run_mock.assert_called_once()
        description, command = run_mock.call_args.args
        self.assertEqual(description, "Syntax check")
        self.assertEqual(command[:5], ["uv", "run", "python", "-m", "py_compile"])
        self.assertIn("harness.py", command)
        self.assertIn("src/app.py", command)

    def test_test_command_runs_unittest_when_tests_exist(self):
        with temp_project(with_tests=True), mock.patch.object(harness, "run") as run_mock:
            harness.cmd_test()

        run_mock.assert_called_once_with(
            "Tests",
            ["uv", "run", "python", "-m", "unittest", "discover", "-s", "tests", "-q"],
        )

    def test_warning_only_gates_skip_when_no_tests_exist(self):
        commands = [
            (harness.cmd_coverage, "Coverage: no tests/test*.py files; skipped"),
            (harness.cmd_mutation, "Mutation: no tests/test*.py files; skipped"),
            (harness.cmd_crap, "CRAP: no tests; skipped"),
        ]
        for command, expected in commands:
            with self.subTest(command=command.__name__), temp_project(with_tests=False):
                output = io.StringIO()
                with (
                    redirect_stdout(output),
                    mock.patch.object(harness, "run") as run_mock,
                    mock.patch.object(harness.subprocess, "run") as subprocess_run,
                ):
                    command()

                self.assertIn(expected, output.getvalue())
                run_mock.assert_not_called()
                subprocess_run.assert_not_called()


class TestBranchGuard(unittest.TestCase):
    MAIN_PUSH = "refs/heads/main abc123 refs/heads/main def456"
    FEATURE_PUSH = "refs/heads/feature abc123 refs/heads/feature def456"
    ZERO = "0000000000000000000000000000000000000000"

    def setUp(self):
        harness._pre_push_refs.cache_clear()
        self.addCleanup(harness._pre_push_refs.cache_clear)

    def test_ref_line_targeting_main_is_protected(self):
        self.assertEqual(harness._protected_push_branch([self.MAIN_PUSH], "feature/x"), "main")

    def test_ref_line_targeting_feature_branch_is_not_protected(self):
        self.assertIsNone(harness._protected_push_branch([self.FEATURE_PUSH], "main"))

    def test_deleting_a_protected_branch_is_protected(self):
        line = f"(delete) {self.ZERO} refs/heads/main def456"
        self.assertEqual(harness._protected_push_branch([line], "feature/x"), "main")

    def test_deleting_a_feature_branch_is_not_protected(self):
        line = f"(delete) {self.ZERO} refs/heads/feature def456"
        self.assertIsNone(harness._protected_push_branch([line], "feature/x"))

    def test_tag_refs_never_match(self):
        line = "refs/tags/v1 abc123 refs/tags/main def456"
        self.assertIsNone(harness._protected_push_branch([line], "feature/x"))

    def test_current_branch_decides_without_ref_lines(self):
        self.assertEqual(harness._protected_push_branch([], "main"), "main")
        self.assertEqual(harness._protected_push_branch([], "master"), "master")
        self.assertIsNone(harness._protected_push_branch([], "feature/x"))

    def test_override_env_passes_the_guard(self):
        output = io.StringIO()
        with (
            redirect_stdout(output),
            mock.patch.object(
                harness, "_pre_push_refs", return_value=harness.PrePushRefs((self.MAIN_PUSH,))
            ),
            clean_env(HARNESS_ALLOW_PROTECTED_PUSH="1"),
        ):
            self.assertTrue(harness._check_branch_guard())

        self.assertIn("Branch guard override: main", output.getvalue())

    def test_guard_fails_and_explains_without_override(self):
        output = io.StringIO()
        with (
            redirect_stdout(output),
            mock.patch.object(
                harness, "_pre_push_refs", return_value=harness.PrePushRefs((self.MAIN_PUSH,))
            ),
            clean_env(),
        ):
            self.assertFalse(harness._check_branch_guard())

        text = output.getvalue()
        self.assertIn("Push targets protected branch: main", text)
        self.assertIn(harness.PROTECTED_PUSH_ALLOW_ENV, text)

    def test_env_refs_win_over_stdin(self):
        with (
            clean_env(HARNESS_PRE_PUSH_REFS=self.MAIN_PUSH),
            mock.patch.object(harness, "_read_pre_push_stdin") as read_mock,
        ):
            refs = harness._pre_push_refs()

        self.assertEqual(refs.lines, (self.MAIN_PUSH,))
        read_mock.assert_not_called()

    def test_closed_empty_stdin_means_no_refs(self):
        with clean_env(), stdin_pipe(b""):
            refs = harness._pre_push_refs()

        self.assertEqual(refs, harness.PrePushRefs())

    def test_closed_stdin_yields_refs(self):
        with clean_env(), stdin_pipe(f"{self.MAIN_PUSH}\n".encode()):
            refs = harness._pre_push_refs()

        self.assertEqual(refs.lines, (self.MAIN_PUSH,))
        self.assertFalse(refs.incomplete)

    def test_idle_pipe_times_out_without_refs(self):
        with (
            clean_env(),
            mock.patch.object(harness, "PRE_PUSH_STDIN_TIMEOUT", 0.1),
            stdin_pipe(b"", close=False),
        ):
            refs = harness._pre_push_refs()

        self.assertEqual(refs, harness.PrePushRefs())

    def test_partial_input_is_incomplete(self):
        # Data arrived but the writer never closed: the deadline bounds the whole read.
        with (
            clean_env(),
            mock.patch.object(harness, "PRE_PUSH_STDIN_TIMEOUT", 0.1),
            stdin_pipe(self.FEATURE_PUSH.encode(), close=False),
        ):
            refs = harness._pre_push_refs()

        self.assertTrue(refs.incomplete)
        self.assertEqual(refs.lines, ())

    def test_incomplete_refs_fail_both_push_guards(self):
        output = io.StringIO()
        with (
            redirect_stdout(output),
            mock.patch.object(
                harness, "_pre_push_refs", return_value=harness.PrePushRefs(incomplete=True)
            ),
            clean_env(),
        ):
            self.assertFalse(harness._check_branch_guard())
            self.assertFalse(harness._check_arch_config_guard(include_pre_push_refs=True))

        self.assertEqual(output.getvalue().count("Pre-push refs incomplete after 1s"), 2)

    def test_stdin_is_read_once_and_shared(self):
        # The pipe hits EOF on the first read: a second resolution only works if the
        # first one was cached, and the arch guard consumes the very same refs.
        with clean_env(), stdin_pipe(f"{self.MAIN_PUSH}\n".encode()):
            first = harness._pre_push_refs()
            second = harness._pre_push_refs()
            with mock.patch.object(harness, "_git_lines", return_value=["src/app.py"]):
                paths = harness._changed_paths_from_pre_push_refs()

        self.assertEqual(first.lines, (self.MAIN_PUSH,))
        self.assertEqual(second.lines, first.lines)
        self.assertEqual(paths, ["src/app.py"])


class TestArchGuardPrePushRefs(unittest.TestCase):
    def setUp(self):
        harness._pre_push_refs.cache_clear()
        self.addCleanup(harness._pre_push_refs.cache_clear)

    def test_new_branch_push_reports_arch_config_from_the_whole_branch(self):
        # A branch the remote has never seen (zero remote sha): the arch config change
        # sits below the tip commit, so only a merge-base diff finds it.
        with temp_git_repo() as root:
            (root / "README.md").write_text("init\n", encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "init")
            first = harness._git_lines(["rev-parse", "HEAD"])[0]
            git(root, "update-ref", "refs/remotes/origin/main", first)
            git(root, "checkout", "-q", "-b", "feature")

            (root / ".importlinter").write_text("[importlinter]\n", encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "arch")
            (root / "other.txt").write_text("unrelated\n", encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "unrelated")
            tip = harness._git_lines(["rev-parse", "HEAD"])[0]

            refs = f"refs/heads/feature {tip} refs/heads/feature {'0' * 40}"
            with clean_env(HARNESS_PRE_PUSH_REFS=refs):
                changed = harness._changed_arch_configs(include_pre_push_refs=True)
            tip_only = harness._git_lines([
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                tip,
            ])

        self.assertEqual(changed, [".importlinter"])
        self.assertEqual(tip_only, ["other.txt"])  # the tip alone would have missed it

    def test_deleted_arch_config_is_detected_and_blocks_without_override(self):
        # --diff-filter=d hides deletions. A branch that deletes the protected config
        # (then adds an unrelated commit) must still be caught by the guard.
        with temp_git_repo() as root:
            (root / ".importlinter").write_text("[importlinter]\n", encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "init")
            first = harness._git_lines(["rev-parse", "HEAD"])[0]
            git(root, "update-ref", "refs/remotes/origin/main", first)
            git(root, "checkout", "-q", "-b", "feature")

            (root / ".importlinter").unlink()
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "delete arch config")
            (root / "other.txt").write_text("unrelated\n", encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "unrelated")
            tip = harness._git_lines(["rev-parse", "HEAD"])[0]

            refs = f"refs/heads/feature {tip} refs/heads/feature {'0' * 40}"
            output = io.StringIO()
            with clean_env(HARNESS_PRE_PUSH_REFS=refs), redirect_stdout(output):
                ok = harness._check_arch_config_guard(include_pre_push_refs=True)

        self.assertFalse(ok)
        self.assertIn(".importlinter", output.getvalue())


class TestPreCommitArchConfigOnly(unittest.TestCase):
    def test_staged_arch_config_only_warns_and_exits_zero(self):
        # cmd_pre_commit used to return early (no staged .py files) before the arch
        # guard ever ran, so an arch-config-only commit sailed through silently.
        with temp_git_repo() as root:
            (root / ".importlinter").write_text("[importlinter]\n", encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "init")

            (root / ".importlinter").write_text(
                "[importlinter]\nchanged = true\n", encoding="utf-8"
            )
            git(root, "add", "-A")

            output = io.StringIO()
            with clean_env(), redirect_stdout(output):
                harness.cmd_pre_commit()  # must not raise SystemExit

        text = output.getvalue()
        self.assertIn("Arch config changed", text)
        self.assertIn(".importlinter", text)
        self.assertIn("No staged Python files", text)


class TestParallelGates(unittest.TestCase):
    def test_all_gates_run_to_completion_on_seeded_failure(self):
        # A seeded failure in the middle must not short-circuit the batch: every
        # gate still reports, results print in submission order, and the overall
        # result is False.
        gates = [
            harness.Gate("first ok", ["true"]),
            harness.Gate("seeded fail", ["false"]),
            harness.Gate("last ok", ["true"]),
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            all_ok = harness.run_gates_parallel(gates)
        text = output.getvalue()

        self.assertFalse(all_ok)
        self.assertIn("first ok", text)
        self.assertIn("seeded fail", text)
        self.assertIn("last ok", text)
        self.assertLess(text.index("first ok"), text.index("last ok"))

    def test_empty_batch_passes(self):
        self.assertTrue(harness.run_gates_parallel([]))


if __name__ == "__main__":
    unittest.main()
