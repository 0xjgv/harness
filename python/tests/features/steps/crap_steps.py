import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from behave import given, then, when

HARNESS = Path(__file__).resolve().parents[3] / "harness.py"

# Function with CCN ~9: lizard reports it on the synthetic src/stub.py.
STUB_PY = """\
def f(n):
    if n < 1:
        return 0
    if n < 2:
        return 1
    if n < 3:
        return 2
    if n < 4:
        return 3
    if n < 5:
        return 4
    if n < 6:
        return 5
    if n < 7:
        return 6
    if n < 8:
        return 7
    return 8
"""

# Cobertura XML with hits=0 for every line of the stub → 0% coverage.
COVERAGE_XML = """\
<?xml version="1.0" ?>
<coverage>
  <packages><package name="src"><classes>
    <class name="stub.py" filename="src/stub.py"><lines>
      <line number="1" hits="0"/>
      <line number="2" hits="0"/>
      <line number="3" hits="0"/>
      <line number="4" hits="0"/>
      <line number="5" hits="0"/>
      <line number="6" hits="0"/>
      <line number="7" hits="0"/>
      <line number="8" hits="0"/>
      <line number="9" hits="0"/>
      <line number="10" hits="0"/>
      <line number="11" hits="0"/>
    </lines></class>
  </classes></package></packages>
</coverage>
"""


def _make_tmp_with_src(context):
    context.tmp = Path(tempfile.mkdtemp(prefix="crap-"))
    (context.tmp / "src").mkdir()
    (context.tmp / "src" / "stub.py").write_text(STUB_PY)


@given("a coverage artifact for a high-CCN, zero-coverage function")
def step_artifact_present(context):
    _make_tmp_with_src(context)
    (context.tmp / "coverage.xml").write_text(COVERAGE_XML)


@given("no coverage artifact")
def step_artifact_missing(context):
    _make_tmp_with_src(context)


@when('I run "{cmd}"')
def step_run(context, cmd):
    # cmd looks like 'harness crap --max=0 [--enforce]'; drop the leading "harness".
    argv = shlex.split(cmd)[1:]
    # Sanitize env: behave runs under `uv run`, which leaks VIRTUAL_ENV and a
    # PATH that fronts the project's `.venv/bin`. If the harness's inner
    # `uv run coverage xml` finds `coverage`, it opens (and truncates) our
    # pre-populated coverage.xml before noticing it has no `.coverage` to
    # convert from, leaving us with an empty file. Drop both so uv errors out
    # before opening anything.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PATH"] = ":".join(
        p for p in env.get("PATH", "").split(":") if "/.venv/" not in p
    )
    result = subprocess.run(
        [sys.executable, str(HARNESS), *argv],
        cwd=str(context.tmp),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    context.result = result
    context.output = result.stdout + result.stderr


@then("the exit code is {code:d}")
def step_exit_code(context, code):
    assert context.result.returncode == code, (
        f"expected exit {code}, got {context.result.returncode}\n--- output ---\n{context.output}"
    )


@then('the output contains "{text}"')
def step_output_contains(context, text):
    assert text in context.output, (
        f"expected {text!r} in output:\n{context.output}"
    )


@then('the output does not contain "{text}"')
def step_output_not_contains(context, text):
    assert text not in context.output, (
        f"unexpected {text!r} in output:\n{context.output}"
    )


@then("the output mentions running the coverage command first")
def step_output_coverage_hint(context):
    out = context.output.lower()
    assert "coverage" in out and "first" in out, (
        f"expected coverage-hint in output:\n{context.output}"
    )
