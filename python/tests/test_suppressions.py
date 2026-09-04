"""Tests for the suppression scanner in harness.py."""

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

import harness
from harness import _parse_line_for_suppressions, _scan_suppressions


@contextmanager
def cwd(path: Path):
    old = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(old)


def _as_measurement(value: harness.Measurement | int | None) -> harness.Measurement:
    if isinstance(value, harness.Measurement):
        return value
    if value is None:
        return harness.Measurement(unavailable="not applicable here")
    return harness.Measurement(value=value)


@contextmanager
def measurements(
    coverage: harness.Measurement | int | None = None,
    complexity: harness.Measurement | int | None = None,
    duplication: harness.Measurement | int | None = None,
    crap: harness.Measurement | int | None = None,
    deadcode: harness.Measurement | int | None = None,
):
    """Stub every ratcheted measurement. `None` means "does not apply here"."""
    with (
        mock.patch.object(harness, "_measured_coverage", return_value=_as_measurement(coverage)),
        mock.patch.object(
            harness, "_measured_complexity_violations", return_value=_as_measurement(complexity)
        ),
        mock.patch.object(
            harness, "_measured_duplicate_blocks", return_value=_as_measurement(duplication)
        ),
        mock.patch.object(
            harness, "_measured_crap_violations", return_value=_as_measurement(crap)
        ),
        mock.patch.object(
            harness, "_measured_deadcode_findings", return_value=_as_measurement(deadcode)
        ),
    ):
        yield


class TestParseLine(unittest.TestCase):
    def test_plain_code_no_match(self) -> None:
        self.assertEqual(_parse_line_for_suppressions("x = 1"), [])

    def test_bare_noqa(self) -> None:
        self.assertEqual(_parse_line_for_suppressions("x = 1  # noqa"), [("noqa", [])])

    def test_noqa_with_rules(self) -> None:
        self.assertEqual(
            _parse_line_for_suppressions("x = 1  # noqa: E501, F401"),
            [("noqa", ["E501", "F401"])],
        )

    def test_type_ignore_with_rule(self) -> None:
        self.assertEqual(
            _parse_line_for_suppressions("x: int = y  # type: ignore[union-attr]"),
            [("type_ignore", ["union-attr"])],
        )

    def test_pyright_ignore_with_rule(self) -> None:
        self.assertEqual(
            _parse_line_for_suppressions("x = y  # pyright: ignore[reportGeneralTypeIssues]"),
            [("pyright_ignore", ["reportGeneralTypeIssues"])],
        )

    def test_bare_type_ignore_no_rules(self) -> None:
        self.assertEqual(
            _parse_line_for_suppressions("x = y  # type: ignore"),
            [("type_ignore", [])],
        )

    def test_multiple_kinds_on_one_line(self) -> None:
        result = _parse_line_for_suppressions("x = y  # noqa: E501 # type: ignore[arg-type]")
        self.assertIn(("noqa", ["E501"]), result)
        self.assertIn(("type_ignore", ["arg-type"]), result)


class TestScanFixture(unittest.TestCase):
    def test_scan_temp_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "mod.py").write_text(
                "x = 1  # noqa: E501\n"
                "y = 2  # type: ignore[assignment]\n"
                "z = 3  # pyright: ignore[reportGeneralTypeIssues]\n"
                "w = 4\n",
            )
            (tmp_path / "skip.txt").write_text("# noqa: ignored because not .py\n")

            results = _scan_suppressions([tmp])

        self.assertEqual(results.get("noqa"), [["E501"]])
        self.assertEqual(results.get("type_ignore"), [["assignment"]])
        self.assertEqual(results.get("pyright_ignore"), [["reportGeneralTypeIssues"]])


class TestBaseline(unittest.TestCase):
    def test_read_baseline_parses_key_value_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".harness-baseline").write_text(
                "suppressions.noqa 2\ncoverage.min 75\n", encoding="utf-8"
            )
            with cwd(root):
                self.assertEqual(
                    harness._read_baseline(),
                    {"suppressions.noqa": 2, "coverage.min": 75},
                )

    def test_coverage_min_uses_flag_before_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".harness-baseline").write_text("coverage.min 60\n", encoding="utf-8")
            with cwd(root):
                with mock.patch.object(harness.sys, "argv", ["harness", "coverage"]):
                    self.assertEqual(harness._coverage_min_default(), 60)
                with mock.patch.object(harness.sys, "argv", ["harness", "coverage", "--min=10"]):
                    self.assertEqual(harness._coverage_min_default(), 10)

    def test_coverage_min_rejects_non_integer_flag(self) -> None:
        # `--min=abc` used to raise an uncaught ValueError; it must instead print a
        # clear error and exit 1.
        with mock.patch.object(harness.sys, "argv", ["harness", "coverage", "--min=abc"]):
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as ctx:
                harness._coverage_min_default()

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("--min=", output.getvalue())

    def test_write_baseline_merges_over_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".harness-baseline").write_text(
                "custom.thing 7\ncoverage.min 40\n", encoding="utf-8"
            )
            with (
                cwd(root),
                measurements(coverage=88, complexity=3, duplication=8, crap=None, deadcode=12),
            ):
                written = harness._write_baseline({"noqa": [["E501"]]})
                content = (root / ".harness-baseline").read_text(encoding="utf-8")

        # Unknown key preserved, measured keys updated, inapplicable key dropped.
        self.assertEqual(written["custom.thing"], 7)
        self.assertEqual(written["coverage.min"], 88)
        self.assertEqual(written["complexity.max_violations"], 3)
        self.assertEqual(written["duplication.max_blocks"], 8)
        self.assertEqual(written["deadcode.max_findings"], 12)
        self.assertNotIn("crap.max_violations", written)
        self.assertIn("custom.thing 7", content)

    def test_write_baseline_zeroes_a_vanished_suppression_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".harness-baseline").write_text("suppressions.noqa 5\n", encoding="utf-8")
            with cwd(root), measurements():
                written = harness._write_baseline({})

        self.assertEqual(written["suppressions.noqa"], 0)

    def test_write_baseline_drops_an_inherited_floor_it_cannot_measure(self) -> None:
        # B5: the shipped template's `coverage.min 100` must never survive into an
        # adopting repo's first baseline just because coverage is not measurable there.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".harness-baseline").write_text("coverage.min 100\n", encoding="utf-8")
            with cwd(root), measurements():
                written = harness._write_baseline({})
                content = (root / ".harness-baseline").read_text(encoding="utf-8")

        self.assertNotIn("coverage.min", written)
        self.assertNotIn("coverage.min", content)

    def test_write_baseline_aborts_and_writes_nothing_on_a_failed_measurement(self) -> None:
        # B6: a floor recorded from a broken test run is worse than no floor.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = "coverage.min 40\n"
            (root / ".harness-baseline").write_text(original, encoding="utf-8")
            output = io.StringIO()
            with (
                cwd(root),
                measurements(coverage=harness.Measurement(error="the test run failed (exit 1)")),
                redirect_stdout(output),
                self.assertRaises(SystemExit) as ctx,
            ):
                harness._write_baseline({})
            content = (root / ".harness-baseline").read_text(encoding="utf-8")

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("coverage.min", output.getvalue())
        self.assertIn("the test run failed (exit 1)", output.getvalue())
        self.assertEqual(content, original)

    def test_measure_ratcheted_stops_at_the_first_failure(self) -> None:
        # CRAP re-runs the coverage suite, so measuring it after coverage already
        # failed produces noise, not information.
        with measurements(
            coverage=harness.Measurement(error="boom"), complexity=3, crap=4, deadcode=5
        ):
            measured = harness._measure_ratcheted()

        self.assertEqual(list(measured), ["coverage.min"])

    def test_complexity_gate_takes_its_violation_floor_from_the_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".harness-baseline").write_text(
                "complexity.max_violations 12\n", encoding="utf-8"
            )
            with cwd(root):
                gate = harness._complexity_gate()

        self.assertEqual(gate.cmd[gate.cmd.index("-i") + 1], "12")
        self.assertIn("baseline 12", gate.description)

    def test_lizard_warning_count_reads_the_summary_row(self) -> None:
        stdout = (
            "Total nloc   Avg.NLOC  AvgCCN  Avg.token   Fun Cnt  Warning cnt   Fun Rt   nloc Rt\n"
            "---------------------------------------------------------------------\n"
            "       889       6.1     1.2       51.6      117            4      0.00    0.00\n"
        )
        self.assertEqual(harness._lizard_warning_count(stdout), 4)

    def test_lizard_warning_count_returns_none_without_a_summary(self) -> None:
        self.assertIsNone(harness._lizard_warning_count("no summary here\n"))

    def test_measured_coverage_is_unavailable_without_tests(self) -> None:
        with mock.patch.object(harness, "_has_tests", return_value=False):
            measured = harness._measured_coverage()
        self.assertIsNone(measured.value)
        self.assertFalse(measured.error)
        self.assertIn("test", measured.unavailable)

    def test_measured_coverage_parses_total_percent(self) -> None:
        completed = subprocess.CompletedProcess([], returncode=0, stdout="87.6\n", stderr="")
        with (
            mock.patch.object(harness, "_has_tests", return_value=True),
            mock.patch.object(harness, "_artifact_is_fresh", return_value=True),
            mock.patch.object(harness.subprocess, "run", return_value=completed),
        ):
            self.assertEqual(harness._measured_coverage().value, 87)

    def test_measured_coverage_errors_when_the_test_run_fails(self) -> None:
        # B6: doghouse recorded `coverage.min 7` from a run where every test module
        # raised ImportError. A number from a failed run is not a measurement.
        completed = subprocess.CompletedProcess([], returncode=1, stdout="7\n", stderr="")
        with (
            mock.patch.object(harness, "_has_tests", return_value=True),
            mock.patch.object(harness, "_artifact_is_fresh", return_value=False),
            mock.patch.object(harness.subprocess, "run", return_value=completed),
        ):
            measured = harness._measured_coverage()

        self.assertIsNone(measured.value)
        self.assertIn("test run", measured.error)

    def test_crap_passes_when_offenders_stay_within_the_baseline(self) -> None:
        measurement = harness.CrapMeasurement([(462.0, 21, 0.0, "f@1-43@src/bad.py")])
        output = io.StringIO()
        with (
            mock.patch.object(harness, "_has_tests", return_value=True),
            mock.patch.object(harness, "_crap_measure", return_value=measurement),
            mock.patch.object(harness, "_read_baseline", return_value={"crap.max_violations": 1}),
            mock.patch.object(harness.sys, "argv", ["harness", "crap", "--enforce"]),
            redirect_stdout(output),
        ):
            harness.cmd_crap()

        self.assertIn("baseline 1", output.getvalue())
        self.assertIn("✓", output.getvalue())

    def test_crap_fails_when_offenders_exceed_the_baseline(self) -> None:
        measurement = harness.CrapMeasurement([(462.0, 21, 0.0, "f@1-43@src/bad.py")])
        output = io.StringIO()
        with (
            mock.patch.object(harness, "_has_tests", return_value=True),
            mock.patch.object(harness, "_crap_measure", return_value=measurement),
            mock.patch.object(harness, "_read_baseline", return_value={"crap.max_violations": 0}),
            mock.patch.object(harness.sys, "argv", ["harness", "crap", "--enforce"]),
            redirect_stdout(output),
            self.assertRaises(SystemExit),
        ):
            harness.cmd_crap()

        self.assertIn("baseline 0", output.getvalue())
        self.assertIn("✗", output.getvalue())

    def test_suppression_growth_fails_against_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("x = 1  # noqa: E501\n", encoding="utf-8")
            (root / ".harness-baseline").write_text("coverage.min 0\n", encoding="utf-8")
            with cwd(root):
                self.assertFalse(harness._check_suppressions_baseline(no_exit=True))


class TestReportOnlyWithoutABaseline(unittest.TestCase):
    """No `.harness-baseline` means no floor was ever measured — report, never block.

    A floor of 0 inferred from a missing number is not a floor, it is a demand that
    a legacy repo already be perfect, i.e. the day-one red that gets the harness
    deleted instead of adopted.
    """

    def test_complexity_gate_tolerates_everything_without_a_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, cwd(Path(tmp)):
            gate = harness._complexity_gate()

        self.assertEqual(gate.cmd[gate.cmd.index("-i") + 1], str(harness.REPORT_ONLY_LIMIT))
        self.assertIn("report-only", gate.description)

    def test_complexity_gate_is_report_only_when_the_key_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".harness-baseline").write_text("coverage.min 0\n", encoding="utf-8")
            with cwd(root):
                gate = harness._complexity_gate()

        self.assertEqual(gate.cmd[gate.cmd.index("-i") + 1], str(harness.REPORT_ONLY_LIMIT))

    def test_deadcode_reports_and_passes_without_a_baseline(self) -> None:
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as tmp,
            cwd(Path(tmp)),
            mock.patch.object(
                harness, "_run_deadcode", return_value=(harness.Measurement(value=1583), [])
            ),
            redirect_stdout(output),
        ):
            ok = harness._check_deadcode(no_exit=True)

        self.assertTrue(ok)
        self.assertIn("1583", output.getvalue())
        self.assertIn("report-only", output.getvalue())


class TestDuplicationRatchet(unittest.TestCase):
    """lizard's exit code ignores `-Eduplicate`, so the block count is judged here."""

    @staticmethod
    def _result(baseline: str | None, measurement, report: str = "") -> harness.GateResult:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if baseline is not None:
                (root / ".harness-baseline").write_text(baseline, encoding="utf-8")
            with (
                cwd(root),
                mock.patch.object(harness, "_run_duplication", return_value=(measurement, report)),
            ):
                gate = harness._duplication_gate()
                assert gate.runner is not None
                return gate.runner()

    def test_passes_at_the_floor(self) -> None:
        result = self._result("duplication.max_blocks 8\n", harness.Measurement(value=8))
        self.assertTrue(result.ok)
        self.assertIn("8 (baseline 8)", result.description)

    def test_fails_when_one_new_block_appears(self) -> None:
        report = "Duplicate block:\nsrc/a.py:1 ~ 17\nsrc/a.py:20 ~ 36\n"
        result = self._result("duplication.max_blocks 8\n", harness.Measurement(value=9), report)
        self.assertFalse(result.ok)
        self.assertIn("9 block(s) > baseline 8", result.description)
        self.assertEqual(result.stdout, report)
        self.assertEqual(result.hint, harness.DUPLICATION_HINT)

    def test_suggests_ratcheting_down_when_blocks_drop(self) -> None:
        result = self._result("duplication.max_blocks 8\n", harness.Measurement(value=5))
        self.assertTrue(result.ok)
        self.assertIn("ratchet down", result.description)

    def test_reports_and_passes_without_a_floor(self) -> None:
        result = self._result("coverage.min 0\n", harness.Measurement(value=1583))
        self.assertTrue(result.ok)
        self.assertIn("1583 block(s), report-only", result.description)

    def test_a_broken_lizard_fails_instead_of_reporting_zero(self) -> None:
        result = self._result(None, harness.Measurement(error="lizard failed to run (exit 2)"))
        self.assertFalse(result.ok)
        self.assertIn("lizard failed to run (exit 2)", result.description)

    def test_duplicate_block_count_counts_only_exact_headers(self) -> None:
        report = (
            "Duplicates\n===================================\n"
            "Duplicate block:\n--------------------------\n"
            "src/a.py:1 ~ 17\nsrc/a.py:20 ~ 36\n^^^^^^^^^^^^^^^^^^^^^^^^^^\n\n"
            "Duplicate block:\n--------------------------\n"
            "src/b.py:3 ~ 9\nsrc/c.py:3 ~ 9\n^^^^^^^^^^^^^^^^^^^^^^^^^^\n\n"
            "  Duplicate block: (indented, not a header)\n"
            "Total duplicate rate: 4.51%\n"
        )
        self.assertEqual(harness._duplicate_block_count(report), 2)

    def test_duplication_argv_shares_the_complexity_targets_and_disarms_lizard(self) -> None:
        # The recorded floor only reproduces against an identical target set, so pin
        # the two argvs to each other rather than to a hand-written list.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            with cwd(root):
                argv = harness._duplication_argv()
                complexity_argv = harness._complexity_argv(harness.REPORT_ONLY_LIMIT)
                targets = harness._app_targets(include_tests=True)

        # Everything before the trailing flags (4 here, 8 for complexity) is the
        # resolved lizard command plus the targets, and must match exactly.
        self.assertEqual(argv[:-4], complexity_argv[:-8])
        self.assertEqual(
            argv[-len(targets) - 4 :], [*targets, "-Eduplicate", "-w", "-i", "1000000"]
        )

    def test_measured_duplicate_blocks_errors_when_lizard_fails(self) -> None:
        failed = subprocess.CompletedProcess([], 2, "", "lizard: error")
        with (
            mock.patch.object(harness, "_app_targets", return_value=["src"]),
            mock.patch.object(harness.subprocess, "run", return_value=failed),
        ):
            measured = harness._measured_duplicate_blocks()

        self.assertEqual(measured.error, "lizard failed to run (exit 2)")

    def test_measured_duplicate_blocks_is_unavailable_without_app_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, cwd(Path(tmp)):
            measured = harness._measured_duplicate_blocks()

        self.assertIsNone(measured.value)
        self.assertEqual(measured.unavailable, "no app sources")


class TestDeadcodeRatchet(unittest.TestCase):
    """vulture has no `-i N`, so the count ratchet lives outside the Gate abstraction."""

    @staticmethod
    def _check(baseline: str | None, measurement, lines=()) -> tuple[bool, str]:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if baseline is not None:
                (root / ".harness-baseline").write_text(baseline, encoding="utf-8")
            with (
                cwd(root),
                mock.patch.object(
                    harness, "_run_deadcode", return_value=(measurement, list(lines))
                ),
                redirect_stdout(output),
            ):
                ok = harness._check_deadcode(no_exit=True)
        return ok, output.getvalue()

    def test_passes_at_the_floor(self) -> None:
        ok, out = self._check("deadcode.max_findings 1583\n", harness.Measurement(value=1583))
        self.assertTrue(ok)
        self.assertIn("1583 (baseline 1583)", out)

    def test_fails_when_one_new_finding_appears(self) -> None:
        ok, out = self._check(
            "deadcode.max_findings 1583\n",
            harness.Measurement(value=1584),
            ["src/a.py:9: unused function 'dead' (60% confidence)"],
        )
        self.assertFalse(ok)
        self.assertIn("1584 finding(s) > baseline 1583", out)
        self.assertIn("unused function 'dead'", out)

    def test_suggests_ratcheting_down_when_findings_drop(self) -> None:
        ok, out = self._check("deadcode.max_findings 10\n", harness.Measurement(value=4))
        self.assertTrue(ok)
        self.assertIn("--update-baseline", out)

    def test_a_broken_vulture_fails_instead_of_reporting_zero(self) -> None:
        ok, out = self._check(
            "deadcode.max_findings 0\n",
            harness.Measurement(error="vulture failed to run (exit 2)"),
        )
        self.assertFalse(ok)
        self.assertIn("vulture failed to run", out)

    def test_skips_without_app_sources(self) -> None:
        ok, out = self._check(None, harness.Measurement(unavailable="no app sources"))
        self.assertTrue(ok)
        self.assertIn("no app sources", out)

    def test_counts_one_finding_per_stdout_line(self) -> None:
        completed = subprocess.CompletedProcess(
            [],
            returncode=1,
            stdout="src/a.py:1: unused import 'os' (90% confidence)\n"
            "src/a.py:9: unused function 'dead' (60% confidence)\n\n",
            stderr="",
        )
        with (
            mock.patch.object(harness, "_app_targets", return_value=["src"]),
            mock.patch.object(harness.subprocess, "run", return_value=completed),
        ):
            measured, lines = harness._run_deadcode()

        self.assertEqual(measured.value, 2)
        self.assertEqual(len(lines), 2)

    def test_a_nonzero_exit_with_no_output_is_a_failure_not_a_clean_tree(self) -> None:
        completed = subprocess.CompletedProcess(
            [], returncode=2, stdout="", stderr="error: no such option\n"
        )
        with (
            mock.patch.object(harness, "_app_targets", return_value=["src"]),
            mock.patch.object(harness.subprocess, "run", return_value=completed),
        ):
            measured, _ = harness._run_deadcode()

        self.assertIsNone(measured.value)
        self.assertIn("exit 2", measured.error)


if __name__ == "__main__":
    unittest.main()
