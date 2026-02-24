"""Tests for harness.cli.heal — self-healing hook errors via background claude -p."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harness.cli.heal import (
    HARNESS_ROOT,
    HEAL_LOCK_TIMEOUT,
    HEAL_MAX_ATTEMPTS,
    HEAL_MAX_TURNS,
    HEAL_SYSTEM_PROMPT,
    _build_heal_prompt,
    _error_signature,
    _find_claude_cli,
    _heal_state_dir,
    _is_locked,
    _read_heal_state,
    _record_attempt,
    _should_heal,
    _spawn_heal,
    _write_heal_state,
    maybe_trigger_heal,
)


class TestErrorSignature:
    def test_stable_hash(self) -> None:
        """Same inputs always produce the same hash."""
        exc = ValueError("test error")
        sig1 = _error_signature("Stop", exc)
        sig2 = _error_signature("Stop", exc)
        assert sig1 == sig2
        assert len(sig1) == 16

    def test_different_errors_different_hashes(self) -> None:
        """Different error types/messages produce different hashes."""
        sig1 = _error_signature("Stop", ValueError("error A"))
        sig2 = _error_signature("Stop", RuntimeError("error B"))
        assert sig1 != sig2

    def test_traceback_irrelevant(self) -> None:
        """Hash is based on event + type + message, not traceback."""
        exc1 = ValueError("same message")
        exc2 = ValueError("same message")
        # Even though tracebacks differ (different stack frames), sig is the same
        sig1 = _error_signature("PostToolUse", exc1)
        sig2 = _error_signature("PostToolUse", exc2)
        assert sig1 == sig2


# ---------------------------------------------------------------------------
# _read_heal_state / _write_heal_state
# ---------------------------------------------------------------------------


class TestHealState:
    def test_read_missing_file(self, tmp_path: Path) -> None:
        """Missing state file returns default state."""
        state = _read_heal_state(tmp_path / "nonexistent.json")
        assert state["version"] == 1
        assert state["lock"] is None
        assert state["errors"] == {}

    def test_read_corrupt_file(self, tmp_path: Path) -> None:
        """Corrupt JSON returns default state."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json {{{", encoding="utf-8")
        state = _read_heal_state(bad_file)
        assert state["version"] == 1

    def test_read_non_dict(self, tmp_path: Path) -> None:
        """Non-dict JSON returns default state."""
        bad_file = tmp_path / "array.json"
        bad_file.write_text("[1, 2, 3]", encoding="utf-8")
        state = _read_heal_state(bad_file)
        assert state["version"] == 1

    def test_write_creates_dirs_and_roundtrips(self, tmp_path: Path) -> None:
        """Write creates parent dirs and data roundtrips correctly."""
        state_path = tmp_path / "sub" / "dir" / "state.json"
        state = {"version": 1, "lock": None, "errors": {"abc": {"attempts": 1}}}
        _write_heal_state(state_path, state)
        loaded = _read_heal_state(state_path)
        assert loaded["errors"]["abc"]["attempts"] == 1


# ---------------------------------------------------------------------------
# _should_heal
# ---------------------------------------------------------------------------


class TestShouldHeal:
    def test_first_error_allowed(self) -> None:
        """First occurrence of an error signature should be healed."""
        state = {"version": 1, "lock": None, "errors": {}}
        assert _should_heal(state, "new_sig") is True

    def test_exhausted_blocks(self) -> None:
        """Exhausted errors are never retried."""
        state = {
            "version": 1,
            "lock": None,
            "errors": {"sig1": {"status": "exhausted", "attempts": 3}},
        }
        assert _should_heal(state, "sig1") is False

    def test_cooldown_blocks(self) -> None:
        """Active cooldown prevents healing."""
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        state = {
            "version": 1,
            "lock": None,
            "errors": {
                "sig1": {
                    "status": "attempted",
                    "attempts": 1,
                    "cooldown_until": future,
                },
            },
        }
        assert _should_heal(state, "sig1") is False

    def test_cooldown_expired_allows(self) -> None:
        """Expired cooldown allows healing."""
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state = {
            "version": 1,
            "lock": None,
            "errors": {
                "sig1": {
                    "status": "attempted",
                    "attempts": 1,
                    "cooldown_until": past,
                },
            },
        }
        assert _should_heal(state, "sig1") is True

    def test_max_attempts_blocks(self) -> None:
        """Reaching max attempts blocks further healing."""
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state = {
            "version": 1,
            "lock": None,
            "errors": {
                "sig1": {
                    "status": "attempted",
                    "attempts": HEAL_MAX_ATTEMPTS,
                    "cooldown_until": past,
                },
            },
        }
        assert _should_heal(state, "sig1") is False


# ---------------------------------------------------------------------------
# _is_locked
# ---------------------------------------------------------------------------


class TestIsLocked:
    def test_no_lock_allows(self) -> None:
        """No lock means not locked."""
        assert _is_locked({"lock": None}) is False

    def test_recent_lock_blocks(self) -> None:
        """Lock within timeout window blocks."""
        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        assert _is_locked({"lock": recent}) is True

    def test_stale_lock_allows(self) -> None:
        """Lock older than timeout is treated as stale."""
        old = (datetime.now(timezone.utc) - HEAL_LOCK_TIMEOUT - timedelta(minutes=1)).isoformat()
        assert _is_locked({"lock": old}) is False


# ---------------------------------------------------------------------------
# _find_claude_cli
# ---------------------------------------------------------------------------


class TestFindClaudeCli:
    def test_found_in_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/claude")
        assert _find_claude_cli() == "/usr/local/bin/claude"

    def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert _find_claude_cli() is None


# ---------------------------------------------------------------------------
# _spawn_heal
# ---------------------------------------------------------------------------


class TestSpawnHeal:
    def test_correct_popen_args(self) -> None:
        """Popen is called with expected arguments."""
        with patch("harness.cli.heal.subprocess.Popen") as mock_popen:
            _spawn_heal("/usr/bin/claude", "fix the bug", HARNESS_ROOT)

            mock_popen.assert_called_once()
            args = mock_popen.call_args
            cmd = args[0][0]
            assert cmd[0] == "/usr/bin/claude"
            assert cmd[1] == "-p"
            assert cmd[2] == "fix the bug"
            assert "--allowedTools" in cmd
            assert "Read,Grep,Glob,Edit" in cmd
            assert "--max-turns" in cmd
            assert str(HEAL_MAX_TURNS) in cmd
            assert "--append-system-prompt" in cmd
            assert HEAL_SYSTEM_PROMPT in cmd

    def test_start_new_session(self) -> None:
        """Process is detached via start_new_session=True."""
        with patch("harness.cli.heal.subprocess.Popen") as mock_popen:
            _spawn_heal("/usr/bin/claude", "fix it", HARNESS_ROOT)

            kwargs = mock_popen.call_args[1]
            assert kwargs["start_new_session"] is True

    def test_all_std_devnull(self) -> None:
        """stdin/stdout/stderr are all DEVNULL."""
        with patch("harness.cli.heal.subprocess.Popen") as mock_popen:
            _spawn_heal("/usr/bin/claude", "fix it", HARNESS_ROOT)

            kwargs = mock_popen.call_args[1]
            assert kwargs["stdin"] == subprocess.DEVNULL
            assert kwargs["stdout"] == subprocess.DEVNULL
            assert kwargs["stderr"] == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# _build_heal_prompt
# ---------------------------------------------------------------------------


class TestBuildHealPrompt:
    def test_contains_error_type(self) -> None:
        exc = ValueError("db schema mismatch")
        prompt = _build_heal_prompt("Stop", exc, "/path/to/hook.py")
        assert "ValueError" in prompt

    def test_contains_event_name(self) -> None:
        exc = RuntimeError("connection lost")
        prompt = _build_heal_prompt("PostToolUse", exc, "/path/to/hook.py")
        assert "PostToolUse" in prompt

    def test_contains_source_path(self) -> None:
        exc = ValueError("test")
        prompt = _build_heal_prompt("Stop", exc, "/opt/harness/cli/hook.py")
        assert "/opt/harness/cli/hook.py" in prompt


# ---------------------------------------------------------------------------
# _record_attempt
# ---------------------------------------------------------------------------


class TestRecordAttempt:
    def test_first_attempt_creates_entry(self) -> None:
        state: dict = {"version": 1, "lock": None, "errors": {}}
        exc = ValueError("test")
        _record_attempt(state, "sig1", "Stop", exc)
        assert "sig1" in state["errors"]
        assert state["errors"]["sig1"]["attempts"] == 1
        assert state["errors"]["sig1"]["status"] == "attempted"
        assert state["errors"]["sig1"]["error_type"] == "ValueError"
        assert state["lock"] is not None

    def test_second_attempt_increments(self) -> None:
        state: dict = {
            "version": 1,
            "lock": None,
            "errors": {
                "sig1": {
                    "first_seen": "2026-01-01T00:00:00+00:00",
                    "last_seen": "2026-01-01T00:00:00+00:00",
                    "attempts": 1,
                    "last_attempt": "2026-01-01T00:00:00+00:00",
                    "cooldown_until": "2026-01-01T01:00:00+00:00",
                    "status": "attempted",
                    "event": "Stop",
                    "error_type": "ValueError",
                    "error_message": "test",
                },
            },
        }
        _record_attempt(state, "sig1", "Stop", ValueError("test"))
        assert state["errors"]["sig1"]["attempts"] == 2

    def test_max_attempts_marks_exhausted(self) -> None:
        state: dict = {
            "version": 1,
            "lock": None,
            "errors": {
                "sig1": {
                    "first_seen": "2026-01-01T00:00:00+00:00",
                    "last_seen": "2026-01-01T00:00:00+00:00",
                    "attempts": HEAL_MAX_ATTEMPTS - 1,
                    "last_attempt": "2026-01-01T00:00:00+00:00",
                    "cooldown_until": "2026-01-01T01:00:00+00:00",
                    "status": "attempted",
                    "event": "Stop",
                    "error_type": "ValueError",
                    "error_message": "test",
                },
            },
        }
        _record_attempt(state, "sig1", "Stop", ValueError("test"))
        assert state["errors"]["sig1"]["status"] == "exhausted"


class TestMaybeTriggerHeal:
    def test_no_claude_no_heal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When claude is not in PATH, no healing occurs."""
        monkeypatch.setattr("harness.cli.heal._find_claude_cli", lambda: None)
        mock_spawn = MagicMock()
        monkeypatch.setattr("harness.cli.heal._spawn_heal", mock_spawn)

        maybe_trigger_heal("Stop", ValueError("test"))
        mock_spawn.assert_not_called()

    def test_first_error_triggers_heal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """First occurrence of an error triggers a heal spawn."""
        monkeypatch.setattr("harness.cli.heal._find_claude_cli", lambda: "/usr/bin/claude")
        monkeypatch.setattr("harness.cli.heal._heal_state_dir", lambda: tmp_path)
        mock_spawn = MagicMock()
        monkeypatch.setattr("harness.cli.heal._spawn_heal", mock_spawn)

        maybe_trigger_heal("Stop", ValueError("db exploded"))
        mock_spawn.assert_called_once()

        # Verify state was written
        state_path = tmp_path / "harness-heal-state.json"
        assert state_path.exists()

    def test_cooldown_skips_heal(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Error within cooldown window does not trigger heal."""
        monkeypatch.setattr("harness.cli.heal._find_claude_cli", lambda: "/usr/bin/claude")
        monkeypatch.setattr("harness.cli.heal._heal_state_dir", lambda: tmp_path)
        mock_spawn = MagicMock()
        monkeypatch.setattr("harness.cli.heal._spawn_heal", mock_spawn)

        # First call triggers
        exc = ValueError("db exploded")
        maybe_trigger_heal("Stop", exc)
        assert mock_spawn.call_count == 1

        # Second call with same error is within cooldown
        maybe_trigger_heal("Stop", exc)
        assert mock_spawn.call_count == 1  # Still 1, no second call

    def test_different_error_triggers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A different error triggers even when another is in cooldown."""
        monkeypatch.setattr("harness.cli.heal._find_claude_cli", lambda: "/usr/bin/claude")
        monkeypatch.setattr("harness.cli.heal._heal_state_dir", lambda: tmp_path)
        mock_spawn = MagicMock()
        monkeypatch.setattr("harness.cli.heal._spawn_heal", mock_spawn)

        maybe_trigger_heal("Stop", ValueError("error A"))
        assert mock_spawn.call_count == 1

        # Different error — but now there's a lock from the first call
        # We need to clear the lock for this test to work
        state_path = tmp_path / "harness-heal-state.json"
        state = json.loads(state_path.read_text())
        state["lock"] = None
        state_path.write_text(json.dumps(state))

        maybe_trigger_heal("Stop", RuntimeError("error B"))
        assert mock_spawn.call_count == 2

    def test_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """maybe_trigger_heal never raises, even on internal errors."""
        monkeypatch.setattr("harness.cli.heal._find_claude_cli", lambda: "/usr/bin/claude")

        def explode(*_args: object, **_kw: object) -> None:
            msg = "internal failure"
            raise OSError(msg)

        monkeypatch.setattr("harness.cli.heal._read_heal_state", explode)

        # Must not raise
        maybe_trigger_heal("Stop", ValueError("test"))

    def test_lock_skips_heal(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Active lock prevents healing."""
        monkeypatch.setattr("harness.cli.heal._find_claude_cli", lambda: "/usr/bin/claude")
        monkeypatch.setattr("harness.cli.heal._heal_state_dir", lambda: tmp_path)
        mock_spawn = MagicMock()
        monkeypatch.setattr("harness.cli.heal._spawn_heal", mock_spawn)

        # Pre-create state with a recent lock
        recent_lock = datetime.now(timezone.utc).isoformat()
        state = {"version": 1, "lock": recent_lock, "errors": {}}
        (tmp_path / "harness-heal-state.json").write_text(json.dumps(state))

        maybe_trigger_heal("Stop", ValueError("test"))
        mock_spawn.assert_not_called()


class TestHealStateDir:
    def test_returns_dot_harness_in_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_heal_state_dir returns ~/.harness/ and creates it."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        result = _heal_state_dir()
        assert result == tmp_path / ".harness"
        assert result.is_dir()

    def test_idempotent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Calling twice doesn't fail (exist_ok=True)."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _heal_state_dir()
        result = _heal_state_dir()
        assert result == tmp_path / ".harness"
