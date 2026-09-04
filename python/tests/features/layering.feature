Feature: Order receipts respect the core/adapters layering
  src.adapters.formatting builds on src.core.pricing to render a receipt.
  This scenario exercises the layered example end-to-end.

  Scenario: Rendering a discounted receipt
    Given an order of 2 widgets at $2.00 and 1 gadget at $5.00
    When I render the receipt with a 10 percent discount
    Then the receipt shows a total of "$8.10"
