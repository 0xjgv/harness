"""harness context run — execute bundled context.sh to gather codebase context."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "context.sh"


def run_context_script(args: list[str] | None = None) -> int:
    """Run the bundled context.sh, forwarding arguments. Returns exit code.

    Raises FileNotFoundError if the script is missing.
    """
    if not SCRIPT_PATH.exists():
        msg = f"context.sh not found at {SCRIPT_PATH}"
        raise FileNotFoundError(msg)

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), *(args or [])],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode


def run_main(argv: list[str] | None = None) -> None:
    """Locate and run the bundled context.sh, forwarding all arguments."""
    args = argv if argv is not None else sys.argv[1:]
    try:
        code = run_context_script(args)
    except FileNotFoundError:
        print(f"Error: context.sh not found at {SCRIPT_PATH}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code)
