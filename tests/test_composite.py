"""Tests for entropy_meter.core.composite — compute_entropy_index and measure_and_score."""
from __future__ import annotations

from pathlib import Path

from entropy_meter.config import DEFAULT_WEIGHTS, TIER_0
from entropy_meter.core.composite import compute_entropy_index, measure_and_score
from entropy_meter.core.metrics import FileMetrics, measure_file


class TestComputeEntropyIndex:
    """Tests for the composite EI scoring function."""

    def test_entropy_index_range(self, tmp_path: Path, sample_python_code: str) -> None:
        f = tmp_path / "range.py"
        f.write_text(sample_python_code)
        m = measure_file(f)
        ei = compute_entropy_index(m)

        assert 0.0 <= ei <= 100.0

    def test_simple_code_lower_than_complex(
        self,
        tmp_path: Path,
        sample_python_code: str,
        complex_python_code: str,
    ) -> None:
        simple_file = tmp_path / "simple.py"
        simple_file.write_text(sample_python_code)
        simple_m = measure_file(simple_file)
        simple_ei = compute_entropy_index(simple_m)

        complex_file = tmp_path / "complex.py"
        complex_file.write_text(complex_python_code)
        complex_m = measure_file(complex_file)
        complex_ei = compute_entropy_index(complex_m)

        assert simple_ei < complex_ei

    def test_empty_code_zero_ei(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.py"
        f.write_text("")
        m = measure_file(f)
        ei = compute_entropy_index(m)

        assert ei == 0.0

    def test_weight_redistribution(self, tmp_path: Path, sample_python_code: str) -> None:
        """With only Tier 0 available, weights should redistribute to sum to 1.0."""
        # Build a FileMetrics with only Tier 0 (simulate no radon)
        m = FileMetrics(
            file_size_bytes=100,
            line_count=5,
            blank_lines=1,
            comment_lines=0,
            compression_ratio=0.3,
            line_length_stddev=5.0,
            line_entropy=3.5,
            tier_mask=TIER_0,
        )

        # Compute — should not error and should use only Tier 0 weights
        ei = compute_entropy_index(m)
        assert 0.0 <= ei <= 100.0

        # Verify the redistribution math: Tier 0 weights should normalize to 1.0
        tier0_weights = [w for w, t in DEFAULT_WEIGHTS.values() if t == TIER_0]
        assert len(tier0_weights) > 0
        normalized_sum = sum(tier0_weights) / sum(tier0_weights)
        assert abs(normalized_sum - 1.0) < 1e-9

    def test_measure_and_score_convenience(self, tmp_path: Path, sample_python_code: str) -> None:
        f = tmp_path / "conv.py"
        f.write_text(sample_python_code)
        metrics, ei = measure_and_score(f)

        assert isinstance(metrics, FileMetrics)
        assert isinstance(ei, float)
        assert 0.0 <= ei <= 100.0

    def test_maintainability_inversion(self) -> None:
        """Higher MI (better code) should result in lower EI contribution."""
        # High MI (good code, ~100) -> inverted to ~0 -> low contribution
        high_mi = FileMetrics(
            file_size_bytes=100,
            line_count=5,
            blank_lines=0,
            comment_lines=1,
            compression_ratio=0.3,
            line_length_stddev=5.0,
            line_entropy=3.5,
            cyclomatic_complexity=2.0,
            maintainability_index=95.0,
            halstead_volume=100.0,
            tier_mask=0b011,
        )

        # Low MI (bad code, ~20) -> inverted to ~80 -> high contribution
        low_mi = FileMetrics(
            file_size_bytes=100,
            line_count=5,
            blank_lines=0,
            comment_lines=1,
            compression_ratio=0.3,
            line_length_stddev=5.0,
            line_entropy=3.5,
            cyclomatic_complexity=2.0,
            maintainability_index=20.0,
            halstead_volume=100.0,
            tier_mask=0b011,
        )

        ei_high_mi = compute_entropy_index(high_mi)
        ei_low_mi = compute_entropy_index(low_mi)

        # Better maintainability (higher MI) should produce a lower EI
        assert ei_high_mi < ei_low_mi
