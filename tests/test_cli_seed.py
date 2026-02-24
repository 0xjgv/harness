"""Tests for harness.cli.seed — seed baseline entropy measurements."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from harness.cli.seed import seed_main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_HASH = "abc1234567890abcdef"


def _create_py_files(root: Path, count: int = 3) -> list[Path]:
    """Create simple Python files in the given directory."""
    files = []
    for i in range(count):
        f = root / f"mod{i}.py"
        f.write_text(f"def func{i}():\n    return {i}\n")
        files.append(f)
    return files


# ---------------------------------------------------------------------------
# Basic flow
# ---------------------------------------------------------------------------


class TestSeedBasicFlow:
    def test_seed_measures_all_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _create_py_files(tmp_path, 3)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Seeded 3 file(s)" in out
        assert "avg EI:" in out

        # Verify DB has 3 rows
        db_path = tmp_path / ".claude" / "entropy.db"
        assert db_path.exists()
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()
        assert rows[0] == 3
        conn.close()

    def test_seed_shows_distribution(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _create_py_files(tmp_path, 2)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Distribution:" in out

    def test_seed_shows_commit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _create_py_files(tmp_path, 1)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Commit: abc123456789" in out


# ---------------------------------------------------------------------------
# Empty project
# ---------------------------------------------------------------------------


class TestSeedEmptyProject:
    def test_no_py_files_exits_cleanly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "readme.txt").write_text("hello")
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        with pytest.raises(SystemExit) as exc_info:
            seed_main(["--project-root", str(tmp_path)])
        assert exc_info.value.code == 0
        err = capsys.readouterr().err
        assert "No Python files found" in err


# ---------------------------------------------------------------------------
# Idempotent re-seed
# ---------------------------------------------------------------------------


class TestSeedIdempotent:
    def test_same_commit_is_idempotent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _create_py_files(tmp_path, 2)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--project-root", str(tmp_path)])
        capsys.readouterr()

        # Seed again on same commit
        seed_main(["--project-root", str(tmp_path)])

        db_path = tmp_path / ".claude" / "entropy.db"
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()
        assert rows[0] == 2  # INSERT OR REPLACE keeps count the same
        conn.close()


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestSeedJsonOutput:
    def test_json_flag_produces_valid_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _create_py_files(tmp_path, 2)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--json", "--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 2
        assert "entropy_index" in data[0]
        assert "file" in data[0]
        assert "tier" in data[0]
        assert data[0]["commit"] == FAKE_HASH


# ---------------------------------------------------------------------------
# No git repo
# ---------------------------------------------------------------------------


class TestSeedNoGitRepo:
    def test_seed_without_git(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _create_py_files(tmp_path, 1)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: None,
        )

        seed_main(["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Seeded 1 file(s)" in out
        assert "no git repo detected" in out

        # Verify commit_hash is NULL in DB
        db_path = tmp_path / ".claude" / "entropy.db"
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT commit_hash FROM measurements LIMIT 1").fetchone()
        assert row[0] is None
        conn.close()


# ---------------------------------------------------------------------------
# --project-root flag
# ---------------------------------------------------------------------------


class TestSeedProjectRoot:
    def test_project_root_scopes_collection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        sub = tmp_path / "subproject"
        sub.mkdir()
        (sub / "a.py").write_text("x = 1\n")
        # File outside subproject should not be collected
        (tmp_path / "outside.py").write_text("y = 2\n")

        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--project-root", str(sub)])
        out = capsys.readouterr().out
        assert "Seeded 1 file(s)" in out


# ---------------------------------------------------------------------------
# Worker function
# ---------------------------------------------------------------------------


class TestMeasureOneWorker:
    def test_success(self, tmp_path: Path) -> None:
        from harness.cli.seed import _measure_one, _SeedResult

        f = tmp_path / "ok.py"
        f.write_text("x = 1\n")
        result = _measure_one((str(f), str(tmp_path)))
        assert isinstance(result, _SeedResult)
        assert result.rel_path == "ok.py"
        assert result.entropy_index >= 0

    def test_failure_returns_tuple(self, tmp_path: Path) -> None:
        from harness.cli.seed import _measure_one

        missing = tmp_path / "gone.py"
        result = _measure_one((str(missing), str(tmp_path)))
        assert isinstance(result, tuple)
        rel_path, error_msg = result
        assert rel_path == "gone.py"
        assert isinstance(error_msg, str)


# ---------------------------------------------------------------------------
# Parallel path
# ---------------------------------------------------------------------------


class TestSeedParallel:
    def test_parallel_path_triggered(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """10+ files triggers the parallel path; all are measured."""
        from harness.cli.seed import _PARALLEL_THRESHOLD

        count = _PARALLEL_THRESHOLD + 2  # comfortably above
        _create_py_files(tmp_path, count)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert f"Seeded {count} file(s)" in out

        db_path = tmp_path / ".claude" / "entropy.db"
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT COUNT(*) FROM measurements",
        ).fetchone()
        assert rows[0] == count
        conn.close()

    def test_sequential_path_below_threshold(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Below threshold, seed still works (sequential path)."""
        _create_py_files(tmp_path, 3)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Seeded 3 file(s)" in out


# ---------------------------------------------------------------------------
# seed_project (library function)
# ---------------------------------------------------------------------------


class TestSeedProject:
    def test_returns_seed_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from harness.cli.seed import SeedSummary, seed_project

        _create_py_files(tmp_path, 3)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        summary = seed_project(tmp_path)
        assert isinstance(summary, SeedSummary)
        assert summary.files_measured == 3
        assert summary.files_skipped == 0
        assert summary.avg_entropy_index >= 0
        assert summary.commit_hash == FAKE_HASH
        assert summary.db_path.exists()
        assert len(summary.results) == 3

    def test_no_files_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from harness.cli.seed import seed_project

        (tmp_path / "readme.txt").write_text("hello")
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        with pytest.raises(FileNotFoundError, match="No Python files"):
            seed_project(tmp_path)

    def test_quiet_suppresses_warnings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.seed import seed_project

        _create_py_files(tmp_path, 1)
        # Create a file that will fail to measure
        (tmp_path / "bad.py").write_text("")
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_project(tmp_path, quiet=True)
        err = capsys.readouterr().err
        assert "warning" not in err

    def test_seed_main_backward_compat(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """seed_main still works as the CLI entry point."""
        _create_py_files(tmp_path, 2)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Seeded 2 file(s)" in out
