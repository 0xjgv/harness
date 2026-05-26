"""Tests for CRAP formula and coverage XML parser in harness.py."""

import tempfile
import unittest
from pathlib import Path

from harness import _crap_score, _parse_coverage_xml


class TestCrapScore(unittest.TestCase):
    def test_full_coverage_returns_ccn(self) -> None:
        # cov=1.0 -> (1-cov)^3 = 0, so crap = ccn
        self.assertEqual(_crap_score(10, 1.0), 10.0)

    def test_zero_coverage_returns_ccn_squared_plus_ccn(self) -> None:
        # cov=0.0 -> crap = ccn^2 + ccn = 100 + 10 = 110
        self.assertEqual(_crap_score(10, 0.0), 110.0)

    def test_half_coverage(self) -> None:
        # cov=0.5 -> (1-0.5)^3 = 0.125 -> 100 * 0.125 + 10 = 22.5
        self.assertEqual(_crap_score(10, 0.5), 22.5)

    def test_zero_coverage_ccn_one(self) -> None:
        # cov=0.0, ccn=1 -> 1 + 1 = 2
        self.assertEqual(_crap_score(1, 0.0), 2.0)


class TestParseCoverageXml(unittest.TestCase):
    def test_parses_cobertura_lines(self) -> None:
        xml = (
            '<?xml version="1.0" ?>'
            "<coverage>"
            "<packages><package>"
            '<classes><class filename="src/foo.py">'
            "<lines>"
            '<line number="1" hits="3"/>'
            '<line number="2" hits="0"/>'
            '<line number="5" hits="1"/>'
            "</lines>"
            "</class></classes>"
            "</package></packages>"
            "</coverage>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coverage.xml"
            path.write_text(xml, encoding="utf-8")
            result = _parse_coverage_xml(path)

        self.assertEqual(result, {"src/foo.py": {1: 3, 2: 0, 5: 1}})


if __name__ == "__main__":
    unittest.main()
