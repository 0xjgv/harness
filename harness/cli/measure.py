"""harness-measure CLI — measure entropy index for files, commits, or entire projects."""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

from harness.config import (
    DEFAULT_EXCLUDES,
    DEFAULT_EXTENSIONS,
    find_project_root,
    get_db_path,
)
from harness.core.composite import compute_entropy_index
from harness.core.db import Measurement, get_connection, store_measurement
from harness.core.metrics import FileMetrics, measure_file
from harness.git import get_changed_files


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-measure",
        description="Measure code entropy index for files, commits, or projects.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILES",
        help="Files to measure",
    )
    parser.add_argument(
        "--commit",
        type=str,
        default=None,
        help="Measure files changed in a git commit (e.g. HEAD)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="measure_all",
        help="Measure all Python files in project root",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON instead of human-readable",
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Store results in .claude/entropy.db",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Override project root detection",
    )
    return parser


def _resolve_commit_hash(commit: str, cwd: Path | None = None) -> str | None:
    """Resolve a commit ref to its full SHA hash."""
    work_dir = str(cwd) if cwd else None
    try:
        result = subprocess.run(
            ["git", "rev-parse", commit],
            capture_output=True,
            text=True,
            check=True,
            cwd=work_dir,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _is_excluded(filepath: str, excludes: frozenset[str]) -> bool:
    """Check if a file path matches any exclude pattern."""
    return any(fnmatch.fnmatch(filepath, pattern) for pattern in excludes)


def _has_valid_extension(filepath: str, extensions: frozenset[str]) -> bool:
    """Check if a file has a valid extension."""
    return Path(filepath).suffix in extensions


def _ei_tier_label(ei: float) -> str:
    """Return a human-readable tier label for an entropy index value."""
    if ei < 25:
        return "low"
    if ei < 50:
        return "moderate"
    if ei < 75:
        return "high"
    return "very high"


def _metrics_to_dict(
    filepath: str,
    metrics: FileMetrics,
    ei: float,
    commit_hash: str | None,
) -> dict[str, Any]:
    """Convert file metrics + EI to a serializable dict."""
    return {
        "file": filepath,
        "entropy_index": ei,
        "tier": _ei_tier_label(ei),
        "commit": commit_hash,
        "tier_mask": metrics.tier_mask,
        "file_size_bytes": metrics.file_size_bytes,
        "line_count": metrics.line_count,
        "blank_lines": metrics.blank_lines,
        "comment_lines": metrics.comment_lines,
        "compression_ratio": round(metrics.compression_ratio, 4),
        "line_entropy": round(metrics.line_entropy, 4),
        "cyclomatic_complexity": metrics.cyclomatic_complexity,
        "maintainability_index": metrics.maintainability_index,
        "halstead_volume": metrics.halstead_volume,
        "ast_node_count": metrics.ast_node_count,
        "ast_depth_max": metrics.ast_depth_max,
        "ast_entropy": metrics.ast_entropy,
    }


def _metrics_to_measurement(
    filepath: str,
    metrics: FileMetrics,
    ei: float,
    commit_hash: str | None,
    measured_at: float,
) -> Measurement:
    """Convert FileMetrics + EI into a Measurement dataclass for DB storage."""
    return Measurement(
        file_path=filepath,
        commit_hash=commit_hash,
        measured_at=measured_at,
        file_size_bytes=metrics.file_size_bytes,
        line_count=metrics.line_count,
        blank_lines=metrics.blank_lines,
        comment_lines=metrics.comment_lines,
        compression_ratio=metrics.compression_ratio,
        line_length_stddev=metrics.line_length_stddev,
        cyclomatic_complexity=metrics.cyclomatic_complexity,
        maintainability_index=metrics.maintainability_index,
        halstead_volume=metrics.halstead_volume,
        ast_node_count=metrics.ast_node_count,
        ast_depth_max=metrics.ast_depth_max,
        ast_entropy=metrics.ast_entropy,
        entropy_index=ei,
        tier_mask=metrics.tier_mask,
    )


def _collect_all_files(project_root: Path) -> list[Path]:
    """Glob for files matching DEFAULT_EXTENSIONS under project root, filtering excludes."""
    files: list[Path] = []
    for ext in DEFAULT_EXTENSIONS:
        pattern = f"**/*{ext}"
        for path in sorted(project_root.glob(pattern)):
            rel = str(path.relative_to(project_root))
            if not _is_excluded(rel, DEFAULT_EXCLUDES):
                files.append(path)
    return files


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve() if args.project_root else find_project_root()
    commit_hash: str | None = None

    # Determine which files to measure
    file_paths: list[Path] = []

    if args.commit:
        # --commit mode: get files changed in the specified commit
        commit_hash = _resolve_commit_hash(args.commit, cwd=project_root)
        if commit_hash is None:
            print(f"error: could not resolve commit '{args.commit}'", file=sys.stderr)
            sys.exit(1)

        changed = get_changed_files(args.commit, cwd=project_root)
        for rel_path in changed:
            if not _has_valid_extension(rel_path, DEFAULT_EXTENSIONS):
                continue
            if _is_excluded(rel_path, DEFAULT_EXCLUDES):
                continue
            full_path = project_root / rel_path
            if full_path.is_file():
                file_paths.append(full_path)

    elif args.measure_all:
        # --all mode: glob for all matching files under project root
        file_paths = _collect_all_files(project_root)

    elif args.files:
        # Explicit files mode
        for f in args.files:
            p = Path(f).resolve()
            if p.is_file():
                file_paths.append(p)
            else:
                print(f"warning: skipping '{f}' (not found)", file=sys.stderr)

    else:
        parser.print_help()
        sys.exit(0)

    if not file_paths:
        print("No files to measure.", file=sys.stderr)
        sys.exit(0)

    # Measure each file
    results: list[dict[str, Any]] = []
    measured_at = time.time()
    conn = None
    if args.store:
        db_path = get_db_path(project_root)
        conn = get_connection(db_path)

    for path in file_paths:
        try:
            metrics = measure_file(path)
        except Exception as exc:
            print(f"warning: skipping '{path}': {exc}", file=sys.stderr)
            continue

        ei = compute_entropy_index(metrics)

        # Determine the relative path for storage/display
        try:
            rel_path = str(path.relative_to(project_root))
        except ValueError:
            rel_path = str(path)

        result = _metrics_to_dict(rel_path, metrics, ei, commit_hash)
        results.append(result)

        if conn is not None:
            measurement = _metrics_to_measurement(rel_path, metrics, ei, commit_hash, measured_at)
            store_measurement(conn, measurement)

    if conn is not None:
        conn.close()

    # Output
    if args.output_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            ei_val = r["entropy_index"]
            tier_label = r["tier"]
            filepath = r["file"]
            print(f"  {filepath:<50s}  EI: {ei_val:5.1f}  ({tier_label})")

        if results:
            avg_ei = sum(float(r["entropy_index"]) for r in results) / len(results)
            print(f"\n  {len(results)} file(s) measured, avg EI: {avg_ei:.1f}")

        if args.store:
            print(f"  Results stored in {get_db_path(project_root)}")
