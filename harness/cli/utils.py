"""Shared CLI utilities."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic JSON write: tempfile in same dir + os.replace().

    Creates parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        Path(tmp).replace(path)
    except BaseException:
        tmp_path = Path(tmp)
        if tmp_path.exists():
            tmp_path.unlink()
        raise
