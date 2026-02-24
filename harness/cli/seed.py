"""harness entropy seed — establish baseline entropy measurements for a codebase."""

from __future__ import annotations

import argparse
import json
import sys
import time
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
from harness.core.db import get_connection, store_measurement
from harness.core.metrics import measure_file


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


def seed_main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve() if args.project_root else find_project_root()

    # Resolve commit hash (None if not in a git repo)
    commit_hash = _resolve_commit_hash(args.commit, cwd=project_root)
    # Not an error if commit can't be resolved -- seed without commit tag

    # Collect all Python files
    file_paths = _collect_all_files(project_root)
    if not file_paths:
        print("No Python files found. Nothing to seed.", file=sys.stderr)
        sys.exit(0)

    # Measure and store
    db_path = get_db_path(project_root)
    conn = get_connection(db_path)
    results: list[dict[str, Any]] = []
    measured_at = time.time()

    for path in file_paths:
        try:
            metrics = measure_file(path)
        except Exception as exc:
            print(f"warning: skipping '{path}': {exc}", file=sys.stderr)
            continue

        ei = compute_entropy_index(metrics)
        try:
            rel_path = str(path.relative_to(project_root))
        except ValueError:
            rel_path = str(path)

        measurement = _metrics_to_measurement(rel_path, metrics, ei, commit_hash, measured_at)
        store_measurement(conn, measurement)
        results.append(_metrics_to_dict(rel_path, metrics, ei, commit_hash))

    conn.close()

    if not results:
        print("No files could be measured.", file=sys.stderr)
        sys.exit(1)

    # Output
    if args.output_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        avg_ei = sum(float(r["entropy_index"]) for r in results) / len(results)
        tier_counts = {"low": 0, "moderate": 0, "high": 0, "very high": 0}
        for r in results:
            tier_counts[r["tier"]] += 1

        print(f"Seeded {len(results)} file(s), avg EI: {avg_ei:.1f}\n")
        tier_parts = [f"{count} {label}" for label, count in tier_counts.items() if count > 0]
        print(f"  Distribution: {', '.join(tier_parts)}")
        if commit_hash:
            print(f"  Commit: {commit_hash[:12]}")
        else:
            print("  Commit: (none — no git repo detected)")
        print(f"  Stored: {db_path}")
