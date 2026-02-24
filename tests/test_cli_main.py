"""Tests for harness.cli.main — top-level CLI router."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from harness.cli.main import main

# ---------------------------------------------------------------------------
# No args / help
# ---------------------------------------------------------------------------


class TestRouterHelp:
    def test_no_args_prints_help(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "entropy" in out

    def test_entropy_no_subcommand_prints_help(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["entropy"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "measure" in out
        assert "report" in out
        assert "install" in out
        assert "uninstall" in out


# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------


class TestRouterDispatch:
    @patch("harness.cli.measure.main")
    def test_dispatch_measure(self, mock_main: MagicMock) -> None:
        main(["entropy", "measure", "--all", "--project-root", "/tmp/test"])
        mock_main.assert_called_once_with(["--all", "--project-root", "/tmp/test"])

    @patch("harness.cli.report.main")
    def test_dispatch_report(self, mock_main: MagicMock) -> None:
        main(["entropy", "report", "--hotspots"])
        mock_main.assert_called_once_with(["--hotspots"])

    @patch("harness.cli.install.install_main")
    def test_dispatch_install(self, mock_main: MagicMock) -> None:
        main(["entropy", "install", "--project"])
        mock_main.assert_called_once_with(["--project"])

    @patch("harness.cli.install.uninstall_main")
    def test_dispatch_uninstall(self, mock_main: MagicMock) -> None:
        main(["entropy", "uninstall"])
        mock_main.assert_called_once_with([])


# ---------------------------------------------------------------------------
# __main__.py import
# ---------------------------------------------------------------------------


class TestMainModule:
    def test_main_module_imports(self) -> None:
        """Verify __main__.py can be imported."""
        import harness.__main__  # noqa: F401
