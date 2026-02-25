"""harness entropy seed — establish baseline entropy measurements for a codebase."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

from harness.cli.measure import (
    _collect_all_files,
    _has_valid_extension,
    _is_excluded,
    _metrics_to_dict,
    _metrics_to_measurement,
    _resolve_commit_hash,
)
from harness.config import DEFAULT_EXCLUDES, DEFAULT_EXTENSIONS, find_project_root, get_db_path
from harness.core.composite import compute_entropy_index
from harness.core.db import get_connection, store_measurements_batch
from harness.core.metrics import FileMetrics, measure_file
from harness.git import get_file_at_commit, get_files_at_commit, get_recent_commits

_PARALLEL_THRESHOLD = 8


@dataclass
class SeedSummary:
    """Result of seeding a project with baseline measurements."""

    files_measured: int
    files_skipped: int
    avg_entropy_index: float
    commit_hash: str | None
    db_path: Path
    results: list[_SeedResult]


@dataclass
class BackfillSummary:
    """Aggregate result of backfilling multiple commits."""

    commits_processed: int
    commits_skipped: int
    total_files_measured: int
    total_files_skipped: int
    db_path: Path


@dataclass
class _SeedResult:
    """Successful measurement result from a worker process."""

    rel_path: str
    metrics: FileMetrics
    entropy_index: float


def _measure_one(args: tuple[str, str]) -> _SeedResult | tuple[str, str]:
    """Measure a single file from filesystem. Top-level for pickling compatibility.

    Returns _SeedResult on success, (rel_path, error_msg) on failure.
    """
    file_path_str, project_root_str = args
    path = Path(file_path_str)
    project_root = Path(project_root_str)
    try:
        rel_path = str(path.relative_to(project_root))
    except ValueError:
        rel_path = str(path)
    try:
        metrics = measure_file(path)
        ei = compute_entropy_index(metrics)
        return _SeedResult(rel_path=rel_path, metrics=metrics, entropy_index=ei)
    except Exception as exc:
        return (rel_path, str(exc))


def _measure_one_content(args: tuple[str, str]) -> _SeedResult | tuple[str, str]:
    """Measure a single file from in-memory content. Top-level for pickling compatibility.

    Returns _SeedResult on success, (rel_path, error_msg) on failure.
    """
    rel_path, content = args
    try:
        metrics = measure_file(content=content)
        ei = compute_entropy_index(metrics)
        return _SeedResult(rel_path=rel_path, metrics=metrics, entropy_index=ei)
    except Exception as exc:
        return (rel_path, str(exc))


def _collect_files_at_commit(commit: str, cwd: Path | None = None) -> list[str]:
    """Get filtered file list at a historical commit.

    Calls get_files_at_commit() then filters by extension + excludes.
    """
    all_files = get_files_at_commit(commit, cwd=cwd)
    return [
        f
        for f in all_files
        if _has_valid_extension(f, DEFAULT_EXTENSIONS) and not _is_excluded(f, DEFAULT_EXCLUDES)
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness entropy seed",
        description="Establish baseline entropy measurements for all Python files.",
    )
    parser.add_argument(
        "--commit",
        type=str,
        default="HEAD",
        help="Tag measurements with this commit (default: HEAD)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Number of recent commits to backfill (default: 1)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON instead of human-readable",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Override project root detection",
    )
    return parser


def seed_project(
    project_root: Path,
    *,
    commit: str = "HEAD",
    quiet: bool = False,
) -> SeedSummary:
    """Establish baseline entropy measurements for all Python files in a project.

    Returns a SeedSummary with measurement results.
    Raises FileNotFoundError if no Python files are found.
    """
    commit_hash = _resolve_commit_hash(commit, cwd=project_root)

    file_paths = _collect_all_files(project_root)
    if not file_paths:
        msg = f"No Python files found in {project_root}"
        raise FileNotFoundError(msg)

    measured_at = time.time()
    project_root_str = str(project_root)

    # Measure: parallel above threshold, sequential below
    if len(file_paths) >= _PARALLEL_THRESHOLD:
        worker_args = [(str(p), project_root_str) for p in file_paths]
        max_workers = min(os.cpu_count() or 1, len(file_paths))
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            raw_results = list(executor.map(_measure_one, worker_args))
    else:
        raw_results = [_measure_one((str(p), project_root_str)) for p in file_paths]

    # Separate successes from failures
    seed_results: list[_SeedResult] = []
    files_skipped = 0
    for r in raw_results:
        if isinstance(r, _SeedResult):
            seed_results.append(r)
        else:
            files_skipped += 1
            if not quiet:
                rel_path, error_msg = r
                print(f"warning: skipping '{rel_path}': {error_msg}", file=sys.stderr)

    if not seed_results:
        msg = f"No files could be measured in {project_root}"
        raise FileNotFoundError(msg)

    # Batch-write to DB
    db_path = get_db_path(project_root)
    conn = get_connection(db_path)
    measurements = [
        _metrics_to_measurement(
            sr.rel_path,
            sr.metrics,
            sr.entropy_index,
            commit_hash,
            measured_at,
        )
        for sr in seed_results
    ]
    store_measurements_batch(conn, measurements)
    conn.close()

    avg_ei = sum(sr.entropy_index for sr in seed_results) / len(seed_results)

    return SeedSummary(
        files_measured=len(seed_results),
        files_skipped=files_skipped,
        avg_entropy_index=avg_ei,
        commit_hash=commit_hash,
        db_path=db_path,
        results=seed_results,
    )


def seed_commit_from_git(
    project_root: Path,
    commit_hash: str,
    *,
    quiet: bool = False,
) -> SeedSummary:
    """Measure all matching files at a single historical commit via git.

    Phase 1: Fetch content via get_file_at_commit() (sequential, I/O-bound)
    Phase 2: Measure via ProcessPoolExecutor if >= threshold (CPU-bound)
    Phase 3: Batch-store to DB
    """
    rel_paths = _collect_files_at_commit(commit_hash, cwd=project_root)

    if not rel_paths:
        db_path = get_db_path(project_root)
        return SeedSummary(
            files_measured=0,
            files_skipped=0,
            avg_entropy_index=0.0,
            commit_hash=commit_hash,
            db_path=db_path,
            results=[],
        )

    # Phase 1: fetch content (sequential git I/O)
    content_pairs: list[tuple[str, str]] = []
    fetch_skipped = 0
    for rel_path in rel_paths:
        content = get_file_at_commit(rel_path, commit_hash, cwd=project_root)
        if content is None:
            fetch_skipped += 1
            continue
        content_pairs.append((rel_path, content))

    if not content_pairs:
        db_path = get_db_path(project_root)
        return SeedSummary(
            files_measured=0,
            files_skipped=fetch_skipped,
            avg_entropy_index=0.0,
            commit_hash=commit_hash,
            db_path=db_path,
            results=[],
        )

    # Phase 2: measure (parallel above threshold, sequential below)
    if len(content_pairs) >= _PARALLEL_THRESHOLD:
        max_workers = min(os.cpu_count() or 1, len(content_pairs))
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            raw_results = list(executor.map(_measure_one_content, content_pairs))
    else:
        raw_results = [_measure_one_content(pair) for pair in content_pairs]

    # Separate successes from failures
    seed_results: list[_SeedResult] = []
    files_skipped = fetch_skipped
    for r in raw_results:
        if isinstance(r, _SeedResult):
            seed_results.append(r)
        else:
            files_skipped += 1
            if not quiet:
                rel_path, error_msg = r
                print(f"warning: skipping '{rel_path}': {error_msg}", file=sys.stderr)

    # Phase 3: batch-store to DB
    measured_at = time.time()
    db_path = get_db_path(project_root)
    conn = get_connection(db_path)
    measurements = [
        _metrics_to_measurement(
            sr.rel_path,
            sr.metrics,
            sr.entropy_index,
            commit_hash,
            measured_at,
        )
        for sr in seed_results
    ]
    store_measurements_batch(conn, measurements)
    conn.close()

    if seed_results:
        avg_ei = sum(sr.entropy_index for sr in seed_results) / len(seed_results)
    else:
        avg_ei = 0.0

    return SeedSummary(
        files_measured=len(seed_results),
        files_skipped=files_skipped,
        avg_entropy_index=avg_ei,
        commit_hash=commit_hash,
        db_path=db_path,
        results=seed_results,
    )


def seed_backfill(
    project_root: Path,
    *,
    depth: int,
    quiet: bool = False,
) -> BackfillSummary:
    """Backfill entropy measurements for the last N commits.

    Processes oldest-first so measured_at timestamps are chronological.
    Returns aggregate summary.
    """
    commits = get_recent_commits(n=depth, cwd=project_root)
    if not commits:
        print("error: no git commits found (is this a git repo?)", file=sys.stderr)
        sys.exit(1)

    # Oldest-first for chronological measured_at
    commits.reverse()

    total_measured = 0
    total_skipped = 0
    commits_skipped = 0
    db_path = get_db_path(project_root)

    for i, commit_hash in enumerate(commits, 1):
        if not quiet:
            print(f"  [{i}/{len(commits)}] {commit_hash[:12]}...", end="", file=sys.stderr)

        summary = seed_commit_from_git(project_root, commit_hash, quiet=quiet)

        if summary.files_measured == 0:
            commits_skipped += 1
            if not quiet:
                print(" skipped (no matching files)", file=sys.stderr)
        else:
            total_measured += summary.files_measured
            total_skipped += summary.files_skipped
            if not quiet:
                print(
                    f" {summary.files_measured} file(s), avg EI: {summary.avg_entropy_index:.1f}",
                    file=sys.stderr,
                )

    return BackfillSummary(
        commits_processed=len(commits) - commits_skipped,
        commits_skipped=commits_skipped,
        total_files_measured=total_measured,
        total_files_skipped=total_skipped,
        db_path=db_path,
    )


def seed_main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve() if args.project_root else find_project_root()

    if args.depth > 1:
        # Backfill mode: measure last N commits via git
        backfill = seed_backfill(project_root, depth=args.depth)

        if args.output_json:
            print(
                json.dumps(
                    {
                        "commits_processed": backfill.commits_processed,
                        "commits_skipped": backfill.commits_skipped,
                        "total_files_measured": backfill.total_files_measured,
                        "total_files_skipped": backfill.total_files_skipped,
                        "db_path": str(backfill.db_path),
                    },
                    indent=2,
                )
            )
        else:
            print(
                f"\nBackfilled {backfill.commits_processed} commit(s), "
                f"{backfill.total_files_measured} measurement(s)"
            )
            if backfill.commits_skipped:
                print(f"  Skipped: {backfill.commits_skipped} commit(s) with no matching files")
            print(f"  Stored: {backfill.db_path}")
        return

    # Default: single-commit seed via filesystem
    try:
        summary = seed_project(project_root, commit=args.commit)
    except FileNotFoundError as exc:
        if "No Python files" in str(exc):
            print("No Python files found. Nothing to seed.", file=sys.stderr)
            sys.exit(0)
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # Build output dicts
    results: list[dict[str, Any]] = [
        _metrics_to_dict(sr.rel_path, sr.metrics, sr.entropy_index, summary.commit_hash)
        for sr in summary.results
    ]

    # Output
    if args.output_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        tier_counts = {"low": 0, "moderate": 0, "high": 0, "very high": 0}
        for r in results:
            tier_counts[r["tier"]] += 1

        avg_ei = summary.avg_entropy_index
        print(f"Seeded {summary.files_measured} file(s), avg EI: {avg_ei:.1f}\n")
        tier_parts = [f"{count} {label}" for label, count in tier_counts.items() if count > 0]
        print(f"  Distribution: {', '.join(tier_parts)}")
        if summary.commit_hash:
            print(f"  Commit: {summary.commit_hash[:12]}")
        else:
            print("  Commit: (none — no git repo detected)")
        print(f"  Stored: {summary.db_path}")
