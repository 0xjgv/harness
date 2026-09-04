"""pytest-only shim so mutmut can mutation-test a package literally named `src`.

mutmut 3 hard-codes `src/` as a *layout directory*, never a package: it strips
`src.` from every mutant key (`src/core/pricing.py` → `core.pricing.x_f__mutmut_1`),
rejects trampoline hits from a module whose `__module__` starts with `src.`, and
activates a mutant only when the function's `__module__` equals the key's module.
This template's package *is* `src`, so under mutmut a test's
`from src.core.pricing import ...` must resolve to the module `core.pricing` —
importable because mutmut puts `mutants/src` on `sys.path` for exactly that layout.
This finder aliases `src.<name>` to `<name>` so the two agree.

Inert outside a mutmut run: only the copy mutmut makes under `mutants/tests/` has
`mutants` as its grandparent. unittest never loads this file at all. A repo whose
package is not called `src` can delete it.
"""

import importlib
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

_LAYOUT_PREFIX = "src."


class _AliasLoader(Loader):
    """Hand back the already-imported target module instead of executing a copy."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.original_spec: ModuleSpec | None = None

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        module = importlib.import_module(self.target)
        self.original_spec = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        # The import machinery stamped the alias spec on the shared module object
        # in between; put the target's own spec back so `reload` and friends work.
        module.__spec__ = self.original_spec


class _SrcAliasFinder(MetaPathFinder):
    def find_spec(
        self, fullname: str, path: object = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(_LAYOUT_PREFIX):
            return None
        return ModuleSpec(fullname, _AliasLoader(fullname[len(_LAYOUT_PREFIX) :]))


if Path(__file__).resolve().parent.parent.name == "mutants":
    sys.meta_path.insert(0, _SrcAliasFinder())
