"""Unit tests for src.adapters.formatting."""

import unittest

from src.adapters.formatting import format_currency, render_receipt
from src.core.pricing import Item


class TestFormatCurrency(unittest.TestCase):
    def test_formats_with_thousands_separator_and_two_decimals(self) -> None:
        self.assertEqual(format_currency(1234.5), "$1,234.50")

    def test_formats_zero(self) -> None:
        self.assertEqual(format_currency(0), "$0.00")


class TestRenderReceipt(unittest.TestCase):
    def test_lists_each_item_and_total(self) -> None:
        items = [
            Item(name="widget", unit_price=2.0, quantity=2),
            Item(name="gadget", unit_price=5.0, quantity=1),
        ]
        receipt = render_receipt(items)
        self.assertIn("widget x2: $4.00", receipt)
        self.assertIn("gadget x1: $5.00", receipt)
        self.assertIn("Total: $9.00", receipt)

    def test_applies_discount_to_total_line(self) -> None:
        items = [Item(name="widget", unit_price=10.0, quantity=1)]
        receipt = render_receipt(items, discount_percent=50)
        self.assertIn("Total: $5.00", receipt)


if __name__ == "__main__":
    unittest.main()
