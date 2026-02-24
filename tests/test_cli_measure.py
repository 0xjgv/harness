"""Tests for harness.cli.measure — in-process testing via monkeypatch + capsys."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from harness.cli.measure import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Run measure main() with patched sys.argv."""
    monkeypatch.setattr("sys.argv", ["harness-measure", *argv])
    main()


# ---------------------------------------------------------------------------
# Explicit files argument
# ---------------------------------------------------------------------------


class TestMeasureExplicitFiles:
    def test_single_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        f = tmp_path / "hello.py"
        f.write_text("def hello():\n    return 42\n")

        _run_main(monkeypatch, [str(f)])
        out = capsys.readouterr()
        assert "EI:" in out.out
        assert "1 file(s) measured" in out.out

    def test_multiple_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("x = 1\n")
        f2.write_text("y = 2\n")

        _run_main(monkeypatch, [str(f1), str(f2)])
        out = capsys.readouterr()
        assert "2 file(s) measured" in out.out


# ---------------------------------------------------------------------------
# --json flag
# ---------------------------------------------------------------------------


class TestMeasureJsonOutput:
    def test_json_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        f = tmp_path / "example.py"
        f.write_text("x = 1\ny = 2\n")

        _run_main(monkeypatch, ["--json", str(f)])
        out = capsys.readouterr()
        data = json.loads(out.out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert "entropy_index" in data[0]
        assert "file" in data[0]
        assert "tier" in data[0]
        assert "tier_mask" in data[0]


# ---------------------------------------------------------------------------
# --commit flag (mock git subprocess calls)
# ---------------------------------------------------------------------------


class TestMeasureCommitFlag:
    def test_commit_flag_measures_changed_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--commit should resolve the commit hash then measure changed files."""
        py_file = tmp_path / "changed.py"
        py_file.write_text("def foo():\n    return 1\n")

        fake_hash = "abc1234567890"

        monkeypatch.setattr(
            "harness.cli.measure._resolve_commit_hash",
            lambda commit, cwd=None: fake_hash,
        )
        monkeypatch.setattr(
            "harness.cli.measure.get_changed_files",
            lambda commit, cwd=None: ["changed.py"],
        )

        _run_main(
            monkeypatch,
            ["--commit", "HEAD", "--project-root", str(tmp_path)],
        )
        out = capsys.readouterr()
        assert "EI:" in out.out
        assert "1 file(s) measured" in out.out

    def test_commit_flag_unresolvable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unresolvable commit should exit with error."""
        monkeypatch.setattr(
            "harness.cli.measure._resolve_commit_hash",
            lambda commit, cwd=None: None,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_main(
                monkeypatch,
                ["--commit", "bad-ref", "--project-root", str(tmp_path)],
            )
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "could not resolve commit" in err

    def test_commit_flag_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--commit --json should produce valid JSON with commit hash."""
        py_file = tmp_path / "mod.py"
        py_file.write_text("a = 1\n")

        fake_hash = "def456789"
        monkeypatch.setattr(
            "harness.cli.measure._resolve_commit_hash",
            lambda commit, cwd=None: fake_hash,
        )
        monkeypatch.setattr(
            "harness.cli.measure.get_changed_files",
            lambda commit, cwd=None: ["mod.py"],
        )

        _run_main(
            monkeypatch,
            ["--commit", "HEAD", "--json", "--project-root", str(tmp_path)],
        )
        out = capsys.readouterr()
        data = json.loads(out.out)
        assert data[0]["commit"] == fake_hash


# ---------------------------------------------------------------------------
# --store flag
# ---------------------------------------------------------------------------


class TestMeasureStoreFlag:
    def test_store_populates_db(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--store should create the DB and write a measurement row."""
        f = tmp_path / "storeme.py"
        f.write_text("def store():\n    pass\n")

        _run_main(
            monkeypatch,
            ["--store", "--project-root", str(tmp_path), str(f)],
        )
        out = capsys.readouterr()
        assert "Results stored" in out.out

        db_path = tmp_path / ".claude" / "entropy.db"
        assert db_path.exists()
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()
        assert rows[0] >= 1
        conn.close()


# ---------------------------------------------------------------------------
# --all flag
# ---------------------------------------------------------------------------


class TestMeasureAllFlag:
    def test_all_flag_finds_py_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all should glob for .py files under project root."""
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "mod1.py").write_text("x = 1\n")
        (tmp_path / "pkg" / "mod2.py").write_text("y = 2\n")
        (tmp_path / "readme.txt").write_text("hello")

        _run_main(monkeypatch, ["--all", "--project-root", str(tmp_path)])
        out = capsys.readouterr()
        assert "2 file(s) measured" in out.out

    def test_all_flag_empty_project(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all on a project with no .py files should say no files."""
        (tmp_path / "readme.txt").write_text("hello")

        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, ["--all", "--project-root", str(tmp_path)])
        assert exc_info.value.code == 0
        err = capsys.readouterr().err
        assert "No files to measure" in err


# ---------------------------------------------------------------------------
# Nonexistent files (warning to stderr)
# ---------------------------------------------------------------------------


class TestMeasureNonexistentFile:
    def test_nonexistent_file_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Non-existent file should warn to stderr and exit cleanly."""
        bad_path = tmp_path / "does_not_exist.py"

        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, [str(bad_path)])
        assert exc_info.value.code == 0
        err = capsys.readouterr().err
        assert "warning" in err.lower()
        assert "No files to measure" in err


# ---------------------------------------------------------------------------
# No arguments (prints help)
# ---------------------------------------------------------------------------


class TestMeasureNoArgs:
    def test_no_args_prints_help(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No arguments should print help and exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, [])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# __main__.py import
# ---------------------------------------------------------------------------


class TestMainModule:
    def test_main_module_imports(self) -> None:
        """Verify __main__.py can be imported (covers lines 3-6)."""
        import harness.__main__  # noqa: F401


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_ei_tier_labels(self) -> None:
        from harness.cli.measure import _ei_tier_label

        assert _ei_tier_label(10) == "low"
        assert _ei_tier_label(30) == "moderate"
        assert _ei_tier_label(60) == "high"
        assert _ei_tier_label(80) == "very high"

    def test_is_excluded(self) -> None:
        from harness.cli.measure import _is_excluded

        excludes = frozenset({"migrations/**", "vendor/**"})
        assert _is_excluded("migrations/0001.py", excludes) is True
        assert _is_excluded("src/main.py", excludes) is False

    def test_has_valid_extension(self) -> None:
        from harness.cli.measure import _has_valid_extension

        exts = frozenset({".py"})
        assert _has_valid_extension("foo.py", exts) is True
        assert _has_valid_extension("foo.txt", exts) is False

    def test_resolve_commit_hash_failure(self) -> None:
        from harness.cli.measure import _resolve_commit_hash

        result = _resolve_commit_hash("nonexistent_ref_abc123", cwd=Path("/tmp"))
        assert result is None

    def test_metrics_to_dict(self) -> None:
        from harness.cli.measure import _metrics_to_dict
        from harness.core.metrics import FileMetrics

        metrics = FileMetrics(
            file_size_bytes=100,
            line_count=10,
            blank_lines=2,
            comment_lines=1,
            compression_ratio=0.5,
            line_length_stddev=5.0,
            line_entropy=3.5,
            cyclomatic_complexity=2.0,
            maintainability_index=80.0,
            halstead_volume=100.0,
            ast_node_count=50,
            ast_depth_max=5,
            ast_entropy=2.5,
            tier_mask=7,
        )
        result = _metrics_to_dict("test.py", metrics, 42.5, "abc123")
        assert result["file"] == "test.py"
        assert result["entropy_index"] == 42.5
        assert result["commit"] == "abc123"
        assert result["tier"] == "moderate"

    def test_metrics_to_measurement(self) -> None:
        from harness.cli.measure import _metrics_to_measurement
        from harness.core.metrics import FileMetrics

        metrics = FileMetrics(
            file_size_bytes=100,
            line_count=10,
            blank_lines=2,
            comment_lines=1,
            compression_ratio=0.5,
            line_length_stddev=5.0,
            line_entropy=3.5,
            tier_mask=1,
        )
        ts = time.time()
        m = _metrics_to_measurement("test.py", metrics, 42.5, "abc123", ts)
        assert m.file_path == "test.py"
        assert m.entropy_index == 42.5
        assert m.commit_hash == "abc123"
        assert m.measured_at == ts

    def test_collect_all_files(self, tmp_path: Path) -> None:
        from harness.cli.measure import _collect_all_files

        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("y = 2\n")
        (tmp_path / "migrations").mkdir()
        (tmp_path / "migrations" / "0001.py").write_text("z = 3\n")

        files = _collect_all_files(tmp_path)
        names = [f.name for f in files]
        assert "a.py" in names
        assert "b.py" in names
        assert "0001.py" not in names
