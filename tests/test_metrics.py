"""Tests for harness.core.metrics — FileMetrics and measure_file."""

from __future__ import annotations

from pathlib import Path

from harness.core.metrics import FileMetrics, measure_file


class TestMeasureSimpleCode:
    """measure_file on simple content should populate all Tier 0 fields."""

    def test_measure_simple_code(self, tmp_path: Path, sample_python_code: str) -> None:
        f = tmp_path / "simple.py"
        f.write_text(sample_python_code)
        m = measure_file(f)

        assert isinstance(m, FileMetrics)
        assert m.file_size_bytes > 0
        assert m.line_count > 0
        # compression_ratio can be negative for very small files (gzip overhead)
        assert m.compression_ratio <= 1.0
        assert m.line_entropy >= 0.0
        assert m.line_length_stddev >= 0.0
        # Tier mask should include at least Tier 0
        assert m.tier_mask & 0b001

    def test_measure_empty_content(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.py"
        f.write_text("")
        m = measure_file(f)

        assert m.file_size_bytes == 0
        assert m.line_count == 0
        assert m.blank_lines == 0
        assert m.comment_lines == 0
        assert m.compression_ratio == 0.0
        assert m.line_entropy == 0.0
        assert m.line_length_stddev == 0.0

    def test_measure_blank_lines_counted(self, tmp_path: Path) -> None:
        content = "line1\n\nline3\n\n\nline6\n"
        f = tmp_path / "blanks.py"
        f.write_text(content)
        m = measure_file(f)

        # Lines: "line1", "", "line3", "", "", "line6" => 3 blank lines
        assert m.blank_lines == 3
        assert m.line_count == 6

    def test_measure_comment_lines_counted(self, tmp_path: Path) -> None:
        content = "# comment one\nx = 1\n# comment two\n  # indented comment\n"
        f = tmp_path / "comments.py"
        f.write_text(content)
        m = measure_file(f)

        # "# comment one", "# comment two", "  # indented comment" => 3 comment lines
        assert m.comment_lines == 3

    def test_compression_ratio_range(self, tmp_path: Path, complex_python_code: str) -> None:
        # Use complex code — large enough that gzip overhead doesn't dominate
        f = tmp_path / "comp.py"
        f.write_text(complex_python_code)
        m = measure_file(f)

        # For sufficiently large files the ratio is between 0 and 1
        assert 0.0 <= m.compression_ratio <= 1.0

    def test_line_entropy_positive(self, tmp_path: Path, sample_python_code: str) -> None:
        f = tmp_path / "ent.py"
        f.write_text(sample_python_code)
        m = measure_file(f)

        assert m.line_entropy > 0.0

    def test_line_length_stddev_uniform(self, tmp_path: Path) -> None:
        # Lines of exactly equal length should produce stddev 0
        content = "aaaa\nbbbb\ncccc\n"
        f = tmp_path / "uniform.py"
        f.write_text(content)
        m = measure_file(f)

        assert m.line_length_stddev == 0.0

    def test_measure_from_path(self, tmp_path: Path) -> None:
        content = "x = 42\ny = x + 1\n"
        f = tmp_path / "frompath.py"
        f.write_text(content)
        m = measure_file(f)

        assert m.line_count == 2
        assert m.file_size_bytes == len(content.encode("utf-8"))

    def test_measure_with_content_arg(self, tmp_path: Path) -> None:
        """When content is supplied directly, path is not read."""
        f = tmp_path / "dummy.py"
        # Don't write anything — content arg should be used instead
        f.write_text("should not be read")
        content = "a = 1\n"
        m = measure_file(f, content=content)

        assert m.line_count == 1
        assert m.file_size_bytes == len(content.encode("utf-8"))
