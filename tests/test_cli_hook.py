"""Tests for harness.cli.hook — hook runner for Claude Code events."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from harness.cli.hook import (
    _emit,
    _format_feedback,
    _is_git_commit,
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
# _emit
# ---------------------------------------------------------------------------


class TestEmit:
    def test_emit_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        _emit("test feedback")
        out = capsys.readouterr().out
        data = json.loads(out)
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
