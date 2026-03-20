"""Tests for harness.cli.hook — hook runner for Claude Code events."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from harness.cli.hook import (
    _emit_post_tool_use,
    _format_feedback,
    _is_git_commit,
    _log_hook_error,
    handle_session_summary,
    hook_run_main,
)

# ---------------------------------------------------------------------------
# _is_git_commit detection
# ---------------------------------------------------------------------------


class TestIsGitCommit:
    def test_basic_git_commit(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {"exitCode": 0},
        }
        assert _is_git_commit(data) is True

    def test_git_commit_with_flags(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit --amend --no-edit"},
            "tool_result": {"exitCode": 0},
        }
        assert _is_git_commit(data) is True

    def test_not_bash_tool(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Write",
            "tool_input": {"command": "git commit -m 'test'"},
        }
        assert _is_git_commit(data) is False

    def test_not_git_commit_command(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        }
        assert _is_git_commit(data) is False

    def test_failed_git_commit(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {"exitCode": 1},
        }
        assert _is_git_commit(data) is False

    def test_no_tool_result(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
        }
        assert _is_git_commit(data) is True

    def test_no_exit_code(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {},
        }
        assert _is_git_commit(data) is True

    def test_non_dict_tool_input(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": "git commit -m 'test'",
        }
        assert _is_git_commit(data) is False

    def test_non_dict_tool_result(self) -> None:
        data: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": "success",
        }
        assert _is_git_commit(data) is True


# ---------------------------------------------------------------------------
# _emit_post_tool_use
# ---------------------------------------------------------------------------


class TestEmitPostToolUse:
    def test_emit_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        _emit_post_tool_use("test feedback")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert data["hookSpecificOutput"]["additionalContext"] == "test feedback"


# ---------------------------------------------------------------------------
# _format_feedback
# ---------------------------------------------------------------------------


class TestFormatFeedback:
    def test_simplified(self) -> None:
        result = _format_feedback("abc1234", -10.0, [("a.py", 50.0, 40.0, -10.0, False)])
        assert "(simplified)" in result
        assert "abc1234" in result

    def test_neutral(self) -> None:
        result = _format_feedback("abc1234", 5.0, [("a.py", 50.0, 55.0, 5.0, False)])
        assert "(simplified)" not in result
        assert "(increased complexity)" not in result

    def test_increased_complexity(self) -> None:
        result = _format_feedback("abc1234", 15.0, [("a.py", 50.0, 65.0, 15.0, False)])
        assert "(increased complexity)" in result

    def test_significant_increase_with_tip(self) -> None:
        result = _format_feedback("abc1234", 30.0, [("a.py", 50.0, 80.0, 30.0, False)])
        assert "(significant complexity increase)" in result
        assert "Tip: Consider simplifying" in result

    def test_new_file_annotation(self) -> None:
        result = _format_feedback("abc1234", 0.0, [("new.py", 0.0, 35.0, 0.0, True)])
        assert "(new file)" in result
        assert "new.py" in result
        assert "EI 35" in result

    def test_new_file_excluded_from_delta_total(self) -> None:
        """New files should not inflate the total delta or trigger tips."""
        deltas = [
            ("existing.py", 50.0, 53.0, 3.0, False),
            ("brand_new.py", 0.0, 60.0, 0.0, True),
        ]
        # total_delta is 3.0 (only from existing.py), which is in the neutral range
        result = _format_feedback("abc1234", 3.0, deltas)
        assert "(new file)" in result
        assert "Tip:" not in result

    def test_new_file_not_in_tip(self) -> None:
        """Even with high EI, new files should not trigger the simplify tip."""
        deltas = [
            ("changed.py", 50.0, 80.0, 30.0, False),
            ("huge_new.py", 0.0, 90.0, 0.0, True),
        ]
        result = _format_feedback("abc1234", 30.0, deltas)
        assert "Tip: Consider simplifying changed.py" in result
        # The tip should NOT point to the new file
        assert "Tip: Consider simplifying huge_new.py" not in result


# ---------------------------------------------------------------------------
# hook_run_main
# ---------------------------------------------------------------------------


class TestHookRunMain:
    def test_malformed_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        hook_run_main()  # Should not raise

    def test_empty_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        hook_run_main()  # Should not raise

    def test_unknown_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = json.dumps({"hook_event_name": "PreToolUse"})
        monkeypatch.setattr("sys.stdin", io.StringIO(data))
        hook_run_main()  # Should not raise

    def test_post_tool_use_non_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data = json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        })
        monkeypatch.setattr("sys.stdin", io.StringIO(data))
        hook_run_main()  # Should not raise, no-op for non-commit


# ---------------------------------------------------------------------------
# Stop output validation
# ---------------------------------------------------------------------------


class _FakeConn:
    def close(self) -> None:
        pass


class TestStopOutput:
    def test_stop_outputs_plain_text(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """Stop hook output must be plain text, not hookSpecificOutput JSON."""
        monkeypatch.setattr("harness.cli.hook._ensure_harness", lambda: True)
        # H3: Create a real db file in tmp_path instead of global Path.exists patch
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        fake_db = claude_dir / "entropy.db"
        fake_db.write_text("")
        # Patch at source modules since handle_session_summary uses lazy imports
        monkeypatch.setattr("harness.config.get_db_path", lambda _: fake_db)
        monkeypatch.setattr("harness.core.db.get_connection", lambda _: _FakeConn())
        monkeypatch.setattr(
            "harness.core.db.get_trend",
            lambda _conn, last_n_commits: [
                {"commit_hash": "abc1234def", "avg_ei": 52.0},
                {"commit_hash": "def5678abc", "avg_ei": 50.0},
            ],
        )
        monkeypatch.setattr("harness.cli.hook.Path.cwd", lambda: tmp_path)

        handle_session_summary()
        out = capsys.readouterr().out

        # Must be plain text
        assert "[Entropy Summary]" in out
        # Must NOT be hookSpecificOutput JSON
        assert "hookSpecificOutput" not in out
        assert "hookEventName" not in out

    def test_stop_no_output_when_insufficient_data(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """Stop hook produces nothing when fewer than 2 trend entries exist."""
        monkeypatch.setattr("harness.cli.hook._ensure_harness", lambda: True)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        fake_db = claude_dir / "entropy.db"
        fake_db.write_text("")
        monkeypatch.setattr("harness.config.get_db_path", lambda _: fake_db)
        monkeypatch.setattr("harness.core.db.get_connection", lambda _: _FakeConn())
        monkeypatch.setattr(
            "harness.core.db.get_trend",
            lambda _conn, last_n_commits: [
                {"commit_hash": "abc1234def", "avg_ei": 52.0},
            ],
        )
        monkeypatch.setattr("harness.cli.hook.Path.cwd", lambda: tmp_path)

        handle_session_summary()
        out = capsys.readouterr().out
        assert out == ""


# ---------------------------------------------------------------------------
# Fault tolerance
# ---------------------------------------------------------------------------


class TestFaultTolerance:
    def test_handler_exception_produces_no_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When a handler raises, hook_run_main exits normally with no stdout."""
        data = json.dumps({"hook_event_name": "Stop"})
        monkeypatch.setattr("sys.stdin", io.StringIO(data))

        # H5: Use raise_error() helper, not generator-throw idiom
        def raise_error() -> None:
            msg = "db exploded"
            raise RuntimeError(msg)

        monkeypatch.setattr("harness.cli.hook.handle_session_summary", raise_error)
        monkeypatch.setattr("harness.cli.hook._log_hook_error", lambda *a: None)

        hook_run_main()  # Must not raise
        out = capsys.readouterr().out
        assert out == ""

    def test_handler_exception_logs_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a handler raises, the error is logged to disk."""
        data = json.dumps({"hook_event_name": "Stop"})
        monkeypatch.setattr("sys.stdin", io.StringIO(data))

        def raise_error() -> None:
            msg = "db exploded"
            raise RuntimeError(msg)

        monkeypatch.setattr("harness.cli.hook.handle_session_summary", raise_error)

        log_calls: list[tuple[str, Exception]] = []

        def capture_log(event: str, exc: Exception) -> None:
            log_calls.append((event, exc))

        monkeypatch.setattr("harness.cli.hook._log_hook_error", capture_log)

        hook_run_main()
        assert len(log_calls) == 1
        assert log_calls[0][0] == "Stop"
        assert isinstance(log_calls[0][1], RuntimeError)

    def test_system_exit_propagates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SystemExit must NOT be caught by the fault-tolerant wrapper."""
        data = json.dumps({"hook_event_name": "Stop"})
        monkeypatch.setattr("sys.stdin", io.StringIO(data))

        def raise_error() -> None:
            raise SystemExit(1)

        monkeypatch.setattr("harness.cli.hook.handle_session_summary", raise_error)
        monkeypatch.setattr("harness.cli.hook._log_hook_error", lambda *a: None)

        with pytest.raises(SystemExit):
            hook_run_main()

    def test_keyboard_interrupt_propagates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KeyboardInterrupt must NOT be caught by the fault-tolerant wrapper."""
        data = json.dumps({"hook_event_name": "Stop"})
        monkeypatch.setattr("sys.stdin", io.StringIO(data))

        def raise_error() -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr("harness.cli.hook.handle_session_summary", raise_error)
        monkeypatch.setattr("harness.cli.hook._log_hook_error", lambda *a: None)

        with pytest.raises(KeyboardInterrupt):
            hook_run_main()


# ---------------------------------------------------------------------------
# _log_hook_error
# ---------------------------------------------------------------------------


class TestLogHookError:
    def test_creates_log_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """First error creates the log file."""
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path)

        exc = ValueError("test error")
        _log_hook_error("PostToolUse", exc)

        log_path = tmp_path / ".claude" / "harness-hook-errors.log"
        assert log_path.exists()
        content = log_path.read_text()
        assert "PostToolUse" in content
        assert "ValueError" in content
        assert "test error" in content

    def test_bounded_to_50_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Log never exceeds 50 entries."""
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path)

        for i in range(60):
            exc = ValueError(f"error {i}")
            _log_hook_error("Stop", exc)

        log_path = tmp_path / ".claude" / "harness-hook-errors.log"
        content = log_path.read_text()
        delimiter = "\n=== ENTRY ===\n"
        entries = [e for e in content.split(delimiter) if e.strip()]
        assert len(entries) <= 50
        # Most recent entry should be error 59
        assert "error 59" in entries[-1]
        # Oldest entries should be dropped
        assert "error 0" not in content

    def test_silent_on_write_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_log_hook_error never raises, even on filesystem errors."""
        monkeypatch.setattr("pathlib.Path.cwd", lambda: Path("/nonexistent/path"))

        # Must not raise
        _log_hook_error("Stop", ValueError("test"))


class TestMaybeHeal:
    def test_handler_exception_triggers_heal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When a handler raises, _maybe_heal is called with (event, exc)."""
        data = json.dumps({"hook_event_name": "Stop"})
        monkeypatch.setattr("sys.stdin", io.StringIO(data))

        def raise_error() -> None:
            msg = "db exploded"
            raise RuntimeError(msg)

        monkeypatch.setattr("harness.cli.hook.handle_session_summary", raise_error)
        monkeypatch.setattr("harness.cli.hook._log_hook_error", lambda *a: None)

        heal_calls: list[tuple[str, Exception]] = []

        def capture_heal(event: str, exc: Exception) -> None:
            heal_calls.append((event, exc))

        monkeypatch.setattr("harness.cli.hook._maybe_heal", capture_heal)

        hook_run_main()
        assert len(heal_calls) == 1
        assert heal_calls[0][0] == "Stop"
        assert isinstance(heal_calls[0][1], RuntimeError)


# ---------------------------------------------------------------------------
# handle_session_start
# ---------------------------------------------------------------------------


class TestHandleSessionStart:
    def test_runs_context_script(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from harness.cli.hook import handle_session_start

        context_called = []
        monkeypatch.setattr(
            "harness.cli.context.run_context_script",
            lambda *a, **kw: context_called.append(True) or 0,
        )
        monkeypatch.setattr("harness.cli.hook._ensure_harness", lambda: False)

        handle_session_start()
        assert len(context_called) == 1

    def test_skips_non_python_project(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from harness.cli.hook import handle_session_start

        monkeypatch.setattr(
            "harness.cli.context.run_context_script",
            lambda *a, **kw: 0,
        )
        monkeypatch.setattr("harness.cli.hook._ensure_harness", lambda: True)
        monkeypatch.setattr(
            "harness.config.is_python_project",
            lambda root=None: False,
        )
        monkeypatch.setattr(
            "harness.config.find_project_root",
            lambda start=None: tmp_path,
        )

        # Should not raise, should not try to seed/install
        handle_session_start()

    def test_triggers_auto_seed_and_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from harness.cli.hook import handle_session_start

        monkeypatch.setattr(
            "harness.cli.context.run_context_script",
            lambda *a, **kw: 0,
        )
        monkeypatch.setattr("harness.cli.hook._ensure_harness", lambda: True)
        monkeypatch.setattr(
            "harness.config.is_python_project",
            lambda root=None: True,
        )
        monkeypatch.setattr(
            "harness.config.find_project_root",
            lambda start=None: tmp_path,
        )

        seed_calls = []
        install_calls = []
        monkeypatch.setattr(
            "harness.cli.hook._auto_seed",
            seed_calls.append,
        )
        monkeypatch.setattr(
            "harness.cli.hook._auto_install_hooks",
            install_calls.append,
        )

        handle_session_start()
        assert seed_calls == [tmp_path]
        assert install_calls == [tmp_path]

    def test_context_failure_does_not_block(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from harness.cli.hook import handle_session_start

        monkeypatch.setattr(
            "harness.cli.context.run_context_script",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr("harness.cli.hook._ensure_harness", lambda: True)
        monkeypatch.setattr(
            "harness.config.is_python_project",
            lambda root=None: True,
        )
        monkeypatch.setattr(
            "harness.config.find_project_root",
            lambda start=None: tmp_path,
        )
        monkeypatch.setattr("harness.cli.hook._auto_seed", lambda root: None)
        monkeypatch.setattr(
            "harness.cli.hook._auto_install_hooks",
            lambda root: None,
        )

        handle_session_start()  # Must not raise


# ---------------------------------------------------------------------------
# _auto_seed
# ---------------------------------------------------------------------------


class TestAutoSeed:
    def test_skips_if_measurements_exist(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from harness.cli.hook import _auto_seed
        from harness.core.db import get_connection, store_measurement

        db_path = tmp_path / ".claude" / "entropy.db"
        db_path.parent.mkdir(parents=True)
        monkeypatch.setattr("harness.config.get_db_path", lambda root=None: db_path)

        conn = get_connection(db_path)
        from tests.test_db import _make_measurement

        store_measurement(conn, _make_measurement())
        conn.close()

        # Should return without calling seed_project
        seed_calls = []
        monkeypatch.setattr(
            "harness.cli.seed.seed_project",
            lambda root, **kw: seed_calls.append(root),
        )
        _auto_seed(tmp_path)
        assert seed_calls == []

    def test_seeds_if_empty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.hook import _auto_seed
        from harness.cli.seed import SeedSummary

        monkeypatch.setattr(
            "harness.config.get_db_path",
            lambda root=None: tmp_path / ".claude" / "entropy.db",
        )
        monkeypatch.setattr(
            "harness.cli.seed.seed_project",
            lambda root, **kw: SeedSummary(
                files_measured=5,
                files_skipped=0,
                avg_entropy_index=42.0,
                commit_hash="abc",
                db_path=tmp_path / ".claude" / "entropy.db",
                results=[],
            ),
        )

        _auto_seed(tmp_path)
        out = capsys.readouterr().out
        assert "[Entropy] Auto-seeded 5 files" in out


# ---------------------------------------------------------------------------
# _auto_install_hooks
# ---------------------------------------------------------------------------


class TestAutoInstallHooks:
    def test_installs_per_project_hooks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.hook import _auto_install_hooks

        monkeypatch.setattr(
            "harness.cli.install._find_harness_command",
            lambda: "/usr/bin/harness",
        )
        monkeypatch.setattr(
            "harness.cli.install._find_hook_command",
            lambda: "/usr/bin/harness-hook-run",
        )

        _auto_install_hooks(tmp_path)
        out = capsys.readouterr().out
        assert "[Entropy] Auto-installed per-project hooks" in out

        settings_file = tmp_path / ".claude" / "settings.local.json"
        assert settings_file.exists()
        data = json.loads(settings_file.read_text())
        assert "PostToolUse" in data["hooks"]
        assert "Stop" in data["hooks"]
        # Per-project auto-install should NOT add SessionStart
        assert "SessionStart" not in data["hooks"]

    def test_idempotent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.hook import _auto_install_hooks

        monkeypatch.setattr(
            "harness.cli.install._find_harness_command",
            lambda: "/usr/bin/harness",
        )
        monkeypatch.setattr(
            "harness.cli.install._find_hook_command",
            lambda: "/usr/bin/harness-hook-run",
        )

        _auto_install_hooks(tmp_path)
        capsys.readouterr()  # Clear

        _auto_install_hooks(tmp_path)
        out = capsys.readouterr().out
        assert out == ""  # No output on second call

    def test_hook_run_dispatches_session_start(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """hook_run_main dispatches SessionStart to handle_session_start."""
        data = json.dumps({"hook_event_name": "SessionStart"})
        monkeypatch.setattr("sys.stdin", io.StringIO(data))

        calls = []
        monkeypatch.setattr(
            "harness.cli.hook.handle_session_start",
            lambda: calls.append(True),
        )

        hook_run_main()
        assert len(calls) == 1
