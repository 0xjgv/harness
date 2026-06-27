"""Tests for harness target discovery and no-test behavior."""

from __future__ import annotations

import io
import os
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
        self.assertTrue(harness._is_quality_python_file("src/app.py"))
        self.assertTrue(harness._is_quality_python_file("harness.py"))
        self.assertFalse(harness._is_quality_python_file("tests/test_app.py"))

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
        with mock.patch.object(harness.subprocess, "run", return_value=result):
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
            "Run tests",
            ["uv", "run", "python", "-m", "unittest", "discover", "-s", "tests", "-q"],
            stream=True,
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


class TestStopHook(unittest.TestCase):
    def test_stop_hook_runs_post_edit_then_parallel_batch_then_crap(self):
        calls: list[str] = []

        def record_batch(gates: list[harness.Gate]) -> bool:
            calls.append("batch:" + ",".join(gate.description for gate in gates))
            return True

        with (
            mock.patch.object(
                harness, "cmd_post_edit", side_effect=lambda: calls.append("post-edit")
            ),
            mock.patch.object(harness, "run_gates_parallel", side_effect=record_batch),
            mock.patch.object(harness, "cmd_crap", side_effect=lambda: calls.append("crap")),
        ):
            harness.cmd_stop_hook()

        # Mutating fix/format runs first and alone; the read-only complexity gate
        # runs through the parallel batch; CRAP streams last.
        self.assertEqual(calls, ["post-edit", "batch:Complexity (lizard)", "crap"])


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
