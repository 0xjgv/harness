"""Install/uninstall Claude Code hooks for entropy tracking."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from harness.config import find_project_root

HARNESS_HOOK_MARKER = "harness"


def _find_harness_command() -> str | None:
    """Resolve the harness binary path via shutil.which()."""
    return shutil.which("harness")


def _find_hook_command() -> str | None:
    """Resolve the harness-hook-run binary path via shutil.which()."""
    return shutil.which("harness-hook-run")


def _read_settings(path: Path) -> dict[str, Any]:
    """Read and parse a settings JSON file. Returns {} if file doesn't exist or is empty."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return dict(json.loads(text))


def _write_settings(path: Path, data: dict[str, Any]) -> None:
    """Atomic write: tempfile in same dir + os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        Path(tmp).replace(path)
    except BaseException:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise


def _settings_path(project_root: Path, project_wide: bool) -> Path:
    """Return .claude/settings.json or .claude/settings.local.json."""
    name = "settings.json" if project_wide else "settings.local.json"
    return project_root / ".claude" / name


def _harness_hook_entry(command: str) -> dict[str, Any]:
    """Build a single hook handler dict."""
    return {"type": "command", "command": command}


def _is_harness_hook(hook: dict[str, Any]) -> bool:
    """Check if a hook entry belongs to harness."""
    cmd = hook.get("command", "")
    return isinstance(cmd, str) and HARNESS_HOOK_MARKER in cmd


def _has_harness_hooks(settings: dict[str, Any]) -> bool:
    """Check if settings already contain harness hook entries."""
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    for _event, handlers in hooks.items():
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            if not isinstance(handler, dict):
                continue
            inner_hooks = handler.get("hooks", [])
            if not isinstance(inner_hooks, list):
                continue
            for hook in inner_hooks:
                if isinstance(hook, dict) and _is_harness_hook(hook):
                    return True
    return False


def _add_harness_hooks(settings: dict[str, Any], command: str) -> dict[str, Any]:
    """Merge harness PostToolUse and Stop hooks into settings, preserving existing hooks."""
    hooks = settings.setdefault("hooks", {})

    # PostToolUse: with Bash matcher
    post_tool_handlers: list[dict[str, Any]] = hooks.setdefault("PostToolUse", [])
    post_tool_handlers.append({
        "matcher": "Bash",
        "hooks": [_harness_hook_entry(command)],
    })

    # Stop: no matcher
    stop_handlers: list[dict[str, Any]] = hooks.setdefault("Stop", [])
    stop_handlers.append({
        "hooks": [_harness_hook_entry(command)],
    })

    return settings


def _remove_harness_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    """Remove all harness hook entries, clean up empty arrays/objects."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings

    events_to_delete: list[str] = []
    for event, handlers in hooks.items():
        if not isinstance(handlers, list):
            continue
        # Filter out handler groups that only contain harness hooks
        remaining: list[dict[str, Any]] = []
        for handler in handlers:
            if not isinstance(handler, dict):
                remaining.append(handler)
                continue
            inner_hooks = handler.get("hooks", [])
            if not isinstance(inner_hooks, list):
                remaining.append(handler)
                continue
            filtered = [
                h for h in inner_hooks if not (isinstance(h, dict) and _is_harness_hook(h))
            ]
            if filtered:
                handler["hooks"] = filtered
                remaining.append(handler)
            # else: all hooks in this group were harness — drop the entire group
        if remaining:
            hooks[event] = remaining
        else:
            events_to_delete.append(event)

    for event in events_to_delete:
        del hooks[event]

    if not hooks:
        del settings["hooks"]

    return settings


def _build_install_parser() -> argparse.ArgumentParser:
    """Build argparse parser for install subcommand."""
    parser = argparse.ArgumentParser(
        prog="harness entropy install",
        description="Install Claude Code hooks for entropy tracking.",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Override project root detection",
    )
    parser.add_argument(
        "--project",
        action="store_true",
        help="Write to .claude/settings.json (team-wide) instead of settings.local.json",
    )
    return parser


def _build_uninstall_parser() -> argparse.ArgumentParser:
    """Build argparse parser for uninstall subcommand."""
    parser = argparse.ArgumentParser(
        prog="harness entropy uninstall",
        description="Remove Claude Code hooks for entropy tracking.",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Override project root detection",
    )
    parser.add_argument(
        "--project",
        action="store_true",
        help="Target .claude/settings.json instead of settings.local.json",
    )
    return parser


def install_main(argv: list[str] | None = None) -> None:
    """Install Claude Code hooks for entropy tracking."""
    parser = _build_install_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve() if args.project_root else find_project_root()
    harness_cmd = _find_harness_command()
    if harness_cmd is None:
        print(
            "Error: 'harness' not found in PATH. Install globally with: uv tool install harness",
            file=sys.stderr,
        )
        sys.exit(1)

    hook_command = _find_hook_command() or f"{harness_cmd} entropy hook-run"
    settings_file = _settings_path(project_root, project_wide=args.project)

    try:
        settings = _read_settings(settings_file)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"Error: Could not parse {settings_file}: {exc}. Fix the file manually or remove it.",
            file=sys.stderr,
        )
        sys.exit(1)

    if _has_harness_hooks(settings):
        print("Harness hooks already configured.")
        return

    _add_harness_hooks(settings, hook_command)
    _write_settings(settings_file, settings)

    rel_settings = settings_file.relative_to(project_root)
    print("Entropy tracking installed.\n")
    print(f"  Settings: {rel_settings}")
    print("  Events:   PostToolUse (Bash), Stop")
    print(f"  Command:  {hook_command}")
    print("\nEntropy will be measured automatically during Claude Code sessions.")
    if not args.project:
        print("To share with your team, re-run with: harness entropy install --project")


def uninstall_main(argv: list[str] | None = None) -> None:
    """Remove Claude Code hooks for entropy tracking."""
    parser = _build_uninstall_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve() if args.project_root else find_project_root()
    settings_file = _settings_path(project_root, project_wide=args.project)

    try:
        settings = _read_settings(settings_file)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"Error: Could not parse {settings_file}: {exc}. Fix the file manually or remove it.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _has_harness_hooks(settings):
        print("No harness hooks found.")
        return

    _remove_harness_hooks(settings)
    _write_settings(settings_file, settings)

    rel_settings = settings_file.relative_to(project_root)
    print("Entropy tracking removed.\n")
    print(f"  Settings: {rel_settings}")
    print("  Removed:  PostToolUse (Bash), Stop hooks")
