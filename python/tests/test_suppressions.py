"""Tests for the suppression scanner in harness.py."""

import tempfile
import unittest
from pathlib import Path

from harness import _parse_line_for_suppressions, _scan_suppressions


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


if __name__ == "__main__":
    unittest.main()
