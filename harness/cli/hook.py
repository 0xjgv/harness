"""Hook runner for Claude Code — reads stdin JSON, dispatches to PostToolUse/Stop handlers."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3


# -- Helpers ---------------------------------------------------------


def _emit_post_tool_use(feedback: str) -> None:
    """Print hookSpecificOutput JSON to stdout for PostToolUse hooks."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": feedback,
        },
    }
    print(json.dumps(output))


def _log_hook_error(event: str, exc: Exception) -> None:
    """Best-effort error logging to .claude/harness-hook-errors.log (bounded, never raises)."""
    try:
        import traceback as _tb  # noqa: PLC0415
        from datetime import datetime, timezone  # noqa: PLC0415

        from harness.config import find_project_root  # noqa: PLC0415

        project_root = find_project_root()

        log_dir = project_root / ".claude"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "harness-hook-errors.log"

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tb = _tb.format_exception(type(exc), exc, exc.__traceback__)
        entry = f"[{ts}] event={event} error={type(exc).__name__}: {exc}\n{''.join(tb)}"

        # Bounded: keep last 49 entries + new one = 50 max
        delimiter = "\n=== ENTRY ===\n"
        existing = ""
        if log_path.exists():
            existing = log_path.read_text(encoding="utf-8")
        entries = [e for e in existing.split(delimiter) if e.strip()]
        entries = entries[-(49):] if len(entries) >= 49 else entries
        entries.append(entry)
        log_path.write_text(delimiter.join(entries) + delimiter, encoding="utf-8")
    except Exception:
        pass  # Error handler must never raise


def _ensure_harness() -> bool:
    """Check if harness is importable."""
    try:
        import harness  # noqa: F401, PLC0415

        return True
    except ImportError:
        return False


def _is_git_commit(data: dict[str, Any]) -> bool:
    """Return True when the hook event is a successful git commit."""
    if data.get("tool_name") != "Bash":
        return False
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    cmd = str(tool_input.get("command", ""))
    if not re.search(r"\bgit\s+commit\b", cmd):
        return False
    tool_result = data.get("tool_result")
    if not isinstance(tool_result, dict):
        return True  # No result info — assume success
    exit_code = tool_result.get("exitCode")
    return exit_code is None or exit_code == 0


# -- Measurement helpers ---------------------------------------------


def _measure_files(
    py_files: list[str],
    commit: str,
    project_root: Path,
    conn: sqlite3.Connection,
) -> list[tuple[str, float, float, float, bool]]:
    """Measure entropy for each file and return deltas.

    Returns list of (path, before_ei, after_ei, delta, is_new_file).
    """
    from harness.core.composite import compute_entropy_index  # noqa: PLC0415
    from harness.core.db import (  # noqa: PLC0415
        Measurement,
        get_previous_measurement,
        store_measurement,
    )
    from harness.core.metrics import measure_file  # noqa: PLC0415
    from harness.git import get_file_at_commit  # noqa: PLC0415

    deltas: list[tuple[str, float, float, float, bool]] = []

    for filepath in py_files:
        content = get_file_at_commit(
            filepath,
            commit,
            cwd=project_root,
        )
        if content is None:
            continue

        try:
            metrics = measure_file(None, content=content)
            ei = compute_entropy_index(metrics)
        except Exception:
            continue

        measurement = Measurement(
            file_path=filepath,
            commit_hash=commit,
            measured_at=time.time(),
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
        store_measurement(conn, measurement)

        prev = get_previous_measurement(conn, filepath, commit)
        if prev:
            delta = ei - prev.entropy_index
            deltas.append((filepath, prev.entropy_index, ei, delta, False))
        else:
            deltas.append((filepath, 0.0, ei, 0.0, True))

    return deltas


def _format_feedback(
    short_hash: str,
    total_delta: float,
    file_deltas: list[tuple[str, float, float, float, bool]],
) -> str:
    """Build human-readable entropy feedback string."""
    from harness.config import (  # noqa: PLC0415
        DELTA_POSITIVE_FLOOR,
        DELTA_SUGGESTION_CEILING,
        DELTA_WARNING_CEILING,
    )

    lines: list[str] = []

    # Headline
    if total_delta <= DELTA_POSITIVE_FLOOR:
        label = "(simplified)"
    elif total_delta <= DELTA_SUGGESTION_CEILING:
        label = ""
    elif total_delta <= DELTA_WARNING_CEILING:
        label = "(increased complexity)"
    else:
        label = "(significant complexity increase)"
    head = f"[Entropy] Commit {short_hash}: {total_delta:+.0f} EI"
    lines.append(f"{head} {label}".rstrip())

    # Per-file details for significant changes
    ranked = sorted(
        file_deltas,
        key=lambda x: abs(x[3]),
        reverse=True,
    )
    for filepath, before_ei, after_ei, delta, is_new in ranked:
        if is_new:
            lines.append(f"  {filepath}: EI {after_ei:.0f} (new file)")
            continue
        if abs(delta) < 2.0:
            continue
        lines.append(
            f"  {filepath}: {delta:+.1f} EI ({before_ei:.0f} -> {after_ei:.0f})",
        )

    # Guidance for large increases (exclude new files)
    non_new_deltas = [d for d in ranked if not d[4]]
    if total_delta > DELTA_SUGGESTION_CEILING and non_new_deltas:
        biggest = max(non_new_deltas, key=lambda x: x[3])
        if biggest[3] > 5:
            lines.append(
                f"  Tip: Consider simplifying {biggest[0]}",
            )

    return "\n".join(lines)


# -- Event handlers --------------------------------------------------


def handle_commit() -> None:
    """Measure entropy changes for the latest commit."""
    if not _ensure_harness():
        return

    from harness.config import (  # noqa: PLC0415
        DELTA_NEUTRAL_CEILING,
        DELTA_POSITIVE_FLOOR,
        get_db_path,
    )
    from harness.core.db import get_connection  # noqa: PLC0415
    from harness.git import (  # noqa: PLC0415
        get_changed_files,
        get_current_commit,
    )

    project_root = Path.cwd()
    commit = get_current_commit(cwd=project_root)
    if not commit:
        return

    changed = get_changed_files(commit, cwd=project_root)
    py_files = [f for f in changed if f.endswith(".py")]
    if not py_files:
        return

    conn = get_connection(get_db_path(project_root))
    file_deltas = _measure_files(
        py_files,
        commit,
        project_root,
        conn,
    )
    conn.close()

    if not file_deltas:
        return

    # Only count real deltas (not new files) toward the neutral band check
    non_new_deltas = [d for d in file_deltas if not d[4]]
    total_delta = sum(d[3] for d in non_new_deltas)

    # Asymmetric neutral band: silence if within -5 to +2
    if DELTA_POSITIVE_FLOOR <= total_delta <= DELTA_NEUTRAL_CEILING:
        return

    feedback = _format_feedback(
        commit[:7],
        total_delta,
        file_deltas,
    )
    _emit_post_tool_use(feedback)


def handle_session_summary() -> None:
    """Provide session summary of entropy changes on Stop."""
    if not _ensure_harness():
        return

    from harness.config import get_db_path  # noqa: PLC0415
    from harness.core.db import (  # noqa: PLC0415
        get_connection,
        get_trend,
    )

    project_root = Path.cwd()
    db_path = get_db_path(project_root)
    if not db_path.exists():
        return

    conn = get_connection(db_path)
    trend = get_trend(conn, last_n_commits=20)
    conn.close()

    if len(trend) < 2:
        return

    lines = [f"[Entropy Summary] Last {len(trend)} commits:"]
    total_delta = 0.0
    for i, entry in enumerate(trend):
        short = entry["commit_hash"][:7]
        avg_ei = entry["avg_ei"]
        delta_str = ""
        if i < len(trend) - 1:
            prev_ei = trend[i + 1]["avg_ei"]
            delta = avg_ei - prev_ei
            total_delta += delta
            if delta <= -5:
                delta_str = f" {delta:+.0f} EI (simplified)"
            elif delta > 2:
                delta_str = f" {delta:+.0f} EI (increased)"
            else:
                delta_str = f" {delta:+.0f} EI"
        lines.append(f"  {short}: EI {avg_ei:.0f}{delta_str}")

    if total_delta != 0:
        lines.append(f"  Net: {total_delta:+.0f} EI")

    print("\n".join(lines))


# -- Entrypoint ------------------------------------------------------


def _dispatch_post_tool_use(data: dict[str, Any]) -> None:
    """Handle PostToolUse events."""
    if not _is_git_commit(data):
        return
    handle_commit()


def hook_run_main(_argv: list[str] | None = None) -> None:
    """Entry point for hook-run subcommand: read hook event from stdin, dispatch."""
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    event = data.get("hook_event_name", "")

    try:
        if event == "PostToolUse":
            _dispatch_post_tool_use(data)
        elif event == "Stop":
            handle_session_summary()
    except Exception as exc:
        _log_hook_error(event, exc)
        _maybe_heal(event, exc)


def _maybe_heal(event: str, exc: Exception) -> None:
    """Best-effort self-healing trigger. Never raises."""
    try:
        from harness.cli.heal import maybe_trigger_heal  # noqa: PLC0415

        maybe_trigger_heal(event, exc)
    except Exception:
        pass
