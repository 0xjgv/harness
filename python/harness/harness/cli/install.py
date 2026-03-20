"""Install/uninstall Claude Code hooks for entropy tracking."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from harness.cli.utils import atomic_write_json
from harness.config import find_project_root

HARNESS_HOOK_MARKER = "harness"
GLOBAL_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


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
    """Atomic write of settings JSON."""
    atomic_write_json(path, data)


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


def _add_harness_hooks(
    settings: dict[str, Any],
    command: str,
    *,
    context_command: str | None = None,
) -> dict[str, Any]:
    """Merge harness hooks into settings, preserving existing hooks.

    When context_command is provided, a SessionStart hook is also added.
    """
    hooks = settings.setdefault("hooks", {})

    if context_command:
        session_handlers: list[dict[str, Any]] = hooks.setdefault("SessionStart", [])
        session_handlers.append({
            "hooks": [_harness_hook_entry(context_command)],
        })

    post_tool_handlers: list[dict[str, Any]] = hooks.setdefault("PostToolUse", [])
    post_tool_handlers.append({
        "matcher": "Bash",
        "hooks": [_harness_hook_entry(command)],
    })

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
        if remaining:
            hooks[event] = remaining
        else:
            events_to_delete.append(event)

    for event in events_to_delete:
        del hooks[event]

    if not hooks:
        del settings["hooks"]

    return settings


def _add_global_session_hook(settings: dict[str, Any], command: str) -> dict[str, Any]:
    """Add SessionStart hook only. For global settings."""
    hooks = settings.setdefault("hooks", {})

    session_handlers: list[dict[str, Any]] = hooks.setdefault("SessionStart", [])
    session_handlers.append({
        "hooks": [_harness_hook_entry(command)],
    })

    return settings


def install_global(hook_command: str) -> bool:
    """Install SessionStart hook to ~/.claude/settings.json. Returns True if installed."""
    try:
        settings = _read_settings(GLOBAL_SETTINGS_PATH)
    except (json.JSONDecodeError, ValueError):
        return False

    if _has_harness_hooks(settings):
        return False

    _add_global_session_hook(settings, hook_command)
    _write_settings(GLOBAL_SETTINGS_PATH, settings)
    return True


def install_project(
    project_root: Path,
    hook_command: str,
    *,
    project_wide: bool = False,
) -> Path:
    """Install PostToolUse + Stop hooks to per-project settings. Returns settings path."""
    settings_file = _settings_path(project_root, project_wide=project_wide)
    settings = _read_settings(settings_file)

    if _has_harness_hooks(settings):
        return settings_file

    _add_harness_hooks(settings, hook_command)
    _write_settings(settings_file, settings)
    return settings_file


def _resolve_project_root(override: str | None) -> Path:
    """Resolve project root from an optional --project-root CLI override."""
    if override:
        return Path(override).resolve()
    return find_project_root()


def _build_base_parser(prog: str, description: str) -> argparse.ArgumentParser:
    """Build an argparse parser with common --project-root and --project flags."""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Override project root detection",
    )
    parser.add_argument(
        "--project",
        action="store_true",
        help="Target .claude/settings.json (team-wide) instead of settings.local.json",
    )
    return parser


def _require_harness_on_path() -> str:
    """Return the harness command path or exit with an error."""
    harness_cmd = _find_harness_command()
    if harness_cmd is None:
        print(
            "Error: 'harness' not found in PATH. Install globally with: uv tool install harness",
            file=sys.stderr,
        )
        sys.exit(1)
    return harness_cmd


def _resolve_hook_command(harness_cmd: str) -> str:
    """Return the hook-run command: dedicated binary if available, else fallback."""
    return _find_hook_command() or f"{harness_cmd} entropy hook-run"


def _read_settings_or_exit(settings_file: Path) -> dict[str, Any]:
    """Read settings, exiting on parse errors."""
    try:
        return _read_settings(settings_file)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"Error: Could not parse {settings_file}: {exc}. Fix the file manually or remove it.",
            file=sys.stderr,
        )
        sys.exit(1)


def install_main(argv: list[str] | None = None) -> None:
    """Install Claude Code hooks for entropy tracking."""
    parser = _build_base_parser(
        "harness entropy install",
        "Install Claude Code hooks for entropy tracking.",
    )
    args = parser.parse_args(argv)

    project_root = _resolve_project_root(args.project_root)
    harness_cmd = _require_harness_on_path()
    hook_command = _resolve_hook_command(harness_cmd)
    settings_file = _settings_path(project_root, project_wide=args.project)
    settings = _read_settings_or_exit(settings_file)

    if _has_harness_hooks(settings):
        print("Harness hooks already configured.")
        return

    context_command = f"{harness_cmd} context run"
    _add_harness_hooks(settings, hook_command, context_command=context_command)
    _write_settings(settings_file, settings)

    rel_settings = settings_file.relative_to(project_root)
    print("Entropy tracking installed.\n")
    print(f"  Settings: {rel_settings}")
    print("  Events:   SessionStart, PostToolUse (Bash), Stop")
    print(f"  Command:  {hook_command}")
    print("\nEntropy will be measured automatically during Claude Code sessions.")
    if not args.project:
        print("To share with your team, re-run with: harness entropy install --project")
    print("\nTo establish a baseline for accurate delta tracking:")
    print("  harness entropy seed")


def uninstall_main(argv: list[str] | None = None) -> None:
    """Remove Claude Code hooks for entropy tracking."""
    parser = _build_base_parser(
        "harness entropy uninstall",
        "Remove Claude Code hooks for entropy tracking.",
    )
    args = parser.parse_args(argv)

    project_root = _resolve_project_root(args.project_root)
    settings_file = _settings_path(project_root, project_wide=args.project)
    settings = _read_settings_or_exit(settings_file)

    if not _has_harness_hooks(settings):
        print("No harness hooks found.")
        return

    _remove_harness_hooks(settings)
    _write_settings(settings_file, settings)

    rel_settings = settings_file.relative_to(project_root)
    print("Entropy tracking removed.\n")
    print(f"  Settings: {rel_settings}")
    print("  Removed:  SessionStart, PostToolUse (Bash), Stop hooks")


# ---------------------------------------------------------------------------
# Global install / uninstall (harness install / harness uninstall)
# ---------------------------------------------------------------------------


def global_install_main(argv: list[str] | None = None) -> None:
    """Install global SessionStart hook + per-project hooks + seed."""
    parser = _build_base_parser(
        "harness install",
        "Install harness hooks globally and in the current project.",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Skip automatic baseline seeding",
    )
    parser.add_argument(
        "--skip-global",
        action="store_true",
        help="Skip global SessionStart hook installation",
    )
    args = parser.parse_args(argv)

    harness_cmd = _require_harness_on_path()
    hook_command = _resolve_hook_command(harness_cmd)
    project_root = _resolve_project_root(args.project_root)

    # 1. Global hooks
    if not args.skip_global:
        if install_global(hook_command):
            print(f"Global hooks installed: {GLOBAL_SETTINGS_PATH}")
        else:
            print(f"Global hooks already configured: {GLOBAL_SETTINGS_PATH}")

    # 2. Per-project hooks
    settings_file = install_project(
        project_root,
        hook_command,
        project_wide=args.project,
    )
    rel = settings_file.relative_to(project_root)
    if _has_harness_hooks(_read_settings(settings_file)):
        print(f"Per-project hooks: {rel}")

    # 3. Seed
    if not args.skip_seed:
        try:
            from harness.cli.seed import seed_project  # noqa: PLC0415

            summary = seed_project(project_root, quiet=True)
            print(
                f"Seeded {summary.files_measured} file(s), "
                f"avg EI: {summary.avg_entropy_index:.1f}",
            )
        except FileNotFoundError:
            print("No Python files found — skipping seed.")
        except Exception as exc:
            print(f"Seed failed: {exc}", file=sys.stderr)

    print("\nHarness installed. Entropy tracking is active.")


def global_uninstall_main(argv: list[str] | None = None) -> None:
    """Remove global and per-project harness hooks."""
    parser = _build_base_parser(
        "harness uninstall",
        "Remove harness hooks globally and from the current project.",
    )
    parser.add_argument(
        "--global-only",
        action="store_true",
        help="Only remove global hooks, leave per-project hooks",
    )
    args = parser.parse_args(argv)

    removed_any = False
    project_root = _resolve_project_root(args.project_root)

    # Remove global hooks
    try:
        global_settings = _read_settings(GLOBAL_SETTINGS_PATH)
        if _has_harness_hooks(global_settings):
            _remove_harness_hooks(global_settings)
            _write_settings(GLOBAL_SETTINGS_PATH, global_settings)
            print(f"Removed global hooks: {GLOBAL_SETTINGS_PATH}")
            removed_any = True
    except (json.JSONDecodeError, ValueError, OSError):
        pass

    # Remove per-project hooks
    if not args.global_only:
        settings_file = _settings_path(project_root, project_wide=args.project)
        try:
            settings = _read_settings(settings_file)
            if _has_harness_hooks(settings):
                _remove_harness_hooks(settings)
                _write_settings(settings_file, settings)
                rel = settings_file.relative_to(project_root)
                print(f"Removed per-project hooks: {rel}")
                removed_any = True
        except (json.JSONDecodeError, ValueError, OSError):
            pass

    if removed_any:
        print("\nHarness hooks removed.")
    else:
        print("No harness hooks found.")
