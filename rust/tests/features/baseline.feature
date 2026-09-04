Feature: Ratcheted floors come from .harness-baseline
  Count-based gates read their floor from .harness-baseline. A repo that has
  never recorded one must still be green on day one, so the gate degrades to
  report-only instead of demanding perfection.

  # Each gate is named in full: "report-only" alone would pass on either
  # lizard gate's line, hiding a regression in the other.
  Scenario: Both lizard gates are report-only without a recorded floor
    Given a project with no .harness-baseline
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "Complexity (lizard, report-only: no .harness-baseline floor)"
    And the output contains "Duplication (lizard, report-only: no .harness-baseline floor)"
    And the output contains "suppressions --update-baseline"

  Scenario: Both lizard gates enforce their recorded floors
    Given a project with complexity and duplication floors of 0
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "Complexity (lizard)"
    And the output contains "Duplication (lizard)"
    And the output contains "duplicate blocks: 0"
    And the output does not contain "report-only"

  Scenario: CRAP is report-only without a recorded floor, even under --enforce
    Given a coverage artifact for a high-CCN, zero-coverage function
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 0
    And the output contains "report-only: no .harness-baseline floor"
    And the output contains "suppressions --update-baseline"
