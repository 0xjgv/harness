"""Unit tests for src.core.pricing."""

import unittest

from src.core.pricing import Item, apply_discount, line_total, order_total, subtotal


class TestLineTotal(unittest.TestCase):
    def test_multiplies_price_by_quantity(self) -> None:
        item = Item(name="widget", unit_price=2.5, quantity=4)
        self.assertEqual(line_total(item), 10.0)


class TestSubtotal(unittest.TestCase):
    def test_sums_all_line_totals(self) -> None:
        items = [
            Item(name="widget", unit_price=2.0, quantity=2),
            Item(name="gadget", unit_price=5.0, quantity=1),
        ]
        self.assertEqual(subtotal(items), 9.0)

    def test_empty_order_is_zero(self) -> None:
        self.assertEqual(subtotal([]), 0.0)


class TestApplyDiscount(unittest.TestCase):
    def test_zero_percent_is_unchanged(self) -> None:
        self.assertEqual(apply_discount(100.0, 0), 100.0)

    def test_full_percent_is_zero(self) -> None:
        self.assertEqual(apply_discount(100.0, 100), 0.0)

    def test_partial_percent(self) -> None:
        self.assertEqual(apply_discount(200.0, 25), 150.0)

    def test_rejects_negative_percent(self) -> None:
        with self.assertRaises(ValueError):
            apply_discount(100.0, -1)

    def test_rejects_over_100_percent(self) -> None:
        with self.assertRaises(ValueError):
            apply_discount(100.0, 101)


class TestOrderTotal(unittest.TestCase):
    def test_applies_discount_to_subtotal(self) -> None:
        items = [Item(name="widget", unit_price=10.0, quantity=2)]
        self.assertEqual(order_total(items, discount_percent=10), 18.0)

    def test_no_discount_by_default(self) -> None:
        items = [Item(name="widget", unit_price=10.0, quantity=1)]
        self.assertEqual(order_total(items), 10.0)


if __name__ == "__main__":
    unittest.main()
