"""Tests for entropy_meter.config — project root, DB path, config loading."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from entropy_meter.config import (
    find_project_root,
    get_current_commit,
    get_db_path,
    get_project_config,
)

# ---------------------------------------------------------------------------
# find_project_root
# ---------------------------------------------------------------------------


class TestFindProjectRoot:
    def test_finds_git_dir(self, tmp_path: Path) -> None:
        """Should find directory containing .git."""
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)

        root = find_project_root(start=sub)
        assert root == tmp_path

    def test_finds_pyproject_toml(self, tmp_path: Path) -> None:
        """Should find directory containing pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        sub = tmp_path / "deep" / "nested"
        sub.mkdir(parents=True)

        root = find_project_root(start=sub)
        assert root == tmp_path

    def test_no_marker_returns_start(self, tmp_path: Path) -> None:
        """When no marker found, should return the start directory itself."""
        sub = tmp_path / "orphan"
        sub.mkdir()

        root = find_project_root(start=sub)
        assert root == sub


# ---------------------------------------------------------------------------
# get_db_path
# ---------------------------------------------------------------------------


class TestGetDbPath:
    def test_creates_claude_dir(self, tmp_path: Path) -> None:
        """get_db_path should create .claude/ directory if missing."""
        db_path = get_db_path(tmp_path)
        assert db_path == tmp_path / ".claude" / "entropy.db"
        assert (tmp_path / ".claude").is_dir()

    def test_existing_claude_dir(self, tmp_path: Path) -> None:
        """Should work if .claude/ already exists."""
        (tmp_path / ".claude").mkdir()
        db_path = get_db_path(tmp_path)
        assert db_path == tmp_path / ".claude" / "entropy.db"


# ---------------------------------------------------------------------------
# get_project_config
# ---------------------------------------------------------------------------


class TestGetProjectConfig:
    def test_no_pyproject_toml(self, tmp_path: Path) -> None:
        """Should return empty dict if no pyproject.toml exists."""
        config = get_project_config(tmp_path)
        assert config == {}

    def test_pyproject_without_tool_section(self, tmp_path: Path) -> None:
        """Should return empty dict if no [tool.entropy-meter] section."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        config = get_project_config(tmp_path)
        assert config == {}

    def test_pyproject_with_config(self, tmp_path: Path) -> None:
        """Should return config from [tool.entropy-meter] section."""
        toml_content = (
            '[project]\nname = "test"\n\n'
            "[tool.entropy-meter]\n"
            "warn_threshold = 70\n"
            "alert_threshold = 90\n"
        )
        (tmp_path / "pyproject.toml").write_text(toml_content)
        config = get_project_config(tmp_path)
        assert config["warn_threshold"] == 70
        assert config["alert_threshold"] == 90


# ---------------------------------------------------------------------------
# get_current_commit (config module version)
# ---------------------------------------------------------------------------


class TestConfigGetCurrentCommit:
    def test_returns_hash_in_git_repo(self, tmp_path: Path) -> None:
        """Should return a commit hash when inside a git repo."""
        subprocess.run(
            ["git", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        (tmp_path / "f.py").write_text("x = 1\n")
        subprocess.run(
            ["git", "add", "."],
            cwd=str(tmp_path),
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
                "init",
            ],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )

        # Mock subprocess.run to use our temp repo
        original_run = subprocess.run

        def mock_run(cmd, **kwargs):
            if cmd == ["git", "rev-parse", "HEAD"]:
                kwargs["cwd"] = str(tmp_path)
            return original_run(cmd, **kwargs)

        with patch("entropy_meter.config.subprocess.run", side_effect=mock_run):
            result = get_current_commit()
            assert result is not None
            assert len(result) == 40

    def test_returns_none_outside_git(self) -> None:
        """Should return None when git fails."""
        with patch(
            "entropy_meter.config.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = get_current_commit()
            assert result is None
