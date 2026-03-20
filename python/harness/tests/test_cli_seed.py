"""Tests for harness.cli.seed — seed baseline entropy measurements."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from harness.cli.seed import (
    BackfillSummary,
    SeedSummary,
    _collect_files_at_commit,
    _measure_one,
    _measure_one_content,
    _SeedResult,
    build_parser,
    seed_backfill,
    seed_main,
    seed_project,
)

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


def _git_commit(cwd: Path, message: str) -> str:
    """Stage all and commit. Returns the commit hash."""
    subprocess.run(
        ["git", "add", "."],
        cwd=str(cwd),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@test.com",
            "commit",
            "-m",
            message,
        ],
        cwd=str(cwd),
        capture_output=True,
        check=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


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
        f = tmp_path / "ok.py"
        f.write_text("x = 1\n")
        result = _measure_one((str(f), str(tmp_path)))
        assert isinstance(result, _SeedResult)
        assert result.rel_path == "ok.py"
        assert result.entropy_index >= 0

    def test_failure_returns_tuple(self, tmp_path: Path) -> None:
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


# ---------------------------------------------------------------------------
# --depth argument
# ---------------------------------------------------------------------------


class TestSeedDepthArg:
    def test_parser_accepts_depth(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--depth", "5"])
        assert args.depth == 5

    def test_depth_defaults_to_one(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.depth == 1

    def test_depth_one_uses_filesystem_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--depth 1 (default) still uses seed_project filesystem path."""
        _create_py_files(tmp_path, 2)
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: FAKE_HASH,
        )

        seed_main(["--depth", "1", "--project-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Seeded 2 file(s)" in out


# ---------------------------------------------------------------------------
# _measure_one_content worker
# ---------------------------------------------------------------------------


class TestMeasureOneContentWorker:
    def test_success_returns_seed_result(self) -> None:
        content = "def hello():\n    return 42\n"
        result = _measure_one_content(("hello.py", content))
        assert isinstance(result, _SeedResult)
        assert result.rel_path == "hello.py"
        assert result.entropy_index >= 0

    def test_failure_returns_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "harness.cli.seed.measure_file",
            lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")),
        )
        result = _measure_one_content(("bad.py", "x = 1\n"))
        assert isinstance(result, tuple)
        rel_path, error_msg = result
        assert rel_path == "bad.py"
        assert "boom" in error_msg


# ---------------------------------------------------------------------------
# _collect_files_at_commit
# ---------------------------------------------------------------------------


class TestCollectFilesAtCommit:
    def test_filters_by_extension(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "harness.cli.seed.get_files_at_commit",
            lambda commit, cwd=None: [
                "app.py",
                "lib/utils.py",
                "README.md",
                "data.json",
            ],
        )
        result = _collect_files_at_commit("abc123")
        assert "app.py" in result
        assert "lib/utils.py" in result
        assert "README.md" not in result
        assert "data.json" not in result

    def test_excludes_patterns(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "harness.cli.seed.get_files_at_commit",
            lambda commit, cwd=None: [
                "app.py",
                ".venv/lib/site.py",
                "__pycache__/mod.py",
                "vendor/third_party.py",
            ],
        )
        result = _collect_files_at_commit("abc123")
        assert result == ["app.py"]


# ---------------------------------------------------------------------------
# seed_backfill (integration with monkeypatched git)
# ---------------------------------------------------------------------------


class TestSeedBackfill:
    def test_multi_commit_backfill(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Backfill stores measurements for multiple commits."""
        commits = ["aaa111", "bbb222", "ccc333"]
        file_content = "def f():\n    return 1\n"

        monkeypatch.setattr(
            "harness.cli.seed.get_recent_commits",
            lambda n, cwd=None: commits[:n],
        )
        monkeypatch.setattr(
            "harness.cli.seed.get_files_at_commit",
            lambda commit, cwd=None: ["mod.py"],
        )
        monkeypatch.setattr(
            "harness.cli.seed.get_file_at_commit",
            lambda filepath, commit, cwd=None: file_content,
        )

        result = seed_backfill(tmp_path, depth=3)
        assert isinstance(result, BackfillSummary)
        assert result.commits_processed == 3
        assert result.commits_skipped == 0
        assert result.total_files_measured == 3

        # Verify DB has 3 rows (one per commit)
        db_path = tmp_path / ".claude" / "entropy.db"
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()
        assert rows[0] == 3
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT commit_hash) FROM measurements",
        ).fetchone()
        assert distinct[0] == 3
        conn.close()

    def test_oldest_first_ordering(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Commits are processed oldest-first for chronological timestamps."""
        processed_order: list[str] = []
        original_commits = ["newest", "middle", "oldest"]

        monkeypatch.setattr(
            "harness.cli.seed.get_recent_commits",
            lambda n, cwd=None: original_commits[:n],
        )

        def fake_get_files(commit: str, cwd: object = None) -> list[str]:
            processed_order.append(commit)
            return ["mod.py"]

        monkeypatch.setattr(
            "harness.cli.seed.get_files_at_commit",
            fake_get_files,
        )
        monkeypatch.setattr(
            "harness.cli.seed.get_file_at_commit",
            lambda filepath, commit, cwd=None: "x = 1\n",
        )

        seed_backfill(tmp_path, depth=3)
        assert processed_order == ["oldest", "middle", "newest"]

    def test_skips_commits_with_no_py_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Commits with no matching files are skipped."""
        monkeypatch.setattr(
            "harness.cli.seed.get_recent_commits",
            lambda n, cwd=None: ["aaa", "bbb"],
        )
        monkeypatch.setattr(
            "harness.cli.seed.get_files_at_commit",
            lambda commit, cwd=None: ["README.md"],  # no .py files
        )

        result = seed_backfill(tmp_path, depth=2)
        assert result.commits_processed == 0
        assert result.commits_skipped == 2
        assert result.total_files_measured == 0

    def test_no_git_repo_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No commits found should sys.exit(1)."""
        monkeypatch.setattr(
            "harness.cli.seed.get_recent_commits",
            lambda n, cwd=None: [],
        )
        with pytest.raises(SystemExit) as exc_info:
            seed_backfill(tmp_path, depth=5)
        assert exc_info.value.code == 1

    def test_json_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--depth > 1 --json produces valid JSON."""
        monkeypatch.setattr(
            "harness.cli.seed.get_recent_commits",
            lambda n, cwd=None: ["aaa111", "bbb222"],
        )
        monkeypatch.setattr(
            "harness.cli.seed.get_files_at_commit",
            lambda commit, cwd=None: ["mod.py"],
        )
        monkeypatch.setattr(
            "harness.cli.seed.get_file_at_commit",
            lambda filepath, commit, cwd=None: "x = 1\n",
        )

        seed_main([
            "--depth",
            "2",
            "--json",
            "--project-root",
            str(tmp_path),
        ])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["commits_processed"] == 2
        assert "total_files_measured" in data

    def test_depth_exceeds_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Requesting more depth than history exists is fine."""
        monkeypatch.setattr(
            "harness.cli.seed.get_recent_commits",
            lambda n, cwd=None: ["only_one"],
        )
        monkeypatch.setattr(
            "harness.cli.seed.get_files_at_commit",
            lambda commit, cwd=None: ["a.py"],
        )
        monkeypatch.setattr(
            "harness.cli.seed.get_file_at_commit",
            lambda filepath, commit, cwd=None: "x = 1\n",
        )

        result = seed_backfill(tmp_path, depth=100)
        assert result.commits_processed == 1
