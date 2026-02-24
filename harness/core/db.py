"""SQLite storage for entropy measurements (WAL mode, sequential migrations, 0600 perms)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness.config import get_db_path

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

CREATE TABLE IF NOT EXISTS measurements (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path         TEXT NOT NULL,
    commit_hash       TEXT,
    measured_at       REAL NOT NULL,
    -- Tier 0
    file_size_bytes   INTEGER NOT NULL,
    line_count        INTEGER NOT NULL,
    blank_lines       INTEGER NOT NULL,
    comment_lines     INTEGER NOT NULL,
    compression_ratio REAL NOT NULL,
    line_length_stddev REAL NOT NULL,
    -- Tier 1 (NULL if unavailable)
    cyclomatic_complexity REAL,
    maintainability_index REAL,
    halstead_volume       REAL,
    -- Tier 2 (NULL if unavailable)
    ast_node_count    INTEGER,
    ast_depth_max     INTEGER,
    ast_entropy       REAL,
    -- Composite
    entropy_index     REAL NOT NULL,
    tier_mask         INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_meas_file_commit
    ON measurements(file_path, commit_hash);
CREATE INDEX IF NOT EXISTS idx_meas_commit ON measurements(commit_hash);
CREATE INDEX IF NOT EXISTS idx_meas_measured_at ON measurements(measured_at);
"""


@dataclass
class Measurement:
    """A single file measurement record."""

    file_path: str
    commit_hash: str | None
    measured_at: float
    file_size_bytes: int
    line_count: int
    blank_lines: int
    comment_lines: int
    compression_ratio: float
    line_length_stddev: float
    cyclomatic_complexity: float | None
    maintainability_index: float | None
    halstead_volume: float | None
    ast_node_count: int | None
    ast_depth_max: int | None
    ast_entropy: float | None
    entropy_index: float
    tier_mask: int
    id: int | None = None


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the entropy DB with WAL mode and 0600 perms."""
    path = db_path or get_db_path()
    is_new = not path.exists()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    if is_new:
        path.chmod(0o600)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Run schema migrations sequentially."""
    # Check if schema_version table exists
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if cur.fetchone() is None:
        conn.executescript(SCHEMA_V1)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
        return

    cur = conn.execute("SELECT MAX(version) FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row and row[0] else 0

    if current < SCHEMA_VERSION:
        # Future migrations go here
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()


_UPSERT_SQL = """INSERT OR REPLACE INTO measurements (
    file_path, commit_hash, measured_at,
    file_size_bytes, line_count, blank_lines, comment_lines,
    compression_ratio, line_length_stddev,
    cyclomatic_complexity, maintainability_index, halstead_volume,
    ast_node_count, ast_depth_max, ast_entropy,
    entropy_index, tier_mask
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def _measurement_params(m: Measurement) -> tuple[Any, ...]:
    """Build the parameter tuple for a Measurement upsert."""
    return (
        m.file_path,
        m.commit_hash,
        m.measured_at,
        m.file_size_bytes,
        m.line_count,
        m.blank_lines,
        m.comment_lines,
        m.compression_ratio,
        m.line_length_stddev,
        m.cyclomatic_complexity,
        m.maintainability_index,
        m.halstead_volume,
        m.ast_node_count,
        m.ast_depth_max,
        m.ast_entropy,
        m.entropy_index,
        m.tier_mask,
    )


def store_measurement(conn: sqlite3.Connection, m: Measurement) -> int:
    """Insert or replace a measurement. Returns the row ID."""
    cur = conn.execute(_UPSERT_SQL, _measurement_params(m))
    conn.commit()
    return cur.lastrowid or 0


def store_measurements_batch(conn: sqlite3.Connection, measurements: list[Measurement]) -> int:
    """Insert or replace multiple measurements in a single transaction.

    Returns the count of rows stored.
    """
    if not measurements:
        return 0
    with conn:
        for m in measurements:
            conn.execute(_UPSERT_SQL, _measurement_params(m))
    return len(measurements)


def get_previous_measurement(
    conn: sqlite3.Connection, file_path: str, before_commit: str | None = None
) -> Measurement | None:
    """Get the most recent measurement for a file before a given commit."""
    if before_commit:
        cur = conn.execute(
            """SELECT * FROM measurements
            WHERE file_path = ? AND commit_hash != ?
            ORDER BY measured_at DESC LIMIT 1""",
            (file_path, before_commit),
        )
    else:
        cur = conn.execute(
            """SELECT * FROM measurements
            WHERE file_path = ?
            ORDER BY measured_at DESC LIMIT 1""",
            (file_path,),
        )
    row = cur.fetchone()
    return _row_to_measurement(row) if row else None


def get_measurements_by_commit(conn: sqlite3.Connection, commit_hash: str) -> list[Measurement]:
    """Get all measurements for a specific commit."""
    cur = conn.execute(
        "SELECT * FROM measurements WHERE commit_hash = ? ORDER BY file_path",
        (commit_hash,),
    )
    return [_row_to_measurement(row) for row in cur.fetchall()]


def get_recent_measurements(conn: sqlite3.Connection, limit: int = 50) -> list[Measurement]:
    """Get recent measurements ordered by time descending."""
    cur = conn.execute(
        "SELECT * FROM measurements ORDER BY measured_at DESC LIMIT ?",
        (limit,),
    )
    return [_row_to_measurement(row) for row in cur.fetchall()]


def get_file_history(
    conn: sqlite3.Connection, file_path: str, limit: int = 20
) -> list[Measurement]:
    """Get measurement history for a specific file."""
    cur = conn.execute(
        """SELECT * FROM measurements
        WHERE file_path = ?
        ORDER BY measured_at DESC LIMIT ?""",
        (file_path, limit),
    )
    return [_row_to_measurement(row) for row in cur.fetchall()]


def get_commit_summary(conn: sqlite3.Connection, commit_hash: str) -> dict[str, Any]:
    """Get aggregate stats for a commit: avg EI, file count, total delta."""
    measurements = get_measurements_by_commit(conn, commit_hash)
    if not measurements:
        return {"commit": commit_hash, "files": 0, "avg_ei": 0.0, "total_delta": 0.0}

    avg_ei = sum(m.entropy_index for m in measurements) / len(measurements)
    total_delta = 0.0
    for m in measurements:
        prev = get_previous_measurement(conn, m.file_path, commit_hash)
        if prev:
            total_delta += m.entropy_index - prev.entropy_index

    return {
        "commit": commit_hash,
        "files": len(measurements),
        "avg_ei": round(avg_ei, 1),
        "total_delta": round(total_delta, 1),
    }


def get_hotspots(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Get files with highest current entropy index."""
    cur = conn.execute(
        """SELECT file_path, entropy_index, commit_hash, measured_at
        FROM measurements m1
        WHERE measured_at = (
            SELECT MAX(measured_at) FROM measurements m2
            WHERE m2.file_path = m1.file_path
        )
        ORDER BY entropy_index DESC
        LIMIT ?""",
        (limit,),
    )
    return [dict(row) for row in cur.fetchall()]


def get_trend(conn: sqlite3.Connection, last_n_commits: int = 10) -> list[dict[str, Any]]:
    """Get per-commit average EI for the last N distinct commits."""
    cur = conn.execute(
        """SELECT commit_hash, AVG(entropy_index) as avg_ei,
                  COUNT(*) as file_count, MIN(measured_at) as measured_at
        FROM measurements
        WHERE commit_hash IS NOT NULL
        GROUP BY commit_hash
        ORDER BY measured_at DESC
        LIMIT ?""",
        (last_n_commits,),
    )
    return [dict(row) for row in cur.fetchall()]


def _row_to_measurement(row: sqlite3.Row) -> Measurement:
    """Convert a DB row to a Measurement dataclass."""
    return Measurement(
        id=row["id"],
        file_path=row["file_path"],
        commit_hash=row["commit_hash"],
        measured_at=row["measured_at"],
        file_size_bytes=row["file_size_bytes"],
        line_count=row["line_count"],
        blank_lines=row["blank_lines"],
        comment_lines=row["comment_lines"],
        compression_ratio=row["compression_ratio"],
        line_length_stddev=row["line_length_stddev"],
        cyclomatic_complexity=row["cyclomatic_complexity"],
        maintainability_index=row["maintainability_index"],
        halstead_volume=row["halstead_volume"],
        ast_node_count=row["ast_node_count"],
        ast_depth_max=row["ast_depth_max"],
        ast_entropy=row["ast_entropy"],
        entropy_index=row["entropy_index"],
        tier_mask=row["tier_mask"],
    )
