"""Self-healing hook errors via background `claude -p`."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

# -- Constants ---------------------------------------------------------------

HARNESS_ROOT = Path(__file__).resolve().parent.parent  # harness/ package root
HEAL_STATE_FILE = "harness-heal-state.json"
HEAL_COOLDOWN = timedelta(hours=1)
HEAL_MAX_ATTEMPTS = 3
HEAL_LOCK_TIMEOUT = timedelta(minutes=10)
HEAL_MAX_TURNS = 3
HEAL_MAX_BUDGET_USD = "0.50"  # Not yet used; --max-turns is primary cost control

HEAL_SYSTEM_PROMPT = (
    "You are fixing a bug in a Python CLI tool called 'harness'. "
    "Apply the MINIMAL fix only. Do not refactor, reorganize, or add features. "
    "If you are unsure of the fix, do nothing. "
    "Verify your fix produces valid Python syntax before finishing."
)


# -- Helpers -----------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _heal_state_dir() -> Path:
    """Return ~/.harness/, creating it if needed."""
    d = Path.home() / ".harness"
    d.mkdir(parents=True, exist_ok=True)
    return d


# -- Core functions ----------------------------------------------------------


def _error_signature(event: str, exc: Exception) -> str:
    """Stable 16-char hex hash for dedup based on event + error identity."""
    key = f"{event}:{type(exc).__name__}:{exc}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _read_heal_state(state_path: Path) -> dict:
    """Read state from disk; returns default state on missing/corrupt."""
    if not state_path.exists():
        return {"version": 1, "lock": None, "errors": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "lock": None, "errors": {}}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "lock": None, "errors": {}}


def _write_heal_state(state_path: Path, state: dict) -> None:
    """Atomic write: tempfile in same dir + os.replace()."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(state_path.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
        Path(tmp).replace(state_path)
    except BaseException:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise


def _is_locked(state: dict) -> bool:
    """True if lock exists and is less than HEAL_LOCK_TIMEOUT old."""
    lock = state.get("lock")
    if not lock:
        return False
    try:
        lock_time = _parse_iso(lock)
        return _now() - lock_time < HEAL_LOCK_TIMEOUT
    except (ValueError, TypeError):
        return False


def _should_heal(state: dict, sig: str) -> bool:
    """Check dedup, cooldown, max-attempts. Returns True if healing should proceed."""
    errors = state.get("errors", {})
    entry = errors.get(sig)
    if entry is None:
        return True  # First occurrence

    if entry.get("status") == "exhausted":
        return False

    # Check cooldown
    cooldown_until = entry.get("cooldown_until")
    if cooldown_until:
        try:
            if _now() < _parse_iso(cooldown_until):
                return False
        except (ValueError, TypeError):
            pass

    # Check max attempts
    return entry.get("attempts", 0) < HEAL_MAX_ATTEMPTS


def _record_attempt(state: dict, sig: str, event: str, exc: Exception) -> None:
    """Increment attempts, set cooldown, update timestamps."""
    errors = state.setdefault("errors", {})
    now = _now_iso()
    cooldown_until = (_now() + HEAL_COOLDOWN).isoformat()

    if sig in errors:
        entry = errors[sig]
        entry["last_seen"] = now
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["last_attempt"] = now
        entry["cooldown_until"] = cooldown_until
        if entry["attempts"] >= HEAL_MAX_ATTEMPTS:
            entry["status"] = "exhausted"
        else:
            entry["status"] = "attempted"
    else:
        errors[sig] = {
            "first_seen": now,
            "last_seen": now,
            "attempts": 1,
            "last_attempt": now,
            "cooldown_until": cooldown_until,
            "status": "attempted",
            "event": event,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }

    # Set lock
    state["lock"] = now


def _find_claude_cli() -> str | None:
    """Check if claude CLI is available in PATH."""
    return shutil.which("claude")


def _build_heal_prompt(event: str, exc: Exception, hook_source_path: str) -> str:
    """Construct prompt with error details and source path."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return (
        f"A hook handler crashed in the harness CLI tool. Fix the bug.\n\n"
        f"Event: {event}\n"
        f"Error type: {type(exc).__name__}\n"
        f"Error message: {exc}\n\n"
        f"Traceback:\n{tb}\n"
        f"Source file: {hook_source_path}\n\n"
        f"Read the source file, identify the root cause, and apply a minimal fix."
    )


def _spawn_heal(claude_path: str, prompt: str, harness_root: Path) -> None:
    """Launch background claude -p process. Detached, all std DEVNULL."""
    subprocess.Popen(
        [
            claude_path,
            "-p",
            prompt,
            "--allowedTools",
            "Read,Grep,Glob,Edit",
            "--max-turns",
            str(HEAL_MAX_TURNS),
            "--output-format",
            "text",
            "--append-system-prompt",
            HEAL_SYSTEM_PROMPT,
        ],
        cwd=str(harness_root.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# -- Public entry point ------------------------------------------------------


def maybe_trigger_heal(event: str, exc: Exception) -> None:
    """Trigger self-healing if appropriate. Never raises."""
    try:
        claude_path = _find_claude_cli()
        if not claude_path:
            return

        state_path = _heal_state_dir() / HEAL_STATE_FILE
        state = _read_heal_state(state_path)

        if _is_locked(state):
            return

        sig = _error_signature(event, exc)
        if not _should_heal(state, sig):
            return

        hook_source = str(Path(__file__).resolve().parent / "hook.py")
        prompt = _build_heal_prompt(event, exc, hook_source)

        _record_attempt(state, sig, event, exc)
        _write_heal_state(state_path, state)
        _spawn_heal(claude_path, prompt, HARNESS_ROOT)
    except Exception:
        pass  # Self-healing must never crash the hook
