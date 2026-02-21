"""Tests for entropy_meter.core.db — SQLite storage operations."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from entropy_meter.core.db import (
    SCHEMA_VERSION,
    Measurement,
    get_commit_summary,
    get_connection,
    get_file_history,
    get_hotspots,
    get_previous_measurement,
    get_trend,
    store_measurement,
)


def _make_measurement(
    file_path: str = "src/foo.py",
    commit_hash: str | None = "abc123",
    entropy_index: float = 42.0,
    measured_at: float | None = None,
    **overrides: object,
) -> Measurement:
    """Helper to build a Measurement with sensible defaults."""
    defaults: dict[str, object] = {
        "file_path": file_path,
        "commit_hash": commit_hash,
        "measured_at": measured_at or time.time(),
        "file_size_bytes": 200,
        "line_count": 10,
        "blank_lines": 2,
        "comment_lines": 1,
        "compression_ratio": 0.3,
        "line_length_stddev": 5.0,
        "cyclomatic_complexity": 3.0,
        "maintainability_index": 70.0,
        "halstead_volume": 150.0,
        "ast_node_count": None,
        "ast_depth_max": None,
        "ast_entropy": None,
        "entropy_index": entropy_index,
        "tier_mask": 0b011,
    }
    defaults.update(overrides)
    return Measurement(**defaults)  # type: ignore[arg-type]


class TestGetConnection:
    def test_get_connection_creates_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "new.db"
        assert not db_path.exists()
        conn = get_connection(db_path)
        try:
            assert db_path.exists()
            # Check permissions (0600)
            mode = db_path.stat().st_mode & 0o777
            assert mode == 0o600
        finally:
            conn.close()

    def test_schema_version(self, tmp_db: sqlite3.Connection) -> None:
        cur = tmp_db.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        assert row[0] == SCHEMA_VERSION


class TestStoreAndRetrieve:
    def test_store_and_retrieve(self, tmp_db: sqlite3.Connection) -> None:
        m = _make_measurement(commit_hash="commit_a")
        row_id = store_measurement(tmp_db, m)
        assert row_id > 0

        # Retrieve by commit
        cur = tmp_db.execute(
            "SELECT * FROM measurements WHERE commit_hash = ?", ("commit_a",)
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["file_path"] == "src/foo.py"
        assert rows[0]["entropy_index"] == 42.0

    def test_unique_constraint(self, tmp_db: sqlite3.Connection) -> None:
        """Storing the same file+commit should replace (INSERT OR REPLACE)."""
        m1 = _make_measurement(entropy_index=30.0)
        m2 = _make_measurement(entropy_index=50.0)
        store_measurement(tmp_db, m1)
        store_measurement(tmp_db, m2)

        cur = tmp_db.execute(
            "SELECT entropy_index FROM measurements WHERE file_path = ? AND commit_hash = ?",
            ("src/foo.py", "abc123"),
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["entropy_index"] == 50.0


class TestGetPreviousMeasurement:
    def test_get_previous_measurement(self, tmp_db: sqlite3.Connection) -> None:
        t_base = 1000.0
        m_old = _make_measurement(
            commit_hash="old_commit", entropy_index=20.0, measured_at=t_base
        )
        m_new = _make_measurement(
            commit_hash="new_commit", entropy_index=45.0, measured_at=t_base + 100
        )
        store_measurement(tmp_db, m_old)
        store_measurement(tmp_db, m_new)

        prev = get_previous_measurement(tmp_db, "src/foo.py", before_commit="new_commit")
        assert prev is not None
        assert prev.commit_hash == "old_commit"
        assert prev.entropy_index == 20.0


class TestGetHotspots:
    def test_get_hotspots(self, tmp_db: sqlite3.Connection) -> None:
        t = 1000.0
        store_measurement(
            tmp_db, _make_measurement("high.py", "c1", entropy_index=90.0, measured_at=t)
        )
        store_measurement(
            tmp_db, _make_measurement("low.py", "c1", entropy_index=10.0, measured_at=t)
        )
        store_measurement(
            tmp_db, _make_measurement("mid.py", "c1", entropy_index=50.0, measured_at=t)
        )

        hotspots = get_hotspots(tmp_db, limit=2)
        assert len(hotspots) == 2
        assert hotspots[0]["file_path"] == "high.py"
        assert hotspots[0]["entropy_index"] == 90.0
        assert hotspots[1]["file_path"] == "mid.py"


class TestGetTrend:
    def test_get_trend(self, tmp_db: sqlite3.Connection) -> None:
        t = 1000.0
        store_measurement(
            tmp_db, _make_measurement("a.py", "commit_1", entropy_index=30.0, measured_at=t)
        )
        store_measurement(
            tmp_db,
            _make_measurement("b.py", "commit_1", entropy_index=40.0, measured_at=t),
        )
        store_measurement(
            tmp_db,
            _make_measurement("a.py", "commit_2", entropy_index=50.0, measured_at=t + 100),
        )

        trend = get_trend(tmp_db, last_n_commits=10)
        assert len(trend) == 2
        # Most recent commit first
        assert trend[0]["commit_hash"] == "commit_2"
        assert trend[1]["commit_hash"] == "commit_1"


class TestGetFileHistory:
    def test_get_file_history(self, tmp_db: sqlite3.Connection) -> None:
        t = 1000.0
        store_measurement(
            tmp_db, _make_measurement("track.py", "c1", entropy_index=20.0, measured_at=t)
        )
        store_measurement(
            tmp_db,
            _make_measurement("track.py", "c2", entropy_index=35.0, measured_at=t + 100),
        )
        store_measurement(
            tmp_db,
            _make_measurement("track.py", "c3", entropy_index=50.0, measured_at=t + 200),
        )

        history = get_file_history(tmp_db, "track.py", limit=10)
        assert len(history) == 3
        # Newest first
        assert history[0].entropy_index == 50.0
        assert history[2].entropy_index == 20.0


class TestGetCommitSummary:
    def test_get_commit_summary(self, tmp_db: sqlite3.Connection) -> None:
        t = 1000.0
        store_measurement(
            tmp_db, _make_measurement("x.py", "sum_c", entropy_index=40.0, measured_at=t)
        )
        store_measurement(
            tmp_db, _make_measurement("y.py", "sum_c", entropy_index=60.0, measured_at=t)
        )

        summary = get_commit_summary(tmp_db, "sum_c")
        assert summary["commit"] == "sum_c"
        assert summary["files"] == 2
        assert summary["avg_ei"] == 50.0

    def test_get_commit_summary_empty(self, tmp_db: sqlite3.Connection) -> None:
        summary = get_commit_summary(tmp_db, "nonexistent")
        assert summary["files"] == 0
        assert summary["avg_ei"] == 0.0
