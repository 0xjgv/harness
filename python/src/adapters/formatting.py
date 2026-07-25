"""Receipt formatting adapter — renders core pricing results as text."""

from src.core.pricing import Item, line_total, order_total


def format_currency(amount: float) -> str:
    """Render an amount as a dollar string, e.g. 1234.5 -> '$1,234.50'."""
    return f"${amount:,.2f}"


def render_receipt(items: list[Item], discount_percent: float = 0.0) -> str:
    """Render a plain-text receipt for an order."""
    lines = [
        f"{item.name} x{item.quantity}: {format_currency(line_total(item))}" for item in items
    ]
    total = order_total(items, discount_percent)
    lines.append(f"Total: {format_currency(total)}")
    return "\n".join(lines)
