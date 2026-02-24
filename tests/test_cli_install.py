"""Tests for harness.cli.install — install/uninstall Claude Code hooks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.cli.install import (
    _add_harness_hooks,
    _has_harness_hooks,
    _read_settings,
    _remove_harness_hooks,
    _write_settings,
    install_main,
    uninstall_main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_HARNESS_PATH = "/usr/local/bin/harness"
HOOK_COMMAND = f"{FAKE_HARNESS_PATH} entropy hook-run"
CONTEXT_COMMAND = f"{FAKE_HARNESS_PATH} context run"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A tmp_path that looks like a project root."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    return tmp_path


def _mock_which(monkeypatch: pytest.MonkeyPatch, path: str | None = FAKE_HARNESS_PATH) -> None:
    monkeypatch.setattr("harness.cli.install._find_harness_command", lambda: path)


# ---------------------------------------------------------------------------
# _read_settings / _write_settings
# ---------------------------------------------------------------------------


class TestReadWriteSettings:
    def test_read_missing_file(self, tmp_path: Path) -> None:
        result = _read_settings(tmp_path / "nonexistent.json")
        assert result == {}

    def test_read_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text("")
        assert _read_settings(p) == {}

    def test_read_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "settings.json"
        p.write_text('{"key": "value"}')
        assert _read_settings(p) == {"key": "value"}

    def test_read_malformed_json_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            _read_settings(p)

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / "dir" / "settings.json"
        _write_settings(p, {"hello": "world"})
        assert p.exists()
        data = json.loads(p.read_text())
        assert data == {"hello": "world"}

    def test_write_produces_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "settings.json"
        _write_settings(p, {"hooks": {}})
        data = json.loads(p.read_text())
        assert data == {"hooks": {}}


# ---------------------------------------------------------------------------
# _add_harness_hooks / _has_harness_hooks / _remove_harness_hooks
# ---------------------------------------------------------------------------


class TestHookMergeLogic:
    def test_add_to_empty_settings(self) -> None:
        settings: dict[str, object] = {}
        result = _add_harness_hooks(settings, HOOK_COMMAND, context_command=CONTEXT_COMMAND)
        assert _has_harness_hooks(result)
        hooks = result["hooks"]
        assert "SessionStart" in hooks
        assert "PostToolUse" in hooks
        assert "Stop" in hooks

    def test_add_without_context_command(self) -> None:
        settings: dict[str, object] = {}
        result = _add_harness_hooks(settings, HOOK_COMMAND)
        assert _has_harness_hooks(result)
        hooks = result["hooks"]
        assert "SessionStart" not in hooks
        assert "PostToolUse" in hooks
        assert "Stop" in hooks

    def test_add_preserves_existing_hooks(self) -> None:
        settings: dict[str, object] = {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Write", "hooks": [{"type": "command", "command": "other-tool"}]},
                ],
            },
        }
        result = _add_harness_hooks(settings, HOOK_COMMAND, context_command=CONTEXT_COMMAND)
        post = result["hooks"]["PostToolUse"]
        assert len(post) == 2  # existing + harness

    def test_has_harness_hooks_false_on_empty(self) -> None:
        assert _has_harness_hooks({}) is False

    def test_has_harness_hooks_false_on_other_hooks(self) -> None:
        settings = {
            "hooks": {
                "PostToolUse": [
                    {"hooks": [{"type": "command", "command": "other-tool"}]},
                ],
            },
        }
        assert _has_harness_hooks(settings) is False

    def test_remove_only_harness_hooks(self) -> None:
        settings: dict[str, object] = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": CONTEXT_COMMAND}]},
                ],
                "PostToolUse": [
                    {"matcher": "Write", "hooks": [{"type": "command", "command": "other-tool"}]},
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": HOOK_COMMAND}]},
                ],
                "Stop": [
                    {"hooks": [{"type": "command", "command": HOOK_COMMAND}]},
                ],
            },
        }
        result = _remove_harness_hooks(settings)
        assert not _has_harness_hooks(result)
        # other-tool should remain
        post = result["hooks"]["PostToolUse"]
        assert len(post) == 1
        assert post[0]["hooks"][0]["command"] == "other-tool"
        # SessionStart and Stop should be removed entirely (were only harness)
        assert "SessionStart" not in result["hooks"]
        assert "Stop" not in result["hooks"]

    def test_remove_cleans_up_empty_hooks(self) -> None:
        settings: dict[str, object] = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": CONTEXT_COMMAND}]},
                ],
                "PostToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": HOOK_COMMAND}]},
                ],
                "Stop": [
                    {"hooks": [{"type": "command", "command": HOOK_COMMAND}]},
                ],
            },
        }
        result = _remove_harness_hooks(settings)
        assert "hooks" not in result


# ---------------------------------------------------------------------------
# install_main
# ---------------------------------------------------------------------------


class TestInstallMain:
    def test_install_creates_settings(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        install_main([])
        out = capsys.readouterr().out
        assert "Entropy tracking installed" in out
        assert "harness entropy seed" in out

        settings_file = project / ".claude" / "settings.local.json"
        assert settings_file.exists()
        data = json.loads(settings_file.read_text())
        assert _has_harness_hooks(data)

    def test_install_creates_claude_directory(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)
        assert not (project / ".claude").exists()

        install_main([])
        assert (project / ".claude").exists()

    def test_install_idempotent(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        install_main([])
        capsys.readouterr()
        install_main([])
        out = capsys.readouterr().out
        assert "already configured" in out

        # Verify no duplicate entries
        settings_file = project / ".claude" / "settings.local.json"
        data = json.loads(settings_file.read_text())
        assert len(data["hooks"]["SessionStart"]) == 1
        assert len(data["hooks"]["PostToolUse"]) == 1
        assert len(data["hooks"]["Stop"]) == 1

    def test_install_preserves_existing_hooks(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        claude_dir = project / ".claude"
        claude_dir.mkdir()
        existing = {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Write", "hooks": [{"type": "command", "command": "other"}]},
                ],
            },
        }
        (claude_dir / "settings.local.json").write_text(json.dumps(existing))

        install_main([])
        data = json.loads((claude_dir / "settings.local.json").read_text())
        assert len(data["hooks"]["PostToolUse"]) == 2

    def test_install_project_flag(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        install_main(["--project"])
        settings_file = project / ".claude" / "settings.json"
        assert settings_file.exists()
        assert not (project / ".claude" / "settings.local.json").exists()

    def test_install_fails_without_harness_on_path(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch, path=None)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        with pytest.raises(SystemExit) as exc_info:
            install_main([])
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "'harness' not found in PATH" in err

    def test_install_fails_on_malformed_json(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        claude_dir = project / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text("{bad json")

        with pytest.raises(SystemExit) as exc_info:
            install_main([])
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Could not parse" in err

    def test_install_with_project_root_override(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        install_main(["--project-root", str(project)])
        out = capsys.readouterr().out
        assert "Entropy tracking installed" in out


# ---------------------------------------------------------------------------
# uninstall_main
# ---------------------------------------------------------------------------


class TestUninstallMain:
    def test_uninstall_removes_hooks(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        # First install
        install_main([])
        capsys.readouterr()

        # Then uninstall
        uninstall_main([])
        out = capsys.readouterr().out
        assert "Entropy tracking removed" in out

        settings_file = project / ".claude" / "settings.local.json"
        data = json.loads(settings_file.read_text())
        assert not _has_harness_hooks(data)

    def test_uninstall_preserves_other_hooks(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        claude_dir = project / ".claude"
        claude_dir.mkdir()
        mixed = {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Write", "hooks": [{"type": "command", "command": "other"}]},
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": HOOK_COMMAND}]},
                ],
                "Stop": [
                    {"hooks": [{"type": "command", "command": HOOK_COMMAND}]},
                ],
            },
        }
        (claude_dir / "settings.local.json").write_text(json.dumps(mixed))

        uninstall_main([])
        data = json.loads((claude_dir / "settings.local.json").read_text())
        assert len(data["hooks"]["PostToolUse"]) == 1
        assert data["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "other"

    def test_uninstall_noop_when_no_hooks(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        uninstall_main([])
        out = capsys.readouterr().out
        assert "No harness hooks found" in out

    def test_uninstall_noop_when_no_settings_file(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        uninstall_main([])
        out = capsys.readouterr().out
        assert "No harness hooks found" in out


# ---------------------------------------------------------------------------
# install_global (library function)
# ---------------------------------------------------------------------------


class TestInstallGlobal:
    def test_installs_session_start(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from harness.cli.install import install_global

        global_path = tmp_path / "settings.json"
        monkeypatch.setattr("harness.cli.install.GLOBAL_SETTINGS_PATH", global_path)

        result = install_global(HOOK_COMMAND)
        assert result is True
        data = json.loads(global_path.read_text())
        assert "SessionStart" in data["hooks"]
        assert _has_harness_hooks(data)

    def test_idempotent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from harness.cli.install import install_global

        global_path = tmp_path / "settings.json"
        monkeypatch.setattr("harness.cli.install.GLOBAL_SETTINGS_PATH", global_path)

        install_global(HOOK_COMMAND)
        result = install_global(HOOK_COMMAND)
        assert result is False

        data = json.loads(global_path.read_text())
        assert len(data["hooks"]["SessionStart"]) == 1

    def test_preserves_existing_hooks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from harness.cli.install import install_global

        global_path = tmp_path / "settings.json"
        global_path.write_text(
            json.dumps({
                "hooks": {
                    "PostToolUse": [
                        {"hooks": [{"type": "command", "command": "other"}]},
                    ],
                },
            })
        )
        monkeypatch.setattr("harness.cli.install.GLOBAL_SETTINGS_PATH", global_path)

        install_global(HOOK_COMMAND)
        data = json.loads(global_path.read_text())
        assert len(data["hooks"]["PostToolUse"]) == 1
        assert "SessionStart" in data["hooks"]


# ---------------------------------------------------------------------------
# install_project (library function)
# ---------------------------------------------------------------------------


class TestInstallProject:
    def test_installs_per_project_hooks(self, project: Path) -> None:
        from harness.cli.install import install_project

        path = install_project(project, HOOK_COMMAND)
        assert path.exists()
        data = json.loads(path.read_text())
        assert "PostToolUse" in data["hooks"]
        assert "Stop" in data["hooks"]
        # Per-project should NOT have SessionStart
        assert "SessionStart" not in data["hooks"]

    def test_idempotent(self, project: Path) -> None:
        from harness.cli.install import install_project

        install_project(project, HOOK_COMMAND)
        path = install_project(project, HOOK_COMMAND)
        data = json.loads(path.read_text())
        assert len(data["hooks"]["PostToolUse"]) == 1


# ---------------------------------------------------------------------------
# global_install_main
# ---------------------------------------------------------------------------


class TestGlobalInstallMain:
    def test_full_flow(
        self,
        project: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.install import global_install_main

        global_path = tmp_path / "global_settings.json"
        monkeypatch.setattr("harness.cli.install.GLOBAL_SETTINGS_PATH", global_path)
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)
        # Create a Python file so seed works
        (project / "app.py").write_text("x = 1\n")
        monkeypatch.setattr(
            "harness.cli.seed._resolve_commit_hash",
            lambda commit, cwd=None: "abc123",
        )

        global_install_main([])
        out = capsys.readouterr().out
        assert "Global hooks installed" in out
        assert "Harness installed" in out

    def test_skip_seed_flag(
        self,
        project: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.install import global_install_main

        global_path = tmp_path / "global_settings.json"
        monkeypatch.setattr("harness.cli.install.GLOBAL_SETTINGS_PATH", global_path)
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        global_install_main(["--skip-seed"])
        out = capsys.readouterr().out
        assert "Seeded" not in out

    def test_skip_global_flag(
        self,
        project: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.install import global_install_main

        global_path = tmp_path / "global_settings.json"
        monkeypatch.setattr("harness.cli.install.GLOBAL_SETTINGS_PATH", global_path)
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        global_install_main(["--skip-global", "--skip-seed"])
        assert not global_path.exists()


# ---------------------------------------------------------------------------
# global_uninstall_main
# ---------------------------------------------------------------------------


class TestGlobalUninstallMain:
    def test_removes_all_hooks(
        self,
        project: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.install import global_install_main, global_uninstall_main

        global_path = tmp_path / "global_settings.json"
        monkeypatch.setattr("harness.cli.install.GLOBAL_SETTINGS_PATH", global_path)
        _mock_which(monkeypatch)
        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)

        global_install_main(["--skip-seed"])
        capsys.readouterr()

        global_uninstall_main([])
        out = capsys.readouterr().out
        assert "Removed global hooks" in out
        assert "Removed per-project hooks" in out

    def test_noop_when_no_hooks(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from harness.cli.install import global_uninstall_main

        monkeypatch.setattr("harness.cli.install.find_project_root", lambda: project)
        monkeypatch.setattr(
            "harness.cli.install.GLOBAL_SETTINGS_PATH",
            project / "nonexistent.json",
        )

        global_uninstall_main([])
        out = capsys.readouterr().out
        assert "No harness hooks found" in out
