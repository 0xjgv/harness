"""harness context run — execute bundled context.sh to gather codebase context."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "context.sh"


def run_main(argv: list[str] | None = None) -> None:
    """Locate and run the bundled context.sh, forwarding all arguments."""
    args = argv if argv is not None else sys.argv[1:]

    if not SCRIPT_PATH.exists():
        print(f"Error: context.sh not found at {SCRIPT_PATH}", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    sys.exit(result.returncode)
