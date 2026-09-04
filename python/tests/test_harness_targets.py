"""Tests for harness target discovery and no-test behavior."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
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


def _fake_git_run(porcelain_stdout: str, prefix_stdout: str = ""):
    """Build a subprocess.run side_effect that answers `git status` and
    `git rev-parse --show-prefix` differently, matching how `_changed_py_files`
    calls both (status for the changes, rev-parse for the repo-root prefix)."""

    def side_effect(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=prefix_stdout, stderr=""
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=porcelain_stdout, stderr=""
        )

    return side_effect


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
        porcelain = (
            " M src/app.py\n"
            " D src/deleted.py\n"
            "?? tests/test_app.py\n"
            "R  old.py -> harness.py\n"
            " M docs/tool.py\n"
        )
        with mock.patch.object(harness.subprocess, "run", side_effect=_fake_git_run(porcelain)):
            self.assertEqual(
                harness._changed_py_files(),
                ["src/app.py", "tests/test_app.py", "harness.py"],
            )

    def test_changed_py_files_strips_git_root_prefix(self):
        # Regression: from a template subdirectory, `git status --porcelain` returns
        # repo-root-relative paths (e.g. "python/src/app.py"); without stripping the
        # `git rev-parse --show-prefix` prefix, _is_project_python_file never matches
        # and post-edit silently no-ops.
        porcelain = " M python/src/app.py\n?? bun/other.py\n"
        fake = _fake_git_run(porcelain, prefix_stdout="python/\n")
        with mock.patch.object(harness.subprocess, "run", side_effect=fake):
            self.assertEqual(harness._changed_py_files(), ["src/app.py"])

    def test_changed_py_files_at_repo_root_has_no_prefix(self):
        porcelain = " M src/app.py\n"
        fake = _fake_git_run(porcelain, prefix_stdout="")
        with mock.patch.object(harness.subprocess, "run", side_effect=fake):
            self.assertEqual(harness._changed_py_files(), ["src/app.py"])


class TestRestageFixedFiles(unittest.TestCase):
    def test_restages_only_existing_files(self):
        with temp_project(with_tests=False) as root:
            missing = "src/gone.py"
            existing = "src/app.py"
            self.assertTrue((root / existing).exists())
            self.assertFalse((root / missing).exists())

            with mock.patch.object(harness.subprocess, "run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
                harness._restage_fixed_files([existing, missing])

            run_mock.assert_called_once_with(["git", "add", "--", existing], check=False)

    def test_skips_git_add_when_nothing_exists(self):
        with mock.patch.object(harness.subprocess, "run") as run_mock:
            harness._restage_fixed_files(["does/not/exist.py"])

        run_mock.assert_not_called()


class TestChangedPathNormalization(unittest.TestCase):
    def test_normalize_strips_git_prefix_and_is_recognized(self):
        normalized = harness._normalize_changed_path("python/src/foo.py", "python")
        self.assertEqual(normalized, "src/foo.py")
        self.assertTrue(harness._is_project_python_file(normalized))

    def test_normalize_leaves_sibling_template_path_unrecognized(self):
        normalized = harness._normalize_changed_path("bun/x.py", "python")
        self.assertEqual(normalized, "bun/x.py")
        self.assertFalse(harness._is_project_python_file(normalized))

    def test_normalize_is_noop_with_empty_prefix(self):
        self.assertEqual(harness._normalize_changed_path("src/foo.py", ""), "src/foo.py")


class TestNoTestBehavior(unittest.TestCase):
    def test_test_command_falls_back_to_py_compile_without_tests(self):
        with temp_project(with_tests=False), mock.patch.object(harness, "run") as run_mock:
            harness.cmd_test()
            prefix = harness._python()

        run_mock.assert_called_once()
        description, command = run_mock.call_args.args
        self.assertEqual(description, "Syntax check")
        self.assertEqual(command[: len(prefix) + 2], [*prefix, "-m", "py_compile"])
        self.assertIn("harness.py", command)
        self.assertIn("src/app.py", command)

    def test_test_command_runs_unittest_when_tests_exist(self):
        with temp_project(with_tests=True), mock.patch.object(harness, "run") as run_mock:
            harness.cmd_test()
            expected = [*harness._python(), *harness.TEST_COMMAND]

        run_mock.assert_called_once_with("Tests", expected, no_exit=False)

    def test_test_command_constant_is_the_only_edit_a_pytest_repo_needs(self):
        """A pytest repo swaps TEST_COMMAND; both the test run and the coverage run
        follow, with no function body touched."""
        pytest_argv = ("-m", "pytest", "-q")
        with temp_project(with_tests=True):
            with (
                mock.patch.object(harness, "TEST_COMMAND", pytest_argv),
                mock.patch.object(harness, "run") as run_mock,
            ):
                harness.cmd_test()
            self.assertEqual(run_mock.call_args.args[1][-3:], list(pytest_argv))

            with (
                mock.patch.object(harness, "TEST_COMMAND", pytest_argv),
                mock.patch.object(harness, "run") as run_mock,
            ):
                harness.cmd_coverage()
            coverage_run_argv = run_mock.call_args_list[0].args[1]
            self.assertEqual(coverage_run_argv[-4:], ["run", *pytest_argv])

    def test_coverage_never_syncs_the_environment(self):
        """`ci` calls cmd_coverage, and `ci` is read-only: every coverage invocation
        must resolve through the read-only tool tier, which never creates `.venv`."""
        with temp_project(with_tests=True), mock.patch.object(harness, "run") as run_mock:
            harness.cmd_coverage()

        self.assertEqual(len(run_mock.call_args_list), 2)
        for call in run_mock.call_args_list:
            argv = call.args[1]
            if argv[:2] == ["uv", "run"]:
                self.assertIn("--no-sync", argv, msg=f"{argv} may sync .venv")
            else:
                self.assertNotIn("uv", argv[:1], msg=f"{argv} may create .venv")

    def test_warning_only_gates_skip_when_no_tests_exist(self):
        commands = [
            (harness.cmd_coverage, "Coverage: no tests/test*.py files; skipped"),
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

    def test_mutation_warns_and_skips_when_not_configured(self):
        # Mutation is unconfigured by default: it must warn and exit 0 without
        # shelling out, whether or not tests exist.
        for with_tests in (False, True):
            with self.subTest(with_tests=with_tests), temp_project(with_tests=with_tests):
                output = io.StringIO()
                with (
                    redirect_stdout(output),
                    mock.patch.object(harness, "run") as run_mock,
                    mock.patch.object(harness.subprocess, "run") as subprocess_run,
                ):
                    harness.cmd_mutation()

                self.assertIn("Mutation testing not configured", output.getvalue())
                run_mock.assert_not_called()
                subprocess_run.assert_not_called()


class TestStopHook(unittest.TestCase):
    def test_stop_hook_runs_post_edit_then_parallel_batch(self):
        calls: list[str] = []

        def record_batch(gates: list[harness.Gate]) -> tuple[bool, list[str]]:
            calls.append("batch:" + ",".join(gate.description for gate in gates))
            return True, []

        with (
            mock.patch.object(
                harness, "cmd_post_edit", side_effect=lambda: calls.append("post-edit")
            ),
            mock.patch.object(harness, "run_gates_parallel", side_effect=record_batch),
            mock.patch.object(
                harness,
                "_check_deadcode",
                side_effect=lambda **_: calls.append("deadcode") is None,
            ),
        ):
            harness.cmd_stop_hook()

        # Mutating fix/format runs first and alone; the read-only complexity gate runs
        # through the parallel batch, then the count-ratcheted dead-code check.
        self.assertEqual(calls, ["post-edit", "batch:Complexity (lizard)", "deadcode"])

    def test_stop_hook_exits_2_and_names_failed_gates_on_stderr(self):
        # Claude Code only treats exit code 2 as blocking, and only stderr is fed
        # back to the model — exit 1 / stdout-only output is silently swallowed.
        def failing_batch(gates: list[harness.Gate]) -> tuple[bool, list[str]]:
            return False, ["Complexity (lizard)"]

        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(harness, "cmd_post_edit"),
            mock.patch.object(harness, "_check_arch_config_guard", return_value=True),
            mock.patch.object(harness, "_check_gherkin_guard", return_value=True),
            mock.patch.object(harness, "run_gates_parallel", side_effect=failing_batch),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit) as ctx,
        ):
            harness.cmd_stop_hook()

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("stop-hook failed: Complexity (lizard)", stderr.getvalue())


class TestParallelGates(unittest.TestCase):
    def test_all_gates_run_to_completion_on_seeded_failure(self):
        # A seeded failure in the middle must not short-circuit the batch: every
        # gate still reports, results print in submission order, and the overall
        # result is False, naming the failed gate.
        gates = [
            harness.Gate("first ok", ["true"]),
            harness.Gate("seeded fail", ["false"]),
            harness.Gate("last ok", ["true"]),
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            all_ok, failed = harness.run_gates_parallel(gates)
        text = output.getvalue()

        self.assertFalse(all_ok)
        self.assertEqual(failed, ["seeded fail"])
        self.assertIn("first ok", text)
        self.assertIn("seeded fail", text)
        self.assertIn("last ok", text)
        self.assertLess(text.index("first ok"), text.index("last ok"))

    def test_empty_batch_passes(self):
        self.assertEqual(harness.run_gates_parallel([]), (True, []))


class TestArchConfigBaseFallback(unittest.TestCase):
    """GITHUB_BASE_REF is only set for pull_request events, so a direct push to
    main sets neither env var — _resolve_fallback_base/_changed_paths_from_base
    must still find a base to diff against, in a fixed candidate order."""

    def test_resolve_fallback_base_prefers_origin_head(self):
        def fake_git_lines(args):
            return [args[-1]] if args[-1] == "origin/HEAD" else []

        with mock.patch.object(harness, "_git_lines", side_effect=fake_git_lines):
            self.assertEqual(harness._resolve_fallback_base(), "origin/HEAD")

    def test_resolve_fallback_base_falls_through_to_origin_main(self):
        def fake_git_lines(args):
            return [args[-1]] if args[-1] == "origin/main" else []

        with mock.patch.object(harness, "_git_lines", side_effect=fake_git_lines):
            self.assertEqual(harness._resolve_fallback_base(), "origin/main")

    def test_resolve_fallback_base_falls_through_to_main(self):
        def fake_git_lines(args):
            return [args[-1]] if args[-1] == "main" else []

        with mock.patch.object(harness, "_git_lines", side_effect=fake_git_lines):
            self.assertEqual(harness._resolve_fallback_base(), "main")

    def test_resolve_fallback_base_returns_none_when_nothing_resolves(self):
        with mock.patch.object(harness, "_git_lines", return_value=[]):
            self.assertIsNone(harness._resolve_fallback_base())

    def test_changed_paths_from_base_uses_fallback_when_env_unset(self):
        calls: list[list[str]] = []

        def fake_git_lines(args):
            calls.append(args)
            if args[:2] == ["rev-parse", "--verify"]:
                return [args[-1]] if args[-1] == "origin/main" else []
            return ["src/app.py"] if args[0] == "diff" else []

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HARNESS_ARCH_BASE", None)
            os.environ.pop("GITHUB_BASE_REF", None)
            with mock.patch.object(harness, "_git_lines", side_effect=fake_git_lines):
                paths = harness._changed_paths_from_base()

        self.assertEqual(paths, ["src/app.py"])
        self.assertIn(
            ["diff", "--name-only", "--diff-filter=d", "origin/main...HEAD", "--", "."], calls
        )

    def test_changed_paths_from_base_skips_silently_when_nothing_resolves(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HARNESS_ARCH_BASE", None)
            os.environ.pop("GITHUB_BASE_REF", None)
            with mock.patch.object(harness, "_git_lines", return_value=[]):
                self.assertEqual(harness._changed_paths_from_base(), [])

    def test_changed_paths_from_base_prefers_explicit_env_over_fallback(self):
        calls: list[list[str]] = []

        def fake_git_lines(args):
            calls.append(args)
            if args[:2] == ["rev-parse", "--verify"]:
                return [args[-1]]
            return ["src/app.py"] if args[0] == "diff" else []

        with (
            mock.patch.dict(os.environ, {"HARNESS_ARCH_BASE": "custom-base"}, clear=False),
            mock.patch.object(harness, "_git_lines", side_effect=fake_git_lines),
        ):
            harness._changed_paths_from_base()

        rev_parse_calls = [c for c in calls if c[:2] == ["rev-parse", "--verify"]]
        self.assertEqual(rev_parse_calls, [["rev-parse", "--verify", "custom-base"]])


class TestCmdCheckSummary(unittest.TestCase):
    """`check` accumulates every step instead of failing fast (matching bun/go/rust)
    and always ends with an OK/FAIL summary line."""

    def _patch_check_steps(self, *, typecheck_ok=True):
        return [
            mock.patch.object(harness, "run", return_value=True),
            mock.patch.object(harness, "cmd_fix", return_value=True),
            mock.patch.object(harness, "cmd_format", return_value=True),
            mock.patch.object(harness, "cmd_typecheck", return_value=typecheck_ok),
            mock.patch.object(harness, "cmd_test", return_value=True),
            mock.patch.object(harness, "run_gates_parallel", return_value=(True, [])),
            # Pin the parallel batch to exactly one gate (complexity) so the summary
            # count is deterministic: `check` now tallies one result per gate.
            mock.patch.object(harness, "_acceptance_gates_or_warn", return_value=[]),
            mock.patch.object(harness, "_arch_gates_or_warn", return_value=[]),
            mock.patch.object(harness, "_check_stop_hooks_present"),
            mock.patch.object(harness, "_check_arch_config_guard", return_value=True),
            mock.patch.object(harness, "_check_gherkin_guard", return_value=True),
            mock.patch.object(harness, "_check_agents_md_drift", return_value=True),
            mock.patch.object(harness, "_check_suppressions_baseline", return_value=True),
            mock.patch.object(harness, "_check_deadcode", return_value=True),
        ]

    def test_check_prints_ok_summary_when_everything_passes(self):
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patcher in self._patch_check_steps():
                stack.enter_context(patcher)
            with redirect_stdout(output):
                harness.cmd_check()  # must not raise

        self.assertIn("OK", output.getvalue())
        self.assertIn("11 passed", output.getvalue())

    def test_check_exits_1_and_prints_fail_summary_on_gate_failure(self):
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patcher in self._patch_check_steps(typecheck_ok=False):
                stack.enter_context(patcher)
            with redirect_stdout(output), self.assertRaises(SystemExit) as ctx:
                harness.cmd_check()

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("FAIL", output.getvalue())
        self.assertIn("10 passed, 1 failed", output.getvalue())

    def test_check_runs_complexity_acceptance_arch_as_parallel_batch(self):
        # check must run every offline, fast, no-build-lock gate — complexity,
        # acceptance, and arch (import-linter: local dev dependency, offline, no
        # build lock) all run through the same read-only parallel batch stop-hook
        # uses. Dead code is count-ratcheted, so it runs outside the Gate batch.
        captured_gates = []

        def record_batch(gates):
            captured_gates.append([gate.description for gate in gates])
            return True, []

        with contextlib.ExitStack() as stack:
            for patcher in self._patch_check_steps():
                stack.enter_context(patcher)
            stack.enter_context(
                mock.patch.object(harness, "run_gates_parallel", side_effect=record_batch)
            )
            stack.enter_context(
                mock.patch.object(harness, "_acceptance_gates_or_warn", return_value=[])
            )
            stack.enter_context(
                mock.patch.object(
                    harness,
                    "_arch_gates_or_warn",
                    return_value=[
                        harness.Gate("Arch (import-linter)", ["uv", "run", "lint-imports"])
                    ],
                )
            )
            with redirect_stdout(io.StringIO()):
                harness.cmd_check()

        self.assertEqual(
            captured_gates,
            [["Complexity (lizard)", "Arch (import-linter)"]],
        )


class TestGherkinGuardDecision(unittest.TestCase):
    """Pure trigger logic — no git, no filesystem — mirroring the arch-config guard's
    changed-path matching but scoped to APP_SOURCES vs. `.feature` files."""

    def test_triggers_on_production_source_change_without_feature(self):
        self.assertTrue(harness._gherkin_guard_decision(["src/app.py"]))

    def test_passes_when_a_feature_file_also_changed(self):
        self.assertFalse(
            harness._gherkin_guard_decision(["src/app.py", "tests/features/thing.feature"])
        )

    def test_passes_when_no_production_source_changed(self):
        self.assertFalse(harness._gherkin_guard_decision(["tests/test_app.py", "README.md"]))

    def test_excludes_test_dir_and_runner_from_production_source(self):
        self.assertFalse(harness._gherkin_guard_decision(["tests/test_app.py", "harness.py"]))

    def test_empty_changeset_does_not_trigger(self):
        self.assertFalse(harness._gherkin_guard_decision([]))


class TestGherkinGuardCheck(unittest.TestCase):
    """`_check_gherkin_guard` orchestration: feature-file skip, override env, and
    warn-vs-block modes, with `_changed_paths` mocked so no git subprocess runs."""

    def test_skips_silently_when_template_has_no_feature_files(self):
        output = io.StringIO()
        with (
            mock.patch.object(harness, "_has_feature_files", return_value=False),
            mock.patch.object(harness, "_changed_paths") as changed_paths_mock,
            redirect_stdout(output),
        ):
            ok = harness._check_gherkin_guard()

        self.assertTrue(ok)
        self.assertEqual(output.getvalue(), "")
        changed_paths_mock.assert_not_called()

    def test_passes_when_feature_file_changed_alongside_source(self):
        with (
            mock.patch.object(harness, "_has_feature_files", return_value=True),
            mock.patch.object(
                harness,
                "_changed_paths",
                return_value=["src/app.py", "tests/features/x.feature"],
            ),
        ):
            self.assertTrue(harness._check_gherkin_guard())

    def test_blocks_when_production_source_changed_without_feature(self):
        output = io.StringIO()
        with (
            mock.patch.object(harness, "_has_feature_files", return_value=True),
            mock.patch.object(harness, "_changed_paths", return_value=["src/app.py"]),
            mock.patch.dict(os.environ, {}, clear=False),
            redirect_stdout(output),
        ):
            os.environ.pop(harness.GHERKIN_ALLOW_ENV, None)
            ok = harness._check_gherkin_guard()

        self.assertFalse(ok)
        self.assertIn("✗", output.getvalue())
        self.assertIn("src/app.py", output.getvalue())

    def test_warn_only_mode_passes_but_still_flags(self):
        output = io.StringIO()
        with (
            mock.patch.object(harness, "_has_feature_files", return_value=True),
            mock.patch.object(harness, "_changed_paths", return_value=["src/app.py"]),
            mock.patch.dict(os.environ, {}, clear=False),
            redirect_stdout(output),
        ):
            os.environ.pop(harness.GHERKIN_ALLOW_ENV, None)
            ok = harness._check_gherkin_guard(warn_only=True)

        self.assertTrue(ok)
        self.assertIn("⚠", output.getvalue())
        self.assertIn("src/app.py", output.getvalue())

    def test_override_env_passes(self):
        output = io.StringIO()
        with (
            mock.patch.object(harness, "_has_feature_files", return_value=True),
            mock.patch.object(harness, "_changed_paths", return_value=["src/app.py"]),
            mock.patch.dict(os.environ, {harness.GHERKIN_ALLOW_ENV: "1"}, clear=False),
            redirect_stdout(output),
        ):
            ok = harness._check_gherkin_guard()

        self.assertTrue(ok)
        self.assertIn("override", output.getvalue())
        self.assertIn("src/app.py", output.getvalue())


@contextmanager
def temp_git_project():
    """A real git repo with a base commit on `main`, cwd'd into.

    Scoping is git-derived, so the union of working tree + index + untracked +
    base-diff is only meaningful against a real repository. `origin/*` never
    resolves here, so `_base_ref()` falls through to `main` — no dependency on
    the developer's remotes.
    """
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.chdir(root)
        try:
            _git("init", "-b", "main")
            _git("config", "user.email", "harness@example.invalid")
            _git("config", "user.name", "Harness Test")
            (root / "src").mkdir()
            (root / "src" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
            _git("add", "-A")
            _git("commit", "-m", "base")
            yield root
        finally:
            os.chdir(old_cwd)


GIT = shutil.which("git") or "git"


def _git(*args):
    subprocess.run([GIT, *args], check=True, capture_output=True, text=True)


def _require_gate(gate: harness.Gate | None) -> harness.Gate:
    """Assert a scoped gate was built, narrowing `Gate | None` for the type checker."""
    if gate is None:
        msg = "expected a gate, got None — the scope resolved to nothing"
        raise AssertionError(msg)
    return gate


class TestScopedPyFiles(unittest.TestCase):
    """`_scoped_py_files` is the file list every scoped gate (fix/format/lint/
    typecheck) resolves. It must union every source of "changed", keep only project
    `.py` files, and never hand a vanished path to a tool."""

    def test_unions_working_tree_index_untracked_and_base_diff(self):
        with temp_git_project() as root:
            # working tree (unstaged modification)
            (root / "src" / "base.py").write_text("BASE = 2\n", encoding="utf-8")
            # index (staged addition)
            (root / "src" / "staged.py").write_text("STAGED = 1\n", encoding="utf-8")
            _git("add", "src/staged.py")
            # untracked
            (root / "src" / "untracked.py").write_text("UNTRACKED = 1\n", encoding="utf-8")
            # committed on a branch off main → only the base diff sees it
            _git("checkout", "-b", "feature")
            (root / "src" / "committed.py").write_text("COMMITTED = 1\n", encoding="utf-8")
            _git("add", "src/committed.py")
            _git("commit", "-m", "committed")

            scoped = harness._scoped_py_files()

        self.assertEqual(
            set(scoped),
            {"src/base.py", "src/staged.py", "src/untracked.py", "src/committed.py"},
        )
        # Every source contributes at most one entry — the union is deduplicated.
        self.assertEqual(len(scoped), len(set(scoped)))

    def test_staged_mode_sees_only_the_index(self):
        with temp_git_project() as root:
            (root / "src" / "staged.py").write_text("STAGED = 1\n", encoding="utf-8")
            _git("add", "src/staged.py")
            (root / "src" / "untracked.py").write_text("UNTRACKED = 1\n", encoding="utf-8")

            self.assertEqual(harness._scoped_py_files(staged=True), ["src/staged.py"])

    def test_filters_to_project_python_files(self):
        with temp_project():
            changed = ["src/app.py", "docs/tool.py", "src/data.txt", "harness.py"]
            with mock.patch.object(harness, "_changed_paths", return_value=changed):
                # docs/ is not a project target and .txt is not Python.
                self.assertEqual(harness._scoped_py_files(), ["src/app.py", "harness.py"])

    def test_drops_paths_that_no_longer_exist_on_disk(self):
        # The base diff can name a file the working tree has since deleted;
        # handing that to ruff is a hard error, not a lint finding.
        with (
            temp_project(),
            mock.patch.object(
                harness, "_changed_paths", return_value=["src/app.py", "src/deleted.py"]
            ),
        ):
            self.assertEqual(harness._scoped_py_files(), ["src/app.py"])

    def test_deduplicates_while_preserving_first_seen_order(self):
        with temp_project():
            changed = ["harness.py", "src/app.py", "harness.py"]
            with mock.patch.object(harness, "_changed_paths", return_value=changed):
                self.assertEqual(harness._scoped_py_files(), ["harness.py", "src/app.py"])


class TestBaseRef(unittest.TestCase):
    """One base ref feeds every changed-path consumer. Precedence:
    `--base=` → HARNESS_ARCH_BASE → GITHUB_BASE_REF → the fallback chain."""

    @contextmanager
    def _no_base_env(self, **extra):
        with mock.patch.dict(os.environ, extra, clear=False):
            for key in ("HARNESS_ARCH_BASE", "GITHUB_BASE_REF"):
                if key not in extra:
                    os.environ.pop(key, None)
            yield

    def test_cli_base_override_wins_over_everything(self):
        with (
            self._no_base_env(HARNESS_ARCH_BASE="from-env", GITHUB_BASE_REF="from-gh"),
            mock.patch.object(harness, "BASE_OVERRIDE", "from-cli"),
        ):
            self.assertEqual(harness._base_ref(), "from-cli")

    def test_env_base_wins_over_github_base_ref(self):
        with (
            self._no_base_env(HARNESS_ARCH_BASE="from-env", GITHUB_BASE_REF="from-gh"),
            mock.patch.object(harness, "BASE_OVERRIDE", None),
        ):
            self.assertEqual(harness._base_ref(), "from-env")

    def test_github_base_ref_is_qualified_with_origin(self):
        with (
            self._no_base_env(GITHUB_BASE_REF="release"),
            mock.patch.object(harness, "BASE_OVERRIDE", None),
        ):
            self.assertEqual(harness._base_ref(), "origin/release")

    def test_falls_back_through_the_candidate_chain(self):
        for resolvable in harness._FALLBACK_BASE_CANDIDATES:
            with self.subTest(candidate=resolvable):

                def fake_git_lines(args, *, target=resolvable):
                    return [args[-1]] if args[-1] == target else []

                with (
                    self._no_base_env(),
                    mock.patch.object(harness, "BASE_OVERRIDE", None),
                    mock.patch.object(harness, "_git_lines", side_effect=fake_git_lines),
                ):
                    self.assertEqual(harness._base_ref(), resolvable)

    def test_unresolvable_base_yields_no_scope(self):
        # A shallow or detached checkout with no origin/main: the base diff is
        # empty and the scoped gates skip rather than fail. Documented trade-off.
        with (
            self._no_base_env(),
            mock.patch.object(harness, "BASE_OVERRIDE", None),
            mock.patch.object(harness, "_git_lines", return_value=[]),
        ):
            self.assertIsNone(harness._base_ref())
            self.assertEqual(harness._changed_paths_from_base(), [])


class TestEmptyScopeSkipsRatherThanWideningToWholeTree(unittest.TestCase):
    """THE safety property of the scoping design.

    An empty scope must make the gate return None (skipped via `_present`), never
    fall back to `["."]`. A whole-tree fallback turns adoption into ~19,300 ruff
    findings on day one in a large existing repo, which is exactly the failure the
    scoping exists to prevent. If this test fails, the design is broken.
    """

    @contextmanager
    def _empty_scope(self):
        output = io.StringIO()
        with (
            mock.patch.object(harness, "ALL_FILES", False),
            mock.patch.object(harness, "_scoped_py_files", return_value=[]),
            redirect_stdout(output),
        ):
            yield output

    def test_lint_format_and_typecheck_gates_return_none(self):
        for build in (harness._lint_gate, harness._format_check_gate, harness._typecheck_gate):
            with self.subTest(gate=build.__name__), self._empty_scope():
                self.assertIsNone(build())

    def test_present_drops_the_skipped_gates_so_no_command_runs(self):
        with self._empty_scope() as output:
            gates = harness._present([
                harness._lint_gate(),
                harness._format_check_gate(),
                harness._typecheck_gate(),
            ])

        self.assertEqual(gates, [])
        self.assertIn("skipped", output.getvalue())

    def test_no_gate_command_ever_contains_the_whole_tree_target(self):
        # The regression this guards: `_resolve_targets` returning ["."] (or
        # `_quality_targets()`) instead of None on an empty scope.
        with self._empty_scope():
            for build in (harness._lint_gate, harness._format_check_gate, harness._typecheck_gate):
                gate = build()
                if gate is not None:
                    self.fail(f"{build.__name__} widened an empty scope to {gate.cmd!r}")

    def test_fix_and_format_no_op_without_running_a_tool(self):
        for command in (harness.cmd_fix, harness.cmd_format):
            with self.subTest(command=command.__name__), self._empty_scope():
                with mock.patch.object(harness, "run") as run_mock:
                    self.assertTrue(command(no_exit=True))
                run_mock.assert_not_called()

    def test_typecheck_no_ops_without_running_a_tool(self):
        with self._empty_scope(), mock.patch.object(harness, "run") as run_mock:
            self.assertTrue(harness.cmd_typecheck(no_exit=True))
        run_mock.assert_not_called()

    def test_explicit_empty_file_list_also_skips(self):
        # pre-commit passes its staged set verbatim; an empty one must skip too,
        # not silently become a whole-tree run.
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(harness._lint_gate([]))
            self.assertIsNone(harness._typecheck_gate([]))


class TestAllFilesWidensScopedGates(unittest.TestCase):
    """`--all` is the deliberate whole-tree escape hatch."""

    @contextmanager
    def _all_files(self):
        with (
            mock.patch.object(harness, "ALL_FILES", True),
            mock.patch.object(harness, "_scoped_py_files", return_value=[]),
            redirect_stdout(io.StringIO()),
        ):
            yield

    def test_lint_and_format_widen_to_dot(self):
        with temp_project(with_tests=True), self._all_files():
            self.assertEqual(_require_gate(harness._lint_gate()).cmd[-1], ".")
            self.assertEqual(_require_gate(harness._format_check_gate()).cmd[-1], ".")

    def test_typecheck_widens_to_quality_targets_not_dot(self):
        # basedpyright over "." would walk .venv and build artifacts; the whole-tree
        # widening for typecheck is the project's own quality targets.
        with temp_project(with_tests=True), self._all_files():
            gate = _require_gate(harness._typecheck_gate())
            targets = harness._quality_targets()

        self.assertEqual(gate.cmd[-len(targets) :], targets)
        self.assertEqual(targets, ["src", "harness.py", "tests"])

    def test_fix_and_format_run_against_dot(self):
        for command, expected in ((harness.cmd_fix, "check"), (harness.cmd_format, "format")):
            with (
                self.subTest(command=command.__name__),
                temp_project(),
                self._all_files(),
                mock.patch.object(harness, "run", return_value=True) as run_mock,
            ):
                command(no_exit=True)
                cmd = run_mock.call_args.args[1]

            self.assertIn(expected, cmd)
            self.assertEqual(cmd[-1], ".")


class TestToolResolution(unittest.TestCase):
    """Three descending tiers, so the runner starts in a uv project, a pip
    virtualenv, or a repo with neither."""

    @staticmethod
    def _install(root: Path, name: str = "ruff") -> None:
        venv_bin = root / ".venv" / "bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        (venv_bin / name).write_text("", encoding="utf-8")

    def test_tier1_uses_uv_run_when_pyproject_exists_and_tool_is_installed(self):
        with temp_project() as root:
            Path("pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            self._install(root)
            self.assertEqual(harness._tool("ruff"), ["uv", "run", "ruff"])

    def test_tier1_adds_no_sync_only_for_read_only_stages(self):
        with temp_project() as root:
            Path("pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            self._install(root)
            read_only = harness._tool("ruff", read_only=True)
            mutating = harness._tool("ruff", read_only=False)

        # Plain `uv run` implicitly syncs (installs packages); pre-push/ci must not.
        self.assertEqual(read_only, ["uv", "run", "--no-sync", "ruff"])
        self.assertNotIn("--no-sync", mutating)

    def test_tier2_uses_local_venv_binary_when_no_pyproject(self):
        with temp_project() as root:
            self._install(root)
            self.assertEqual(harness._tool("ruff"), [str(Path(".venv", "bin", "ruff"))])
            # read_only never adds --no-sync outside the uv tier.
            self.assertEqual(
                harness._tool("ruff", read_only=True), [str(Path(".venv", "bin", "ruff"))]
            )

    def test_tier3_falls_back_to_uvx(self):
        with temp_project():
            self.assertEqual(harness._tool("ruff"), ["uvx", "ruff"])
            self.assertEqual(harness._tool("ruff", read_only=True), ["uvx", "ruff"])

    def test_tier1_falls_through_to_uvx_when_the_tool_is_not_installed(self):
        # A `pyproject.toml` with no `[project]` table (the shape `uv sync` cannot
        # install dev dependencies into) is a permanent state, not a transient one:
        # taking tier 1 there is a hard `Failed to spawn` with no fallback, so the
        # missing binary must fall all the way through to uvx.
        with temp_project():
            Path("pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            self.assertEqual(harness._tool("ruff"), ["uvx", "ruff"])
            self.assertEqual(harness._tool("ruff", read_only=True), ["uvx", "ruff"])

    def test_no_tier_can_create_a_venv_for_a_read_only_stage(self):
        # `uv run` creates `.venv` before it even tries to spawn the tool, so a
        # read-only gate resolved to `uv run` mutates the environment. With no
        # `.venv` present, no tier may resolve to `uv run` at all.
        with temp_project() as root:
            Path("pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            self.assertFalse((root / ".venv").exists())
            for tool in ("ruff", "coverage", "lizard"):
                self.assertEqual(harness._tool(tool, read_only=True), ["uvx", tool])
            self.assertFalse((root / ".venv").exists())

    def test_tier1_still_wins_over_tier2_for_an_installed_tool(self):
        # Precedence is unchanged where it is safe: in a uv project with the tool
        # installed, `uv run` beats the bare binary — same binary, but `uv run`
        # also puts the project's own packages on the import path.
        with temp_project() as root:
            Path("pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
            self._install(root)
            self.assertEqual(harness._tool("ruff"), ["uv", "run", "ruff"])

    def test_uvx_uses_from_when_the_console_script_is_not_the_distribution(self):
        # `uvx lint-imports` fails with "lint-imports was not found in the package
        # registry" — the script ships in the `import-linter` distribution. Without
        # this, `arch` is broken in every adopter that has no `.venv/bin/lint-imports`.
        with temp_project():
            self.assertEqual(
                harness._tool("lint-imports", read_only=True),
                ["uvx", "--from", "import-linter", "lint-imports"],
            )

    def test_tools_whose_name_matches_their_distribution_get_no_from(self):
        with temp_project():
            for tool in ("ruff", "basedpyright", "coverage", "behave", "vulture", "lizard"):
                self.assertEqual(harness._tool(tool), ["uvx", tool])

    def test_a_local_binary_still_wins_over_the_distribution_map(self):
        with temp_project() as root:
            self._install(root, "lint-imports")
            self.assertEqual(
                harness._tool("lint-imports"), [str(Path(".venv", "bin", "lint-imports"))]
            )

    @staticmethod
    def _lock(*packages: tuple[str, str]) -> None:
        body = "".join(
            f'[[package]]\nname = "{name}"\nversion = "{version}"\n\n'
            for name, version in packages
        )
        Path("uv.lock").write_text(f"version = 1\n\n{body}", encoding="utf-8")

    def test_tier3_pins_the_locked_version_with_from(self):
        # The lock holds the exact release `uv run` would use; the fallback runs
        # the same one, so a gate reports the same findings with or without a venv.
        with temp_project():
            self._lock(("ruff", "0.15.11"), ("lizard", "1.22.2"))
            self.assertEqual(harness._tool("ruff"), ["uvx", "--from", "ruff==0.15.11", "ruff"])
            self.assertEqual(
                harness._tool("lizard", read_only=True),
                ["uvx", "--from", "lizard==1.22.2", "lizard"],
            )

    def test_tier3_pins_the_distribution_of_a_renamed_console_script(self):
        with temp_project():
            self._lock(("import-linter", "2.11"))
            self.assertEqual(
                harness._tool("lint-imports"),
                ["uvx", "--from", "import-linter==2.11", "lint-imports"],
            )

    def test_tier3_stays_unpinned_when_the_lock_has_no_entry(self):
        with temp_project():
            self._lock(("coverage", "7.13.5"))
            self.assertEqual(harness._tool("ruff"), ["uvx", "ruff"])
            self.assertEqual(
                harness._tool("lint-imports"), ["uvx", "--from", "import-linter", "lint-imports"]
            )

    def test_tier3_stays_unpinned_when_the_lock_is_unreadable(self):
        # A pin is a fidelity gain, never a reason for the runner to stop starting.
        for body in ("version = [\n", "version = 1\n", '[[package]]\nname = "ruff"\n'):
            with self.subTest(body=body), temp_project():
                Path("uv.lock").write_text(body, encoding="utf-8")
                self.assertEqual(harness._tool("ruff"), ["uvx", "ruff"])

    def test_lock_versions_are_read_once_per_run(self):
        with temp_project():
            self._lock(("ruff", "0.15.11"))
            self.assertEqual(harness._lock_versions(), {"ruff": "0.15.11"})
            self._lock(("ruff", "9.9.9"))
            self.assertEqual(harness._lock_versions(), {"ruff": "0.15.11"})

    def test_a_local_binary_still_wins_over_the_lock_pin(self):
        with temp_project() as root:
            self._lock(("ruff", "0.15.11"))
            self._install(root)
            self.assertEqual(harness._tool("ruff"), [str(Path(".venv", "bin", "ruff"))])


class TestLockfileGate(unittest.TestCase):
    """A pip + requirements.txt repo has no uv.lock; absence is not drift."""

    def test_returns_none_and_warns_when_uv_lock_is_absent(self):
        output = io.StringIO()
        with temp_project(), redirect_stdout(output):
            gate = harness._lockfile_gate()

        self.assertIsNone(gate)
        self.assertIn("no uv.lock; skipped", output.getvalue())
        self.assertEqual(harness._present([gate]), [])

    def test_returns_the_gate_when_uv_lock_exists(self):
        with temp_project():
            Path("uv.lock").write_text("version = 1\n", encoding="utf-8")
            gate = harness._lockfile_gate()

        self.assertEqual(_require_gate(gate).cmd, ["uv", "lock", "--check"])


class TestCmdTypecheckFilesArgument(unittest.TestCase):
    """`cmd_typecheck` takes the scoped file list as its leading positional, so
    pre-commit can hand it the staged set verbatim."""

    def test_leading_positional_files_is_used_verbatim(self):
        files = ["src/app.py", "tests/test_app.py"]
        with (
            temp_project(with_tests=True),
            mock.patch.object(harness, "run", return_value=True) as run_mock,
        ):
            self.assertTrue(harness.cmd_typecheck(files))
            cmd = run_mock.call_args.args[1]

        self.assertEqual(cmd[-2:], files)
        self.assertNotIn(".", cmd)

    def test_explicit_files_ignore_the_all_flag(self):
        with (
            temp_project(with_tests=True),
            mock.patch.object(harness, "ALL_FILES", True),
            mock.patch.object(harness, "run", return_value=True) as run_mock,
        ):
            harness.cmd_typecheck(["src/app.py"])
            cmd = run_mock.call_args.args[1]

        self.assertEqual(cmd[-1:], ["src/app.py"])

    def test_no_exit_is_keyword_only_and_forwarded(self):
        with (
            temp_project(with_tests=True),
            mock.patch.object(harness, "run", return_value=False) as run_mock,
        ):
            self.assertFalse(harness.cmd_typecheck(["src/app.py"], no_exit=True))

        self.assertTrue(run_mock.call_args.kwargs["no_exit"])


class TestCrapGlyph(unittest.TestCase):
    """Advisory CRAP output must not print a red ✗ (blocking glyph) on a run that
    exits 0 — ✗ is reserved for --enforce runs that actually fail."""

    def test_advisory_mode_uses_green_warn_glyph(self):
        self.assertEqual(
            harness._advisory_glyph(enforce=False), f"{harness.GREEN}⚠{harness.RESET}"
        )

    def test_enforce_mode_uses_red_cross_glyph(self):
        self.assertEqual(harness._advisory_glyph(enforce=True), f"{harness.RED}✗{harness.RESET}")

    def test_cmd_crap_prints_warn_glyph_when_lizard_fails_in_advisory_mode(self):
        output = io.StringIO()
        with (
            mock.patch.object(harness, "_has_tests", return_value=True),
            mock.patch.object(harness, "_artifact_is_fresh", return_value=True),
            mock.patch.object(harness, "_app_targets", return_value=["src"]),
            mock.patch.object(harness.Path, "exists", return_value=True),
            mock.patch.object(harness, "_parse_coverage_xml", return_value={}),
            mock.patch.object(harness.sys, "argv", ["harness", "crap"]),
            mock.patch.object(
                harness.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], returncode=2, stdout="", stderr="x"),
            ),
            redirect_stdout(output),
        ):
            harness.cmd_crap()

        text = output.getvalue()
        self.assertIn("(advisory)", text)
        self.assertNotIn("✗", text)
        self.assertIn("⚠", text)

    def test_cmd_crap_prints_red_cross_when_lizard_fails_in_enforce_mode(self):
        output = io.StringIO()
        with (
            mock.patch.object(harness, "_has_tests", return_value=True),
            mock.patch.object(harness, "_artifact_is_fresh", return_value=True),
            mock.patch.object(harness, "_app_targets", return_value=["src"]),
            mock.patch.object(harness.Path, "exists", return_value=True),
            mock.patch.object(harness, "_parse_coverage_xml", return_value={}),
            mock.patch.object(harness.sys, "argv", ["harness", "crap", "--enforce"]),
            mock.patch.object(
                harness.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], returncode=2, stdout="", stderr="x"),
            ),
            redirect_stdout(output),
            self.assertRaises(SystemExit),
        ):
            harness.cmd_crap()

        text = output.getvalue()
        self.assertNotIn("(advisory)", text)
        self.assertIn("✗", text)


class TestUnresolvableRequestedBase(unittest.TestCase):
    """An empty scope caused by a typo'd `--base=` must not look like a clean tree.

    The empty-scope skip itself stays green (see
    TestEmptyScopeSkipsRatherThanWideningToWholeTree) — only the *cause* is
    disambiguated: a base nobody asked for that does not resolve (shallow checkout)
    stays a silent skip; a base someone asked for that does not resolve is loud, and
    blocks in ci/pre-push.
    """

    @contextmanager
    def _requested(self, base, *, resolves):
        with (
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch.object(harness, "BASE_OVERRIDE", base),
            mock.patch.object(harness, "_git_lines", return_value=["sha"] if resolves else []),
        ):
            for key in ("HARNESS_ARCH_BASE", "GITHUB_BASE_REF"):
                os.environ.pop(key, None)
            yield

    def test_unresolvable_requested_base_is_named(self):
        with self._requested("does-not-exist", resolves=False):
            self.assertEqual(harness._unresolved_requested_base(), "does-not-exist")

    def test_resolvable_requested_base_is_not_flagged(self):
        with self._requested("main", resolves=True):
            self.assertIsNone(harness._unresolved_requested_base())

    def test_unresolvable_fallback_base_is_not_flagged(self):
        # No --base=, no env: a shallow checkout with no origin/main must stay the
        # silent, green skip the scoping design depends on.
        with self._requested(None, resolves=False):
            self.assertIsNone(harness._unresolved_requested_base())
            self.assertIsNone(harness._base_ref())

    def test_scoped_gate_skip_message_names_the_bad_base(self):
        output = io.StringIO()
        with (
            self._requested("does-not-exist", resolves=False),
            mock.patch.object(harness, "ALL_FILES", False),
            mock.patch.object(harness, "_scoped_py_files", return_value=[]),
            redirect_stdout(output),
        ):
            self.assertIsNone(harness._lint_gate())

        text = output.getvalue()
        self.assertIn("does-not-exist", text)
        self.assertIn("does not resolve", text)
        self.assertNotIn("no changed Python files", text)

    def test_clean_tree_keeps_the_plain_skip_message(self):
        output = io.StringIO()
        with (
            self._requested(None, resolves=False),
            mock.patch.object(harness, "ALL_FILES", False),
            mock.patch.object(harness, "_scoped_py_files", return_value=[]),
            redirect_stdout(output),
        ):
            self.assertIsNone(harness._lint_gate())

        self.assertIn("no changed Python files", output.getvalue())
        self.assertNotIn("does not resolve", output.getvalue())

    def test_check_warns_but_does_not_block(self):
        output = io.StringIO()
        with self._requested("does-not-exist", resolves=False), redirect_stdout(output):
            self.assertTrue(harness._check_base_ref(warn_only=True))
        self.assertIn("⚠", output.getvalue())
        self.assertIn("does-not-exist", output.getvalue())

    def test_ci_and_pre_push_fail(self):
        output = io.StringIO()
        with self._requested("does-not-exist", resolves=False), redirect_stdout(output):
            self.assertFalse(harness._check_base_ref(no_exit=True))
            with self.assertRaises(SystemExit) as ctx:
                harness._check_base_ref()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("✗", output.getvalue())

    def test_silent_when_nothing_is_wrong(self):
        output = io.StringIO()
        with self._requested("main", resolves=True), redirect_stdout(output):
            self.assertTrue(harness._check_base_ref())
        self.assertEqual(output.getvalue(), "")


class TestCheckSummaryCountsEveryGate(unittest.TestCase):
    """`check`'s FAIL tally must equal the number of ✗ lines it printed.

    The bug: the whole parallel batch collapsed into one boolean, so six failing
    gates were summarized as one.
    """

    def test_every_batch_failure_is_counted(self):
        batch = [harness.Gate(f"g{i}", ["true"]) for i in range(4)]
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patcher in (
                mock.patch.object(harness, "run", return_value=True),
                mock.patch.object(harness, "cmd_fix", return_value=True),
                mock.patch.object(harness, "cmd_format", return_value=True),
                mock.patch.object(harness, "cmd_typecheck", return_value=True),
                mock.patch.object(harness, "cmd_test", return_value=True),
                mock.patch.object(harness, "_complexity_gate", return_value=batch[0]),
                mock.patch.object(harness, "_acceptance_gates_or_warn", return_value=batch[1:]),
                mock.patch.object(harness, "_arch_gates_or_warn", return_value=[]),
                mock.patch.object(
                    harness,
                    "run_gates_parallel",
                    return_value=(False, ["g0", "g1", "g2"]),
                ),
                mock.patch.object(harness, "_check_stop_hooks_present"),
                mock.patch.object(harness, "_check_arch_config_guard", return_value=True),
                mock.patch.object(harness, "_check_gherkin_guard", return_value=True),
                mock.patch.object(harness, "_check_agents_md_drift", return_value=True),
                mock.patch.object(harness, "_check_suppressions_baseline", return_value=True),
                mock.patch.object(harness, "_check_deadcode", return_value=True),
            ):
                stack.enter_context(patcher)
            with redirect_stdout(output), self.assertRaises(SystemExit):
                harness.cmd_check()

        # 3 of the 4 batch gates failed; the old code reported 1.
        self.assertIn("3 failed", output.getvalue())
        self.assertIn("11 passed", output.getvalue())


class TestVerboseStillPrintsGlyphs(unittest.TestCase):
    """`--verbose` is the flag you reach for when something is wrong. It printed the
    argv and swallowed the ✓/✗, hiding which gate failed."""

    @contextmanager
    def _verbose(self, returncode):
        with (
            mock.patch.object(harness, "VERBOSE", True),
            mock.patch.object(
                harness.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], returncode=returncode),
            ),
        ):
            yield

    def test_run_prints_argv_and_success_glyph(self):
        output = io.StringIO()
        with self._verbose(0), redirect_stdout(output):
            self.assertTrue(harness.run("Lint check", ["ruff", "check"]))
        text = output.getvalue()
        self.assertIn("-> ruff check", text)
        self.assertIn("✓", text)
        self.assertIn("Lint check", text)

    def test_run_prints_failure_glyph_before_exiting(self):
        output = io.StringIO()
        with self._verbose(3), redirect_stdout(output):
            self.assertFalse(harness.run("Lint check", ["ruff", "check"], no_exit=True))
        self.assertIn("✗", output.getvalue())
        self.assertIn("Lint check", output.getvalue())

    def test_run_still_exits_with_the_command_returncode(self):
        with (
            self._verbose(3),
            redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            harness.run("Lint check", ["ruff", "check"])
        self.assertEqual(ctx.exception.code, 3)

    def test_parallel_batch_prints_a_glyph_per_gate(self):
        output = io.StringIO()
        gates = [harness.Gate("A", ["a"]), harness.Gate("B", ["b"])]
        with self._verbose(1), redirect_stdout(output):
            all_ok, failed = harness.run_gates_parallel(gates)
        self.assertFalse(all_ok)
        self.assertEqual(failed, ["A", "B"])
        self.assertEqual(output.getvalue().count("✗"), 2)


class TestHunkParsing(unittest.TestCase):
    """`@@` headers are the whole source of truth for "lines this change wrote"."""

    def test_reads_post_image_start_and_count(self):
        self.assertEqual(harness._parse_hunks(["@@ -1,3 +4,2 @@ def f():"]), [(4, 5)])

    def test_a_missing_count_means_one_line(self):
        self.assertEqual(harness._parse_hunks(["@@ -1 +7 @@"]), [(7, 7)])

    def test_pure_deletion_hunks_contribute_nothing(self):
        # `+9,0` writes no post-image line, so there is nothing of yours to check.
        self.assertEqual(harness._parse_hunks(["@@ -3,2 +9,0 @@"]), [])

    def test_non_hunk_lines_are_ignored(self):
        self.assertEqual(harness._parse_hunks(["diff --git a/x b/x", "+added"]), [])

    def test_merge_joins_overlapping_and_abutting_ranges(self):
        self.assertEqual(harness._merge_ranges([(5, 6), (1, 3), (4, 4)]), [(1, 6)])

    def test_merge_keeps_a_real_gap(self):
        self.assertEqual(harness._merge_ranges([(1, 2), (10, 12)]), [(1, 2), (10, 12)])

    def test_in_ranges_is_inclusive_overlap_not_containment(self):
        self.assertTrue(harness._in_ranges(3, 8, [(7, 9)]))
        self.assertFalse(harness._in_ranges(3, 6, [(7, 9)]))


class TestFindingFilter(unittest.TestCase):
    """The bug this whole design exists to kill: a pre-existing violation on an
    untouched line surfacing because a change touched the same file."""

    @staticmethod
    def _finding(row, code="E501", path="src/legacy.py"):
        return {
            "filename": path,
            "code": code,
            "message": "stub",
            "location": {"row": row, "column": 1},
            "end_location": {"row": row, "column": 2},
        }

    def test_a_finding_outside_every_changed_range_is_dropped(self):
        kept = harness._filter_findings(
            [self._finding(1), self._finding(40)], {"src/legacy.py": [(38, 42)]}
        )
        self.assertEqual([f["location"]["row"] for f in kept], [40])

    def test_a_finding_spanning_into_a_changed_range_is_kept(self):
        finding = self._finding(8)
        finding["end_location"] = {"row": 12, "column": 1}
        self.assertEqual(
            harness._filter_findings([finding], {"src/legacy.py": [(11, 11)]}), [finding]
        )

    def test_whole_file_codes_survive_anywhere_in_a_scoped_file(self):
        # An import this edit orphaned sits on a line the edit never wrote.
        kept = harness._filter_findings(
            [self._finding(1, "F401"), self._finding(1, "E501")], {"src/legacy.py": [(99, 99)]}
        )
        self.assertEqual([f["code"] for f in kept], ["F401"])

    def test_unsorted_imports_are_not_a_whole_file_code(self):
        # I001 looks like it belongs in WHOLE_FILE_CODES and does not: ruff reports
        # it spanning the entire import block, so an edit inside the imports already
        # intersects it. Exempting it as well turns a pre-existing unsorted block red
        # on any edit to the file — and `fix` cannot resort it without rewriting
        # untouched lines, so the gate would be red with no way out.
        self.assertNotIn("I001", harness.WHOLE_FILE_CODES)
        spanning = self._finding(1, "I001")
        spanning["end_location"] = {"row": 6, "column": 1}
        self.assertEqual(harness._filter_findings([spanning], {"src/legacy.py": [(40, 40)]}), [])
        self.assertEqual(
            harness._filter_findings([spanning], {"src/legacy.py": [(3, 3)]}), [spanning]
        )

    def test_a_file_with_no_base_version_keeps_every_finding(self):
        findings = [self._finding(1), self._finding(2)]
        self.assertEqual(harness._filter_findings(findings, {"src/legacy.py": None}), findings)

    def test_a_deletion_only_file_keeps_only_whole_file_codes(self):
        kept = harness._filter_findings(
            [self._finding(1, "F401"), self._finding(1, "E501")], {"src/legacy.py": []}
        )
        self.assertEqual([f["code"] for f in kept], ["F401"])

    def test_a_finding_in_an_unscoped_file_is_dropped(self):
        self.assertEqual(harness._filter_findings([self._finding(1)], {"src/other.py": None}), [])

    def test_the_label_reports_the_count_only_when_filtering_happened(self):
        self.assertEqual(harness._scoped_label("Lint check", 3, 3), "Lint check")
        self.assertEqual(
            harness._scoped_label("Lint check", 0, 7), "Lint check (0/7 on changed lines)"
        )


class TestDiagnosticFilter(unittest.TestCase):
    """basedpyright's line numbers are 0-based; the hunk ranges are 1-based."""

    @staticmethod
    def _diagnostic(line, severity="error", path="src/legacy.py"):
        return {
            "file": path,
            "severity": severity,
            "message": "stub",
            "range": {"start": {"line": line}, "end": {"line": line}},
        }

    def test_zero_based_lines_are_converted_before_comparison(self):
        self.assertEqual(harness._diagnostic_row_span(self._diagnostic(27)), (28, 28))

    def test_a_diagnostic_outside_the_changed_ranges_is_dropped(self):
        kept = harness._filter_diagnostics(
            [self._diagnostic(0), self._diagnostic(9)], {"src/legacy.py": [(10, 10)]}
        )
        self.assertEqual([harness._diagnostic_row_span(d)[0] for d in kept], [10])

    def test_a_file_with_no_base_version_keeps_every_diagnostic(self):
        diagnostics = [self._diagnostic(0), self._diagnostic(9)]
        self.assertEqual(
            harness._filter_diagnostics(diagnostics, {"src/legacy.py": None}), diagnostics
        )

    def test_a_deletion_only_file_keeps_nothing(self):
        # There is no type-level analogue of WHOLE_FILE_CODES.
        self.assertEqual(
            harness._filter_diagnostics([self._diagnostic(0)], {"src/legacy.py": []}), []
        )


class TestFixStaysInScope(unittest.TestCase):
    """`ruff --fix` rewrites a whole file or nothing, so the fix is all-or-nothing
    per file: a fix that reached an untouched line is undone, not partially kept."""

    def test_a_rewrite_confined_to_the_changed_lines_is_accepted(self):
        before = "a\nb\nc\n"
        after = "a\nB\nc\n"
        self.assertTrue(harness._edits_stayed_in_scope(before, after, [(2, 2)]))

    def test_a_rewrite_of_an_untouched_line_is_rejected(self):
        before = "a\nb\nc\n"
        after = "A\nb\nc\n"
        self.assertFalse(harness._edits_stayed_in_scope(before, after, [(2, 2)]))

    def test_an_insertion_at_the_edge_of_the_change_is_accepted(self):
        before = "a\nb\n"
        after = "a\nnew\nb\n"
        self.assertTrue(harness._edits_stayed_in_scope(before, after, [(1, 2)]))

    def test_an_insertion_far_from_the_change_is_rejected(self):
        # The fusion regression: ruff adds `from collections.abc import Callable`
        # at the top because of a rewrite the change never asked for.
        before = "import a\n\nx = 1\ny = 2\n"
        after = "import a\nimport b\n\nx = 1\ny = 2\n"
        self.assertFalse(harness._edits_stayed_in_scope(before, after, [(4, 4)]))

    def test_a_deletion_only_file_never_accepts_a_rewrite(self):
        self.assertFalse(harness._edits_stayed_in_scope("a\n", "b\n", []))


class TestWholeFileEscapeHatch(unittest.TestCase):
    """`--whole-file` keeps the changed-file scope but drops the line filter, so a
    regression sweep over the files you touched is one flag away."""

    @contextmanager
    def _mode(self, *, all_files=False, whole_file=False):
        with (
            mock.patch.object(harness, "ALL_FILES", all_files),
            mock.patch.object(harness, "WHOLE_FILE", whole_file),
            mock.patch.object(harness, "_scoped_py_files", return_value=["src/app.py"]),
            redirect_stdout(io.StringIO()),
        ):
            yield

    def test_default_mode_is_line_scoped(self):
        with self._mode():
            self.assertTrue(harness._line_scoped())
            gate = _require_gate(harness._lint_gate())
        self.assertIsNotNone(gate.runner)
        self.assertIn("--output-format=json", gate.cmd)

    def test_whole_file_drops_the_line_filter(self):
        with self._mode(whole_file=True):
            self.assertFalse(harness._line_scoped())
            gate = _require_gate(harness._lint_gate())
        self.assertIsNone(gate.runner)
        self.assertNotIn("--output-format=json", gate.cmd)

    def test_all_also_drops_the_line_filter(self):
        with self._mode(all_files=True):
            self.assertFalse(harness._line_scoped())

    def test_a_runner_gate_reports_its_own_verdict_not_an_exit_code(self):
        # The command "fails" (exit 1) but the runner says the findings were all
        # off-scope; the runner wins, because the exit code is not the verdict.
        result = harness.GateResult("Lint check (0/9 on changed lines)", ["ruff"], 0, "", "")
        gate = harness.Gate("Lint check", ["ruff"], None, runner=lambda: result)
        output = io.StringIO()
        with redirect_stdout(output):
            all_ok, failed = harness.run_gates_parallel([gate])
        self.assertTrue(all_ok)
        self.assertEqual(failed, [])
        self.assertIn("0/9 on changed lines", output.getvalue())


class TestChangedLineRanges(unittest.TestCase):
    """End-to-end over a real git repo: the ranges must come out of real hunks."""

    @contextmanager
    def _repo(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.chdir(root)
            git = shutil.which("git") or "git"

            def run_git(*args):
                subprocess.run([git, *args], check=True, capture_output=True, text=True)

            run_git("init", "-b", "main")
            run_git("config", "user.email", "harness@example.invalid")
            run_git("config", "user.name", "Harness Test")
            (root / "src").mkdir()
            (root / "src" / "legacy.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
            run_git("add", "-A")
            run_git("commit", "-m", "base")
            try:
                yield root
            finally:
                os.chdir(old_cwd)

    def test_an_appended_line_scopes_to_that_line_alone(self):
        with self._repo() as root:
            (root / "src" / "legacy.py").write_text(
                "a = 1\nb = 2\nc = 3\n# appended\n", encoding="utf-8"
            )
            ranges = harness._scoped_ranges(["src/legacy.py"])
        self.assertEqual(ranges, {"src/legacy.py": [(4, 4)]})

    def test_an_untracked_file_has_no_base_version_so_the_whole_file_is_in_scope(self):
        with self._repo() as root:
            (root / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
            ranges = harness._scoped_ranges(["src/new.py"])
        self.assertIsNone(ranges["src/new.py"])

    def test_an_unchanged_file_yields_no_ranges(self):
        with self._repo():
            self.assertEqual(harness._scoped_ranges(["src/legacy.py"]), {"src/legacy.py": []})


class TestSyncAgentsMdRefusesToClobber(unittest.TestCase):
    """`sync-agents-md` is the remedy the drift check prints, and on a brownfield
    adoption that drift failure is guaranteed — so an unconditional copy makes data
    loss the *default* path through the harness. It must refuse instead."""

    @staticmethod
    @contextmanager
    def _docs(claude: str, agents: str | None = None, *, agents_name: str = "AGENTS.md"):
        with temp_project() as root:
            (root / "CLAUDE.md").write_text(claude, encoding="utf-8")
            if agents is not None:
                (root / agents_name).write_text(agents, encoding="utf-8")
            yield root

    def _sync(self) -> tuple[int, str]:
        out = io.StringIO()
        code = 0
        with redirect_stdout(out):
            try:
                harness.cmd_sync_agents_md()
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue()

    def test_a_stale_copy_is_still_overwritten(self):
        # The template case the command exists for: AGENTS.md is a previous revision
        # of CLAUDE.md, so the copy is the whole point and must still happen.
        body = "".join(f"line {i}\n" for i in range(40))
        with self._docs(body + "new tail\n", body + "old tail\n") as root:
            code, out = self._sync()
            written = (root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("AGENTS.md ← CLAUDE.md", out)
        self.assertTrue(written.endswith("new tail\n"))

    def test_an_unrelated_agents_md_is_refused_not_overwritten(self):
        adopter = "".join(f"# the adopter's own instruction {i}\n" for i in range(277))
        template = "".join(f"harness line {i}\n" for i in range(73))
        with self._docs(template, adopter) as root:
            code, out = self._sync()
            after = (root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(code, 1)
        self.assertIn("too different to be a stale copy", out)
        self.assertEqual(after, adopter, "the adopter's AGENTS.md must survive verbatim")

    def test_a_differently_cased_twin_is_refused(self):
        # On APFS/NTFS `Path("AGENTS.md").write_bytes()` writes *through* to an
        # existing `agents.md`; `exists()` cannot see the difference, `iterdir` can.
        with self._docs("template\n", agents_name="agents.md") as root:
            (root / "agents.md").write_text("adopter content\n", encoding="utf-8")
            code, out = self._sync()
            after = (root / "agents.md").read_text(encoding="utf-8")
        self.assertEqual(code, 1)
        self.assertIn("different case", out)
        self.assertEqual(after, "adopter content\n")

    def test_a_symlinked_pair_is_a_no_op_not_a_replaced_link(self):
        # Some repos ship CLAUDE.md as a symlink to AGENTS.md. They are identical by
        # construction, so there is nothing to sync — and the link must survive.
        with temp_project() as root:
            (root / "AGENTS.md").write_text("shared\n", encoding="utf-8")
            (root / "CLAUDE.md").symlink_to("AGENTS.md")
            code, out = self._sync()
            self.assertTrue((root / "CLAUDE.md").is_symlink())
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "shared\n")
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", out)

    def test_a_symlink_pointing_elsewhere_is_refused(self):
        with temp_project() as root:
            (root / "elsewhere.md").write_text("adopter content\n", encoding="utf-8")
            (root / "CLAUDE.md").write_text("template\n", encoding="utf-8")
            (root / "AGENTS.md").symlink_to("elsewhere.md")
            code, out = self._sync()
            self.assertTrue((root / "AGENTS.md").is_symlink())
            self.assertEqual(
                (root / "elsewhere.md").read_text(encoding="utf-8"), "adopter content\n"
            )
        self.assertEqual(code, 1)
        self.assertIn("linked, not copied", out)

    def test_a_missing_agents_md_is_created(self):
        with self._docs("template\n") as root:
            code, _out = self._sync()
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "template\n")
        self.assertEqual(code, 0)


class TestAgentsMdDriftOptOut(unittest.TestCase):
    """Retrofit accommodation, modelled on gherkin-guard's no-.feature self-skip."""

    def _drift(self) -> tuple[bool, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            ok = harness._check_agents_md_drift(no_exit=True)
        return ok, out.getvalue()

    def test_the_env_var_lets_a_repo_keep_its_own_agents_md(self):
        with temp_project() as root:
            (root / "CLAUDE.md").write_text("harness\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("the adopter's own\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {harness.AGENTS_MD_ALLOW_ENV: "1"}):
                ok, out = self._drift()
        self.assertTrue(ok)
        self.assertIn("skipped", out)

    def test_drift_still_fails_without_the_opt_out(self):
        with temp_project() as root:
            (root / "CLAUDE.md").write_text("harness\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("the adopter's own\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(harness.AGENTS_MD_ALLOW_ENV, None)
                ok, out = self._drift()
        self.assertFalse(ok)
        self.assertIn("differs from CLAUDE.md", out)

    def test_the_failure_message_does_not_recommend_an_unconditional_copy(self):
        # The original message was "— run `harness sync-agents-md`" with no caveat,
        # which pointed a brownfield adopter straight at deleting their own file.
        with temp_project() as root:
            (root / "CLAUDE.md").write_text("harness\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("the adopter's own\n", encoding="utf-8")
            os.environ.pop(harness.AGENTS_MD_ALLOW_ENV, None)
            _ok, out = self._drift()
        self.assertIn("reconcile the two by hand", out)
        self.assertIn("refuses when AGENTS.md is not a stale copy", out)
        self.assertIn(harness.AGENTS_MD_ALLOW_ENV, out)


class TestAuditGateIsReadOnly(unittest.TestCase):
    """`ci` documents itself as read-only in three places; the audit gate was the
    last path that still created a `.venv`."""

    def test_no_venv_means_no_gate_and_a_warning(self):
        with temp_project():
            out = io.StringIO()
            with redirect_stdout(out):
                gate = harness._audit_gate_or_warn()
        self.assertIsNone(gate)
        self.assertIn("skipped (no .venv", out.getvalue())

    def test_an_existing_venv_is_audited_without_syncing(self):
        with temp_project() as root:
            (root / ".venv" / "bin").mkdir(parents=True)
            out = io.StringIO()
            with redirect_stdout(out):
                gate = harness._audit_gate_or_warn()
        assert gate is not None
        # Measured: --no-sync alone does NOT stop uv from creating a missing .venv,
        # so the presence check above is load-bearing and this is the second belt.
        self.assertEqual(gate.cmd, ["uv", "run", "--no-sync", "--with", "pip-audit", "pip-audit"])
        self.assertEqual(out.getvalue(), "")

    def test_cmd_audit_skips_quietly_rather_than_failing(self):
        with temp_project():
            out = io.StringIO()
            with redirect_stdout(out), mock.patch.object(harness, "run") as ran:
                harness.cmd_audit()
        ran.assert_not_called()
        self.assertIn("skipped", out.getvalue())


class TestCoverageResolvesOneWay(unittest.TestCase):
    def test_every_coverage_invocation_is_read_only(self):
        # One tool, one resolution. Coverage resolving two ways in this file is what
        # produced the original `ci`-creates-a-`.venv` bug.
        source = Path(harness.__file__).read_text(encoding="utf-8")
        calls = [line for line in source.splitlines() if '_tool("coverage"' in line]
        self.assertTrue(calls)
        for line in calls:
            self.assertIn("read_only=True", line, f"non-read-only coverage call: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
