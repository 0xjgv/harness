"""Property-based tests for the pure helpers in harness.py.

Worked example for the template's PBT convention: law-like behavior
(formulas, parsers, round-trips) gets a property, not just examples.
Examples pin known cases; properties pin the law.
"""

import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from harness import _crap_score, _parse_coverage_xml, _parse_line_for_suppressions

ccns = st.integers(min_value=1, max_value=100)
covs = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


class TestCrapScoreProperties(unittest.TestCase):
    @given(ccn=ccns)
    def test_full_coverage_collapses_to_ccn(self, ccn):
        self.assertEqual(_crap_score(ccn, 1.0), float(ccn))

    @given(ccn=ccns, cov=covs)
    def test_bounded_below_by_ccn_above_by_zero_coverage(self, ccn, cov):
        score = _crap_score(ccn, cov)
        self.assertGreaterEqual(score, float(ccn))
        self.assertLessEqual(score, float(ccn * ccn + ccn))

    @given(ccn=ccns, a=covs, b=covs)
    def test_more_coverage_never_raises_score(self, ccn, a, b):
        lo, hi = min(a, b), max(a, b)
        self.assertGreaterEqual(_crap_score(ccn, lo), _crap_score(ccn, hi))

    @given(a=ccns, b=ccns, cov=covs)
    def test_more_complexity_never_lowers_score(self, a, b, cov):
        lo, hi = min(a, b), max(a, b)
        self.assertLessEqual(_crap_score(lo, cov), _crap_score(hi, cov))


class TestSuppressionParserProperties(unittest.TestCase):
    @given(line=st.text())
    def test_total_on_arbitrary_text(self, line):
        # Never raises; every match has a known kind and a list of rules.
        for kind, rules in _parse_line_for_suppressions(line):
            self.assertIn(kind, {"noqa", "type_ignore", "pyright_ignore"})
            self.assertIsInstance(rules, list)

    @given(line=st.text(alphabet=st.characters(exclude_characters="#")))
    def test_no_comment_marker_means_no_match(self, line):
        self.assertEqual(_parse_line_for_suppressions(line), [])

    @given(
        rules=st.lists(
            st.from_regex(r"[A-Z][A-Z0-9]{1,5}", fullmatch=True),
            min_size=1,
            max_size=4,
        )
    )
    def test_noqa_rules_round_trip(self, rules):
        line = f"x = 1  # noqa: {', '.join(rules)}"
        self.assertIn(("noqa", rules), _parse_line_for_suppressions(line))


class TestParseCoverageXmlProperties(unittest.TestCase):
    @given(
        lines=st.dictionaries(
            st.integers(min_value=1, max_value=10_000),
            st.integers(min_value=0, max_value=1_000),
            min_size=1,
            max_size=20,
        )
    )
    def test_generated_cobertura_round_trips(self, lines):
        body = "".join(f'<line number="{n}" hits="{h}"/>' for n, h in lines.items())
        xml = (
            '<?xml version="1.0" ?><coverage><packages><package>'
            '<classes><class filename="src/foo.py"><lines>'
            f"{body}"
            "</lines></class></classes></package></packages></coverage>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coverage.xml"
            path.write_text(xml, encoding="utf-8")
            self.assertEqual(_parse_coverage_xml(path), {"src/foo.py": lines})


if __name__ == "__main__":
    unittest.main()
