"""Tests for the suppression scanner in harness.py."""

import os
import tempfile
import unittest
from contextlib import contextmanager
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

    def test_suppression_growth_fails_against_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("x = 1  # noqa: E501\n", encoding="utf-8")
            (root / ".harness-baseline").write_text("coverage.min 0\n", encoding="utf-8")
            with cwd(root):
                self.assertFalse(harness._check_suppressions_baseline(no_exit=True))


if __name__ == "__main__":
    unittest.main()
