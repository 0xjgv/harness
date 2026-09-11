"""Given steps for agent_loop.feature.

Builds a throwaway git repository whose only resolvable base ref is its local `main`,
so the delta gates have a real "before" image to diff against. Two of the three tools
the Stop hook runs are stubbed — ruff answers `--output-format=json` with an empty
findings list, vulture reports nothing — because neither is what these scenarios are
about. lizard is *not* stubbed: the whole point is whether the harness reads real
cyclomatic complexity on both sides of the diff, so the template's own installed
lizard is linked in.

The generic `When I run ...` / `Then ...` steps live in crap_steps.py; behave loads
every module under steps/ into one registry.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from behave import given

GIT = shutil.which("git") or "git"
TEMPLATE = Path(__file__).resolve().parents[3]

# Answers the one invocation the harness parses (`ruff check --output-format=json`)
# with "no findings", and exits 0 for the fix/format runs it does not read.
RUFF_STUB = "#!/bin/sh\ncase \"$*\" in\n  *--output-format=json*) echo '[]' ;;\nesac\nexit 0\n"
VULTURE_STUB = "#!/bin/sh\nexit 0\n"

SIMPLE = "def simple(x):\n    return x + 1\n"


def _complex_function(name, branches):
    """A function whose CCN is `branches + 1` — one per `if`, plus the entry path."""
    body = f"def {name}(x):\n"
    for value in range(1, branches + 1):
        body += f"    if x == {value}:\n        return {value}\n"
    return body + "    return 0\n"


def _git(context, *args):
    subprocess.run([GIT, *args], cwd=str(context.tmp), check=True, capture_output=True, text=True)


def _link_lizard(context):
    """Link the template's own lizard into the fixture's `.venv/bin`.

    `_tool` resolves `.venv/bin/<tool>` when there is no pyproject.toml, so the link
    is what keeps the scenario offline: without it the harness falls through to
    `uvx lizard`, which needs the network.
    """
    real = TEMPLATE / ".venv" / "bin" / "lizard"
    source = real if real.exists() else Path(shutil.which("lizard") or "")
    assert source.exists(), "lizard is not installed — run `uv sync` in the template first"
    (context.tmp / ".venv" / "bin" / "lizard").symlink_to(source)


def _make_git_project(context, committed):
    context.tmp = Path(tempfile.mkdtemp(prefix="agent-loop-"))
    venv_bin = context.tmp / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    for name, script in (("ruff", RUFF_STUB), ("vulture", VULTURE_STUB)):
        stub = venv_bin / name
        stub.write_text(script)
        stub.chmod(0o755)
    _link_lizard(context)

    (context.tmp / ".gitignore").write_text(".venv/\n")
    src = context.tmp / "src"
    src.mkdir()
    for name, body in committed.items():
        (src / name).write_text(body)

    _git(context, "init", "-b", "main")
    _git(context, "config", "user.email", "harness@example.invalid")
    _git(context, "config", "user.name", "Harness Test")
    _git(context, "add", "-A")
    _git(context, "commit", "-m", "base")


@given("a git project whose committed code is under every complexity limit")
def step_simple_base(context):
    _make_git_project(context, {"ok.py": SIMPLE})


@given("a git project whose committed code already has a function with CCN 16")
def step_complex_base(context):
    _make_git_project(context, {"legacy.py": _complex_function("legacy", 15)})


@given("the change adds a function with CCN 16 to src/")
def step_add_complex_function(context):
    (context.tmp / "src" / "worse.py").write_text(_complex_function("worse", 15))


@given("the change appends a comment to that file")
def step_append_comment(context):
    legacy = context.tmp / "src" / "legacy.py"
    legacy.write_text(legacy.read_text() + "# appended\n")
