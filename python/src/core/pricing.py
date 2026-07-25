"""Pricing domain logic — pure functions, no I/O, no adapter imports."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    name: str
    unit_price: float
    quantity: int


def line_total(item: Item) -> float:
    """Return the price of an item line (unit price times quantity)."""
    return item.unit_price * item.quantity


def subtotal(items: list[Item]) -> float:
    """Return the sum of every item line, before discount."""
    return sum(line_total(item) for item in items)


def apply_discount(amount: float, percent: float) -> float:
    """Apply a percentage discount to an amount.

    Raises ValueError if percent is outside [0, 100].
    """
    if not 0 <= percent <= 100:
        raise ValueError(f"discount percent must be within [0, 100], got {percent}")
    return amount * (1 - percent / 100)


def order_total(items: list[Item], discount_percent: float = 0.0) -> float:
    """Return the order total after discount."""
    return apply_discount(subtotal(items), discount_percent)
