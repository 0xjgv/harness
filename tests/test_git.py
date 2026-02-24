"""Tests for harness.git — git helper functions using real temp repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.git import (
    get_changed_files,
    get_current_commit,
    get_file_at_commit,
    get_parent_commit,
    get_recent_commits,
    is_git_repo,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    subprocess.run(
        ["git", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    # Create initial file and commit
    f = tmp_path / "initial.py"
    f.write_text("x = 1\n")
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
            "initial commit",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    return tmp_path


# ---------------------------------------------------------------------------
# is_git_repo
# ---------------------------------------------------------------------------


class TestIsGitRepo:
    def test_is_git_repo_true(self, git_repo: Path) -> None:
        assert is_git_repo(git_repo) is True

    def test_is_git_repo_false(self, tmp_path: Path) -> None:
        assert is_git_repo(tmp_path) is False


# ---------------------------------------------------------------------------
# get_changed_files
# ---------------------------------------------------------------------------


class TestGetChangedFiles:
    def test_initial_commit_returns_empty(self, git_repo: Path) -> None:
        """Initial commit with no parent returns empty (diff-tree limitation)."""
        files = get_changed_files("HEAD", cwd=git_repo)
        assert files == []

    def test_second_commit(self, git_repo: Path) -> None:
        """Second commit with new + modified files shows both."""
        # Add a new file
        new_file = git_repo / "new.py"
        new_file.write_text("y = 2\n")
        # Modify existing file
        (git_repo / "initial.py").write_text("x = 42\n")

        subprocess.run(
            ["git", "add", "."],
            cwd=str(git_repo),
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
                "second commit",
            ],
            cwd=str(git_repo),
            capture_output=True,
            check=True,
        )

        files = get_changed_files("HEAD", cwd=git_repo)
        assert "new.py" in files
        assert "initial.py" in files

    def test_deleted_file_excluded(self, git_repo: Path) -> None:
        """Deleted files should not appear in changed files."""
        # Delete the file
        (git_repo / "initial.py").unlink()
        subprocess.run(
            ["git", "add", "."],
            cwd=str(git_repo),
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
                "delete file",
            ],
            cwd=str(git_repo),
            capture_output=True,
            check=True,
        )

        files = get_changed_files("HEAD", cwd=git_repo)
        assert "initial.py" not in files

    def test_bad_commit_returns_empty(self, git_repo: Path) -> None:
        """Invalid commit ref should return empty list."""
        files = get_changed_files("nonexistent_ref", cwd=git_repo)
        assert files == []


# ---------------------------------------------------------------------------
# get_file_at_commit
# ---------------------------------------------------------------------------


class TestGetFileAtCommit:
    def test_get_file_at_commit(self, git_repo: Path) -> None:
        """Should retrieve file content at a specific commit."""
        content = get_file_at_commit("initial.py", "HEAD", cwd=git_repo)
        assert content is not None
        assert "x = 1" in content

    def test_get_nonexistent_file(self, git_repo: Path) -> None:
        """Should return None for a file that doesn't exist at that commit."""
        result = get_file_at_commit("nope.py", "HEAD", cwd=git_repo)
        assert result is None


# ---------------------------------------------------------------------------
# get_current_commit
# ---------------------------------------------------------------------------


class TestGetCurrentCommit:
    def test_get_current_commit(self, git_repo: Path) -> None:
        """Should return a 40-char hex hash."""
        commit = get_current_commit(cwd=git_repo)
        assert commit is not None
        assert len(commit) == 40
        # Should be valid hex
        int(commit, 16)

    def test_not_a_repo(self, tmp_path: Path) -> None:
        """Should return None for a non-repo directory."""
        result = get_current_commit(cwd=tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# get_parent_commit
# ---------------------------------------------------------------------------


class TestGetParentCommit:
    def test_initial_commit_has_no_parent(self, git_repo: Path) -> None:
        """Initial commit should return None for parent."""
        result = get_parent_commit("HEAD", cwd=git_repo)
        assert result is None

    def test_second_commit_has_parent(self, git_repo: Path) -> None:
        """Second commit should have a valid parent hash."""
        # Make a second commit
        f = git_repo / "second.py"
        f.write_text("z = 3\n")
        subprocess.run(
            ["git", "add", "."],
            cwd=str(git_repo),
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
                "second",
            ],
            cwd=str(git_repo),
            capture_output=True,
            check=True,
        )

        parent = get_parent_commit("HEAD", cwd=git_repo)
        assert parent is not None
        assert len(parent) == 40


# ---------------------------------------------------------------------------
# get_recent_commits
# ---------------------------------------------------------------------------


class TestGetRecentCommits:
    def test_single_commit(self, git_repo: Path) -> None:
        """Repo with one commit should return a list of one hash."""
        commits = get_recent_commits(n=10, cwd=git_repo)
        assert len(commits) == 1
        assert len(commits[0]) == 40

    def test_multiple_commits(self, git_repo: Path) -> None:
        """Should return commits in newest-first order."""
        # Add two more commits
        for i in range(2):
            f = git_repo / f"file{i}.py"
            f.write_text(f"v = {i}\n")
            subprocess.run(
                ["git", "add", "."],
                cwd=str(git_repo),
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
                    f"commit {i}",
                ],
                cwd=str(git_repo),
                capture_output=True,
                check=True,
            )

        commits = get_recent_commits(n=10, cwd=git_repo)
        assert len(commits) == 3

        # Newest first: HEAD should be first
        current = get_current_commit(cwd=git_repo)
        assert commits[0] == current

    def test_limit_respected(self, git_repo: Path) -> None:
        """n parameter should limit the number of commits returned."""
        # Add another commit
        f = git_repo / "extra.py"
        f.write_text("extra = True\n")
        subprocess.run(
            ["git", "add", "."],
            cwd=str(git_repo),
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
                "extra",
            ],
            cwd=str(git_repo),
            capture_output=True,
            check=True,
        )

        commits = get_recent_commits(n=1, cwd=git_repo)
        assert len(commits) == 1

    def test_not_a_repo(self, tmp_path: Path) -> None:
        """Non-repo should return empty list."""
        commits = get_recent_commits(n=10, cwd=tmp_path)
        assert commits == []
