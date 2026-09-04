Feature: Order receipts respect the core/adapters layering
  src.adapters.formatting builds on src.core.pricing to render a receipt. The
  first scenario exercises that layered example end-to-end; the rest cover how
  `harness arch` holds the boundary — as a ratchet against `arch.max_violations`,
  so a real layering contract can be written over a tree that already breaks it.

  Scenario: Rendering a discounted receipt
    Given an order of 2 widgets at $2.00 and 1 gadget at $5.00
    When I render the receipt with a 10 percent discount
    Then the receipt shows a total of "$8.10"

  Scenario: Arch is report-only until a floor is recorded
    Given a project with an .importlinter reporting 3 broken imports and no baseline
    When I run "harness arch"
    Then the exit code is 0
    And the output contains "report-only"

  Scenario: The arch floor tolerates exactly the recorded violations
    Given a project with an .importlinter reporting 3 broken imports and a baseline line "arch.max_violations 3"
    When I run "harness arch"
    Then the exit code is 0
    And the output contains "3 (baseline 3)"

  Scenario: The arch floor blocks a new violation
    Given a project with an .importlinter reporting 3 broken imports and a baseline line "arch.max_violations 2"
    When I run "harness arch"
    Then the exit code is 1
    And the output contains "3 violation(s) > baseline 2"
    And the output contains "src.core.mod0 -> src.adapters.formatting"

  Scenario: A repo with no layering contract is skipped, not failed
    Given a project with no .importlinter
    When I run "harness arch"
    Then the exit code is 0
    And the output contains "no .importlinter"

  Scenario: A layering contract that cannot be analysed fails instead of counting zero
    Given a project with an .importlinter whose tool cannot run
    When I run "harness arch"
    Then the exit code is 1
    And the output contains "lint-imports failed to run"
