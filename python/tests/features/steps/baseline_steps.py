import tempfile
from pathlib import Path

from behave import given, then

BASELINE = ".harness-baseline"

# CCN 21 — over the template's threshold of 15, so lizard flags exactly one function.
COMPLEX_PY = (
    "def f(n):\n    t = 0\n"
    + "".join(f"    if n == {i}:\n        t += {i}\n" for i in range(20))
    + "    return t\n"
)


def _make_project(context, *, source: str = "x = 1\n") -> Path:
    context.tmp = Path(tempfile.mkdtemp(prefix="baseline-"))
    (context.tmp / "src").mkdir()
    (context.tmp / "src" / "stub.py").write_text(source)
    return context.tmp


@given("a project with no baseline")
def step_project_without_baseline(context):
    _make_project(context)


@given('a project with a baseline line "{line}"')
def step_project_with_baseline_line(context, line):
    (_make_project(context) / BASELINE).write_text(f"{line}\n")


@given('a project with a CCN-21 function and a baseline line "{line}"')
def step_project_with_complex_function(context, line):
    (_make_project(context, source=COMPLEX_PY) / BASELINE).write_text(f"{line}\n")


@given("a project with a CCN-21 function and no baseline")
def step_project_with_complex_function_no_baseline(context):
    _make_project(context, source=COMPLEX_PY)


# Two identical bodies of ~85 tokens each: lizard's duplicate finder needs a run of
# at least 70 unified tokens (`min_duplicate_tokens`) before it reports a block, so a
# short repeated snippet would not trip it. Reports exactly one `Duplicate block:`.
_DUPLICATE_BODY = (
    "    total = order.base\n"
    + "".join(f"    total += order.part_{i}\n" for i in range(16))
    + "    return total\n"
)
DUPLICATE_PY = (
    "\n\n".join(f"def total_{name}(order):\n{_DUPLICATE_BODY}" for name in ("a", "b")) + "\n"
)


@given("a project with a duplicate block and no baseline")
def step_project_with_duplicate_block(context):
    _make_project(context, source=DUPLICATE_PY)


@given('a project with a duplicate block and a baseline line "{line}"')
def step_project_with_duplicate_block_and_baseline(context, line):
    (_make_project(context, source=DUPLICATE_PY) / BASELINE).write_text(f"{line}\n")


def _dead_functions(count: int) -> str:
    return "".join(f"def dead_{i}():\n    return {i}\n\n" for i in range(count))


@given("a project with {count:d} dead functions and no baseline")
def step_project_with_dead_code(context, count):
    _make_project(context, source=_dead_functions(count))


@given('a project with {count:d} dead functions and a baseline line "{line}"')
def step_project_with_dead_code_and_baseline(context, count, line):
    (_make_project(context, source=_dead_functions(count)) / BASELINE).write_text(f"{line}\n")


@given("a project whose tests cannot be imported")
def step_project_with_unimportable_tests(context):
    # The exact shape that made doghouse record `coverage.min 7`: coverage still
    # produces a number, but every test module blew up on import.
    root = _make_project(context)
    (root / BASELINE).write_text("coverage.min 40\n")
    context.baseline_before = (root / BASELINE).read_text()
    (root / "tests").mkdir()
    (root / "tests" / "test_broken.py").write_text("import no_such_module_at_all\n")


@then('the baseline file contains "{text}"')
def step_baseline_contains(context, text):
    content = (context.tmp / BASELINE).read_text()
    assert text in content, f"expected {text!r} in {BASELINE}:\n{content}"


@then('the baseline file does not contain "{text}"')
def step_baseline_not_contains(context, text):
    content = (context.tmp / BASELINE).read_text()
    assert text not in content, f"unexpected {text!r} in {BASELINE}:\n{content}"


@then("the baseline file is unchanged")
def step_baseline_unchanged(context):
    content = (context.tmp / BASELINE).read_text()
    assert content == context.baseline_before, (
        f"{BASELINE} was rewritten:\n{context.baseline_before!r} -> {content!r}"
    )
