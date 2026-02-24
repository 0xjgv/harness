"""entropy-report CLI — show trends, hotspots, and history from stored measurements."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from entropy_meter.config import find_project_root, get_db_path
from entropy_meter.core.db import get_connection, get_file_history, get_hotspots, get_trend

if TYPE_CHECKING:
    from typing import Any

    from entropy_meter.core.db import Measurement


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="entropy-report",
        description="Show entropy trends, hotspots, and file history from stored measurements.",
    )
    parser.add_argument(
        "--hotspots",
        action="store_true",
        help="Show files with highest entropy index",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        metavar="PATH",
        help="Show measurement history for a specific file",
    )
    parser.add_argument(
        "--commits",
        type=int,
        default=10,
        metavar="N",
        help="Number of recent commits to show (default: 10)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Number of results to display (default: 10)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Override project root detection",
    )
    return parser


def _short_hash(commit_hash: str | None) -> str:
    """Return the first 7 characters of a commit hash, or '-------' if None."""
    if commit_hash is None:
        return "-------"
    return commit_hash[:7]


def _print_trend(data: list[dict[str, Any]], output_json: bool) -> None:
    """Print per-commit average EI trend."""
    if output_json:
        print(json.dumps(data, indent=2, default=str))
        return

    if not data:
        print("  No trend data available. Run 'entropy-measure --store' first.")
        return

    # Compute deltas between consecutive commits (data is newest-first)
    print(f"  {'Commit':<10s}  {'Files':>5s}  {'Avg EI':>6s}  {'Delta':>7s}")
    print(f"  {'------':<10s}  {'-----':>5s}  {'------':>6s}  {'-----':>7s}")

    for i, row in enumerate(data):
        commit = _short_hash(str(row.get("commit_hash", "")))
        files = int(row.get("file_count", 0))
        avg_ei = float(row.get("avg_ei", 0.0))

        # Delta vs next older commit (next item in list, since list is newest-first)
        if i + 1 < len(data):
            prev_ei = float(data[i + 1].get("avg_ei", 0.0))
            delta = avg_ei - prev_ei
            delta_str = f"{delta:+.1f}"
        else:
            delta_str = "   ---"

        print(f"  {commit:<10s}  {files:>5d}  {avg_ei:>6.1f}  {delta_str:>7s}")


def _print_hotspots(data: list[dict[str, Any]], output_json: bool) -> None:
    """Print files with highest entropy index."""
    if output_json:
        print(json.dumps(data, indent=2, default=str))
        return

    if not data:
        print("  No hotspot data available. Run 'entropy-measure --store' first.")
        return

    print(f"  {'File':<50s}  {'EI':>6s}  {'Commit':<10s}")
    print(f"  {'----':<50s}  {'--':>6s}  {'------':<10s}")

    for row in data:
        filepath = str(row.get("file_path", ""))
        ei = float(row.get("entropy_index", 0.0))
        commit = _short_hash(row.get("commit_hash"))  # type: ignore[arg-type]
        print(f"  {filepath:<50s}  {ei:>6.1f}  {commit:<10s}")


def _print_file_history(
    file_path: str,
    measurements: list[Measurement],
    output_json: bool,
) -> None:
    """Print measurement history for a specific file."""
    if output_json:
        data = [
            {
                "file": m.file_path,
                "entropy_index": m.entropy_index,
                "commit": m.commit_hash,
                "measured_at": m.measured_at,
            }
            for m in measurements
        ]
        print(json.dumps(data, indent=2, default=str))
        return

    if not measurements:
        print(f"  No history for '{file_path}'. Run 'entropy-measure --store' first.")
        return

    print(f"  History for: {file_path}")
    print(f"  {'Commit':<10s}  {'EI':>6s}  {'Delta':>7s}")
    print(f"  {'------':<10s}  {'--':>6s}  {'-----':>7s}")

    # measurements are newest-first
    for i, m in enumerate(measurements):
        commit = _short_hash(m.commit_hash)
        ei = m.entropy_index

        if i + 1 < len(measurements):
            prev_ei = measurements[i + 1].entropy_index
            delta = ei - prev_ei
            delta_str = f"{delta:+.1f}"
        else:
            delta_str = "   ---"

        print(f"  {commit:<10s}  {ei:>6.1f}  {delta_str:>7s}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve() if args.project_root else find_project_root()
    db_path = get_db_path(project_root)

    if not db_path.exists():
        print(
            "  No entropy database found. Run 'entropy-measure --store' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = get_connection(db_path)

    try:
        if args.file:
            # --file mode: show history for a specific file
            measurements = get_file_history(conn, args.file, limit=args.limit)
            _print_file_history(args.file, measurements, args.output_json)

        elif args.hotspots:
            # --hotspots mode: show files with highest EI
            data = get_hotspots(conn, limit=args.limit)
            _print_hotspots(data, args.output_json)

        else:
            # Default: show trend (per-commit average EI)
            data = get_trend(conn, last_n_commits=args.commits)
            _print_trend(data, args.output_json)

    finally:
        conn.close()
