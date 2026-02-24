"""Top-level CLI router for harness."""

from __future__ import annotations

import argparse
import sys


def _dispatch_entropy(command: str, argv: list[str]) -> None:
    """Dispatch to the appropriate entropy subcommand."""
    if command == "measure":
        from harness.cli.measure import main as measure_main  # noqa: PLC0415

        measure_main(argv)
    elif command == "report":
        from harness.cli.report import main as report_main  # noqa: PLC0415

        report_main(argv)
    elif command == "install":
        from harness.cli.install import install_main  # noqa: PLC0415

        install_main(argv)
    elif command == "uninstall":
        from harness.cli.install import uninstall_main  # noqa: PLC0415

        uninstall_main(argv)
    elif command == "seed":
        from harness.cli.seed import seed_main  # noqa: PLC0415

        seed_main(argv)
    elif command == "hook-run":
        from harness.cli.hook import hook_run_main  # noqa: PLC0415

        hook_run_main(argv)


def _dispatch_context(command: str, argv: list[str]) -> None:
    """Dispatch to the appropriate context subcommand."""
    if command == "run":
        from harness.cli.context import run_main  # noqa: PLC0415

        run_main(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for `harness` command."""
    args_list = argv if argv is not None else sys.argv[1:]

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

    # harness entropy measure
    entropy_sub.add_parser(
        "measure",
        help="Measure entropy index for files",
        add_help=False,
    )

    # harness entropy report
    entropy_sub.add_parser(
        "report",
        help="Show entropy trends and hotspots",
        add_help=False,
    )

    # harness entropy install
    entropy_sub.add_parser(
        "install",
        help="Install Claude Code hooks for entropy tracking",
        add_help=False,
    )

    # harness entropy uninstall
    entropy_sub.add_parser(
        "uninstall",
        help="Remove Claude Code hooks",
        add_help=False,
    )

    # harness entropy seed
    entropy_sub.add_parser(
        "seed",
        help="Establish baseline entropy measurements for the project",
        add_help=False,
    )

    # harness entropy hook-run (internal — invoked by Claude Code hooks)
    entropy_sub.add_parser(
        "hook-run",
        help=argparse.SUPPRESS,
        add_help=False,
    )

    # harness context ...
    context_parser = subparsers.add_parser(
        "context",
        help="Codebase context generation",
    )
    context_sub = context_parser.add_subparsers(dest="context_command")

    # harness context run
    context_sub.add_parser(
        "run",
        help="Run bundled context.sh to gather codebase context",
        add_help=False,
    )

    # Parse only the first 1-2 args to determine routing
    args, remaining = parser.parse_known_args(args_list)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "entropy":
        if args.entropy_command is None:
            entropy_parser.print_help()
            sys.exit(0)
        _dispatch_entropy(args.entropy_command, remaining)
    elif args.command == "context":
        if args.context_command is None:
            context_parser.print_help()
            sys.exit(0)
        _dispatch_context(args.context_command, remaining)
