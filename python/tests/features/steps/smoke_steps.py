from behave import given, then, when


@given("a fresh python environment")
def step_fresh_env(context):
    context.error = None
    context.module = None


@when("I import src")
def step_import_src(context):
    try:
        import src

        context.module = src
    except Exception as exc:
        context.error = exc


@then("no exception is raised")
def step_no_exception(context):
    assert context.error is None, f"unexpected error: {context.error}"
    assert context.module is not None
