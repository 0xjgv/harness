"""Pytest configuration.

The default test runner (unittest) ignores this file. It exists for pytest-based
runs — notably `harness mutation`, where mutmut copies `tests/` into a generated
`mutants/` subdir and runs pytest from there. From inside `mutants/`, the
top-level `harness` module is not importable, so `from harness import ...` in the
test suite fails with ModuleNotFoundError.

Walk up to the directory that contains `harness.py` and append it to sys.path
(append, not insert: a mutated `src/` under `mutants/` must keep precedence over
the project's own `src/`).
"""

import sys
from pathlib import Path

_dir = Path(__file__).resolve().parent
while _dir != _dir.parent:
    if (_dir / "harness.py").exists():
        sys.path.append(str(_dir))
        break
    _dir = _dir.parent
