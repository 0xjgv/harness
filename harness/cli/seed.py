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
    _metrics_to_dict,
    _metrics_to_measurement,
    _resolve_commit_hash,
)
from harness.config import find_project_root, get_db_path
from harness.core.composite import compute_entropy_index
from harness.core.db import get_connection, store_measurements_batch
from harness.core.metrics import FileMetrics, measure_file

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
class _SeedResult:
    """Successful measurement result from a worker process."""

    rel_path: str
    metrics: FileMetrics
    entropy_index: float


def _measure_one(args: tuple[str, str]) -> _SeedResult | tuple[str, str]:
    """Measure a single file. Top-level for pickling compatibility.

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


def seed_main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve() if args.project_root else find_project_root()

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
