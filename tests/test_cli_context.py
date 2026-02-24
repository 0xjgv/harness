"""Tests for harness.cli.context — context.sh runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harness.cli.context import SCRIPT_PATH, run_main

# ---------------------------------------------------------------------------
# Script path resolution
# ---------------------------------------------------------------------------


class TestScriptPath:
    def test_script_path_points_to_harness_scripts(self) -> None:
        assert SCRIPT_PATH.name == "context.sh"
        assert SCRIPT_PATH.parent.name == "scripts"
        # Parent of scripts/ should be the harness package
        assert SCRIPT_PATH.parent.parent.name == "harness"

    def test_script_exists(self) -> None:
        assert SCRIPT_PATH.exists(), f"context.sh not found at {SCRIPT_PATH}"


# ---------------------------------------------------------------------------
# run_main subprocess invocation
# ---------------------------------------------------------------------------


class TestRunMain:
    @patch("harness.cli.context.subprocess.run")
    def test_invokes_bash_with_script(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)

        with pytest.raises(SystemExit) as exc_info:
            run_main([])
        assert exc_info.value.code == 0
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "bash"
        assert cmd[1] == str(SCRIPT_PATH)

    @patch("harness.cli.context.subprocess.run")
    def test_forwards_args(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)

        with pytest.raises(SystemExit):
            run_main(["--short", "--depth", "2", "/tmp/target"])
        cmd = mock_run.call_args[0][0]
        assert cmd[2:] == ["--short", "--depth", "2", "/tmp/target"]

    @patch("harness.cli.context.subprocess.run")
    def test_exits_with_script_exit_code(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=42)

        with pytest.raises(SystemExit) as exc_info:
            run_main([])
        assert exc_info.value.code == 42

    @patch("harness.cli.context.subprocess.run")
    def test_stdin_is_devnull(self, mock_run: MagicMock) -> None:
        import subprocess

        mock_run.return_value = MagicMock(returncode=0)

        with pytest.raises(SystemExit):
            run_main([])
        assert mock_run.call_args[1]["stdin"] == subprocess.DEVNULL

    def test_missing_script_exits_with_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "harness.cli.context.SCRIPT_PATH",
            Path("/nonexistent/context.sh"),
        )
        with pytest.raises(SystemExit) as exc_info:
            run_main([])
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "context.sh not found" in err


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------


class TestRouterIntegration:
    @patch("harness.cli.context.run_main")
    def test_main_dispatches_context_run(self, mock_run: MagicMock) -> None:
        from harness.cli.main import main

        main(["context", "run", "--short"])
        mock_run.assert_called_once_with(["--short"])

    def test_context_no_subcommand_prints_help(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.main import main

        with pytest.raises(SystemExit) as exc_info:
            main(["context"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "run" in out
