"""Git helpers: changed files, before/after content retrieval."""
from __future__ import annotations

import subprocess
from pathlib import Path


def is_git_repo(path: Path | None = None) -> bool:
    """Check if the given path is inside a git repository."""
    cwd = str(path) if path else None
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, check=True, cwd=cwd,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_changed_files(commit: str = "HEAD", cwd: Path | None = None) -> list[str]:
    """Get list of files changed in a commit (added, modified — not deleted).

    Returns paths relative to repo root.
    Uses git diff-tree to find files changed in the commit.
    For the initial commit (no parent), uses --root flag.
    """
    work_dir = str(cwd) if cwd else None

    # Try normal diff-tree first (commit has parent)
    try:
        result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-status", "-r", commit],
            capture_output=True, text=True, check=True, cwd=work_dir,
        )
    except subprocess.CalledProcessError:
        return []

    files = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, filepath = parts
        # Include Added (A) and Modified (M), skip Deleted (D)
        if status in ("A", "M"):
            files.append(filepath)
    return files


def get_file_at_commit(filepath: str, commit: str, cwd: Path | None = None) -> str | None:
    """Get the content of a file at a specific commit.

    Returns None if the file doesn't exist at that commit.
    """
    work_dir = str(cwd) if cwd else None
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{filepath}"],
            capture_output=True, text=True, check=True, cwd=work_dir,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return None


def get_current_commit(cwd: Path | None = None) -> str | None:
    """Get the current HEAD commit hash."""
    work_dir = str(cwd) if cwd else None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, cwd=work_dir,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_parent_commit(commit: str = "HEAD", cwd: Path | None = None) -> str | None:
    """Get the parent commit hash. Returns None for initial commit."""
    work_dir = str(cwd) if cwd else None
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"{commit}~1"],
            capture_output=True, text=True, check=True, cwd=work_dir,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def get_recent_commits(n: int = 10, cwd: Path | None = None) -> list[str]:
    """Get the last N commit hashes, most recent first."""
    work_dir = str(cwd) if cwd else None
    try:
        result = subprocess.run(
            ["git", "log", f"-{n}", "--format=%H"],
            capture_output=True, text=True, check=True, cwd=work_dir,
        )
        return [h for h in result.stdout.strip().splitlines() if h]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
