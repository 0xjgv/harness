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
