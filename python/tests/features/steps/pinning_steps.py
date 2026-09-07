"""Given steps for pinning.feature.

Builds a project with no `.venv`, so `_tool` lands on its `uvx` tier, and puts a
`uvx` stub first on the subprocess PATH so the scenario observes the argv the
harness hands uvx — pinned or not — without a network or a real lizard.

The generic `When I run ...` / `Then ...` steps live in crap_steps.py; behave
loads every module under steps/ into one registry.
"""

import os
import tempfile
from pathlib import Path

from behave import given

UVX_STUB = '#!/bin/sh\necho "uvx-stub $*"\nexit 0\n'
LOCK = '[[package]]\nname = "lizard"\nversion = "1.22.2"\n'


def _make_project(context):
    context.tmp = Path(tempfile.mkdtemp(prefix="pinning-"))
    (context.tmp / "src").mkdir()
    (context.tmp / "src" / "app.py").write_text("VALUE = 1\n")
    stub_bin = context.tmp / "stub-bin"
    stub_bin.mkdir()
    stub = stub_bin / "uvx"
    stub.write_text(UVX_STUB)
    stub.chmod(0o755)
    context.env = {**os.environ, "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}"}


@given("a project with a uvx stub, no .venv, and a uv.lock pinning lizard to 1.22.2")
def step_project_with_lock(context):
    _make_project(context)
    (context.tmp / "uv.lock").write_text(LOCK)


@given("a project with a uvx stub, no .venv, and no uv.lock")
def step_project_without_lock(context):
    _make_project(context)
