"""Tests for entropy_meter.cli.report — in-process testing via monkeypatch + capsys."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from entropy_meter.cli.report import main
from entropy_meter.core.db import Measurement, get_connection, store_measurement

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_report(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Run report main() with patched sys.argv."""
    monkeypatch.setattr("sys.argv", ["entropy-report", *argv])
    main()


def _seed_db(db_path: Path, count: int = 3) -> None:
    """Create a DB with sample measurements for testing reports."""
    conn = get_connection(db_path)
    base_time = time.time()
    for i in range(count):
        m = Measurement(
            file_path=f"pkg/mod{i}.py",
            commit_hash=f"aaa{i:04d}",
            measured_at=base_time + i,
            file_size_bytes=100 + i * 10,
            line_count=10 + i,
            blank_lines=2,
            comment_lines=1,
            compression_ratio=0.3 + i * 0.05,
            line_length_stddev=5.0,
            cyclomatic_complexity=2.0 + i,
            maintainability_index=80.0 - i * 5,
            halstead_volume=100.0 + i * 50,
            ast_node_count=50 + i * 10,
            ast_depth_max=5 + i,
            ast_entropy=2.5 + i * 0.3,
            entropy_index=30.0 + i * 10,
            tier_mask=7,
        )
        store_measurement(conn, m)
    conn.close()


# ---------------------------------------------------------------------------
# No DB exists yet
# ---------------------------------------------------------------------------


class TestReportNoDb:
    def test_no_db_error_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When no DB exists, report should print error to stderr and exit 1."""
        with pytest.raises(SystemExit) as exc_info:
            _run_report(monkeypatch, ["--project-root", str(tmp_path)])
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "No entropy database found" in err


# ---------------------------------------------------------------------------
# Default trend view
# ---------------------------------------------------------------------------


class TestReportTrend:
    def test_default_trend_view(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Default view should show per-commit trend table."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        _seed_db(db_dir / "entropy.db")

        _run_report(monkeypatch, ["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Commit" in out
        assert "Avg EI" in out

    def test_trend_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json on trend view should produce valid JSON."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        _seed_db(db_dir / "entropy.db")

        _run_report(monkeypatch, ["--json", "--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)

    def test_trend_empty_db(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Trend with empty DB should show 'no data' message."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        # Create empty DB (no measurements)
        conn = get_connection(db_dir / "entropy.db")
        conn.close()

        _run_report(monkeypatch, ["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "No trend data" in out


# ---------------------------------------------------------------------------
# --hotspots view
# ---------------------------------------------------------------------------


class TestReportHotspots:
    def test_hotspots_view(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--hotspots should show files with highest EI."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        _seed_db(db_dir / "entropy.db")

        _run_report(
            monkeypatch,
            ["--hotspots", "--project-root", str(tmp_path)],
        )
        out = capsys.readouterr().out
        assert "File" in out
        assert "EI" in out

    def test_hotspots_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--hotspots --json should produce valid JSON."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        _seed_db(db_dir / "entropy.db")

        _run_report(
            monkeypatch,
            ["--hotspots", "--json", "--project-root", str(tmp_path)],
        )
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "file_path" in data[0]
        assert "entropy_index" in data[0]

    def test_hotspots_empty_db(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--hotspots on empty DB should show 'no data' message."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        conn = get_connection(db_dir / "entropy.db")
        conn.close()

        _run_report(
            monkeypatch,
            ["--hotspots", "--project-root", str(tmp_path)],
        )
        out = capsys.readouterr().out
        assert "No hotspot data" in out


# ---------------------------------------------------------------------------
# --file view
# ---------------------------------------------------------------------------


class TestReportFileHistory:
    def test_file_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--file should show measurement history for a specific file."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        _seed_db(db_dir / "entropy.db")

        _run_report(
            monkeypatch,
            ["--file", "pkg/mod0.py", "--project-root", str(tmp_path)],
        )
        out = capsys.readouterr().out
        assert "History for" in out
        assert "pkg/mod0.py" in out

    def test_file_history_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--file --json should produce valid JSON."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        _seed_db(db_dir / "entropy.db")

        _run_report(
            monkeypatch,
            [
                "--file",
                "pkg/mod0.py",
                "--json",
                "--project-root",
                str(tmp_path),
            ],
        )
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "entropy_index" in data[0]

    def test_file_history_not_found(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--file for nonexistent file should show 'no history' message."""
        db_dir = tmp_path / ".claude"
        db_dir.mkdir()
        _seed_db(db_dir / "entropy.db")

        _run_report(
            monkeypatch,
            [
                "--file",
                "nonexistent.py",
                "--project-root",
                str(tmp_path),
            ],
        )
        out = capsys.readouterr().out
        assert "No history" in out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestReportHelpers:
    def test_short_hash_none(self) -> None:
        from entropy_meter.cli.report import _short_hash

        assert _short_hash(None) == "-------"

    def test_short_hash_value(self) -> None:
        from entropy_meter.cli.report import _short_hash

        assert _short_hash("abc1234567890") == "abc1234"

    def test_print_trend_with_deltas(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Trend with multiple commits should show deltas."""
        from entropy_meter.cli.report import _print_trend

        data = [
            {"commit_hash": "aaa0002", "file_count": 3, "avg_ei": 50.0},
            {"commit_hash": "aaa0001", "file_count": 3, "avg_ei": 40.0},
            {"commit_hash": "aaa0000", "file_count": 3, "avg_ei": 35.0},
        ]
        _print_trend(data, output_json=False)
        out = capsys.readouterr().out
        assert "+10.0" in out
        assert "+5.0" in out
        assert "---" in out  # oldest commit has no delta
