import tempfile
from pathlib import Path

from behave import given, then, when

from src.adapters.formatting import render_receipt
from src.core.pricing import Item


@given("an order of 2 widgets at $2.00 and 1 gadget at $5.00")
def step_build_order(context):
    context.items = [
        Item(name="widget", unit_price=2.0, quantity=2),
        Item(name="gadget", unit_price=5.0, quantity=1),
    ]


@when("I render the receipt with a 10 percent discount")
def step_render_receipt(context):
    context.receipt = render_receipt(context.items, discount_percent=10)


@then('the receipt shows a total of "{total}"')
def step_check_total(context, total):
    assert f"Total: {total}" in context.receipt, (
        f"expected 'Total: {total}' in receipt:\n{context.receipt}"
    )


# --- `harness arch` ratchet steps --------------------------------------------
#
# These build a throwaway project rather than pointing at the template's own
# `.importlinter`: the scenarios need a *fixed* violation count, and the
# template's own tree is (and must stay) clean. `_tool` resolves a bare
# `.venv/bin/<tool>` when there is no `pyproject.toml`, so a shell stub standing
# in for lint-imports lets a scenario pin the exact output the parser has to
# survive, with no import-linter, no venv and no network. The generic
# `When I run ...` / `Then ...` steps live in crap_steps.py; behave loads every
# module under steps/ into one registry.

BASELINE = ".harness-baseline"

IMPORTLINTER = """\
[importlinter]
root_packages =
    src

[importlinter:contract:layers]
name = src package layers
type = layers
containers =
    src
layers =
    adapters
    core
"""

# The `Contracts:` summary line is what tells a real count apart from a run that
# never analysed anything — import-linter exits 1 for both.
BROKEN_HEADER = """\
Contracts: 0 kept, 1 broken.


----------------
Broken contracts
----------------

src package layers
------------------

src.core is not allowed to import src.adapters:

"""


def _lint_imports_stub(context, payload: str, *, exit_code: int) -> None:
    venv_bin = context.tmp / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub = venv_bin / "lint-imports"
    stub.write_text(f"#!/bin/sh\ncat <<'OUT'\n{payload}OUT\nexit {exit_code}\n")
    stub.chmod(0o755)


def _arch_project(context, violations: int) -> None:
    context.tmp = Path(tempfile.mkdtemp(prefix="arch-"))
    (context.tmp / ".importlinter").write_text(IMPORTLINTER)
    chains = "".join(
        f"- src.core.mod{i} -> src.adapters.formatting (l.{i + 1})\n" for i in range(violations)
    )
    _lint_imports_stub(context, BROKEN_HEADER + chains, exit_code=1)


@given("a project with an .importlinter reporting {count:d} broken imports and no baseline")
def step_arch_project_no_baseline(context, count):
    _arch_project(context, count)


@given(
    "a project with an .importlinter reporting {count:d} broken imports "
    'and a baseline line "{line}"'
)
def step_arch_project_with_baseline(context, count, line):
    _arch_project(context, count)
    (context.tmp / BASELINE).write_text(f"{line}\n")


@given("a project with no .importlinter")
def step_arch_project_without_config(context):
    context.tmp = Path(tempfile.mkdtemp(prefix="arch-"))


@given("a project with an .importlinter whose tool cannot run")
def step_arch_project_with_broken_tool(context):
    context.tmp = Path(tempfile.mkdtemp(prefix="arch-"))
    (context.tmp / ".importlinter").write_text(IMPORTLINTER)
    _lint_imports_stub(context, "ModuleNotFoundError: no root package named src\n", exit_code=1)
