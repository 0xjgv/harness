"""Top-level CLI router for harness."""

from __future__ import annotations

import argparse
import sys

# Lazy-import dispatch tables: each value is (module_path, function_name).
# Resolved at dispatch time to avoid importing all subcommands eagerly.
_ENTROPY_COMMANDS: dict[str, tuple[str, str]] = {
    "measure": ("harness.cli.measure", "main"),
    "report": ("harness.cli.report", "main"),
    "install": ("harness.cli.install", "install_main"),
    "uninstall": ("harness.cli.install", "uninstall_main"),
    "seed": ("harness.cli.seed", "seed_main"),
    "hook-run": ("harness.cli.hook", "hook_run_main"),
}

_CONTEXT_COMMANDS: dict[str, tuple[str, str]] = {
    "run": ("harness.cli.context", "run_main"),
}

_TOP_LEVEL_COMMANDS: dict[str, tuple[str, str]] = {
    "install": ("harness.cli.install", "global_install_main"),
    "uninstall": ("harness.cli.install", "global_uninstall_main"),
}


def _lazy_dispatch(
    table: dict[str, tuple[str, str]],
    command: str,
    argv: list[str],
) -> None:
    """Import and call the handler for `command` from `table`."""
    import importlib  # noqa: PLC0415

    module_path, func_name = table[command]
    module = importlib.import_module(module_path)
    handler = getattr(module, func_name)
    handler(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for `harness` command."""
    args_list = argv if argv is not None else sys.argv[1:]

    # hook-run is internal (invoked by Claude Code hooks) -- intercept before
    # argparse so it never appears in help/choices.
    if len(args_list) >= 2 and args_list[0] == "entropy" and args_list[1] == "hook-run":
        _lazy_dispatch(_ENTROPY_COMMANDS, "hook-run", args_list[2:])
        return

    parser = argparse.ArgumentParser(
        prog="harness",
        description="Code complexity metrics engine.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # harness entropy ...
    entropy_parser = subparsers.add_parser(
        "entropy",
        help="Entropy index measurement and tracking",
    )
    entropy_sub = entropy_parser.add_subparsers(dest="entropy_command")

    for name, help_text in [
        ("measure", "Measure entropy index for files"),
        ("report", "Show entropy trends and hotspots"),
        ("install", "Install Claude Code hooks for entropy tracking"),
        ("uninstall", "Remove Claude Code hooks"),
        ("seed", "Establish baseline entropy measurements for the project"),
    ]:
        entropy_sub.add_parser(name, help=help_text, add_help=False)

    # harness install / uninstall
    subparsers.add_parser(
        "install",
        help="Install harness hooks (global + per-project + seed)",
        add_help=False,
    )
    subparsers.add_parser(
        "uninstall",
        help="Remove harness hooks (global + per-project)",
        add_help=False,
    )

    # harness context ...
    context_parser = subparsers.add_parser(
        "context",
        help="Codebase context generation",
    )
    context_sub = context_parser.add_subparsers(dest="context_command")
    context_sub.add_parser(
        "run",
        help="Run bundled context.sh to gather codebase context",
        add_help=False,
    )

    args, remaining = parser.parse_known_args(args_list)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command in _TOP_LEVEL_COMMANDS:
        _lazy_dispatch(_TOP_LEVEL_COMMANDS, args.command, remaining)
    elif args.command == "entropy":
        if args.entropy_command is None:
            entropy_parser.print_help()
            sys.exit(0)
        _lazy_dispatch(_ENTROPY_COMMANDS, args.entropy_command, remaining)
    elif args.command == "context":
        if args.context_command is None:
            context_parser.print_help()
            sys.exit(0)
        _lazy_dispatch(_CONTEXT_COMMANDS, args.context_command, remaining)
