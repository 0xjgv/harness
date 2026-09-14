import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from behave import given, when

HARNESS = Path(__file__).resolve().parents[3] / "harness.py"
GIT = shutil.which("git") or "git"

GIT_IDENTITY = [
    "-c",
    "user.email=harness@example.com",
    "-c",
    "user.name=Harness",
    "-c",
    "commit.gpgsign=false",
]


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@given('a git repository on branch "{branch}"')
def step_repo_on_branch(context, branch):
    context.tmp = Path(tempfile.mkdtemp(prefix="branch-guard-"))
    _git(context.tmp, "init", "-q")
    _git(context.tmp, *GIT_IDENTITY, "commit", "--allow-empty", "-q", "-m", "init")
    _git(context.tmp, "branch", "-M", branch)


def _run_branch_guard(context, **env_extra):
    env = {**os.environ, **env_extra}
    result = subprocess.run(
        [sys.executable, str(HARNESS), "branch-guard"],
        cwd=str(context.tmp),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    context.result = result
    context.output = result.stdout + result.stderr


@when("I run the branch guard with no push refs")
def step_run_branch_guard(context):
    # DEVNULL stands in for a tty: no pre-push refs, so the current branch decides.
    _run_branch_guard(context)


@when('I run the branch guard with push refs "{refs}"')
def step_run_branch_guard_with_refs(context, refs):
    # The refs a git pre-push hook would hand over, minus the stdin plumbing.
    _run_branch_guard(context, HARNESS_PRE_PUSH_REFS=refs)
