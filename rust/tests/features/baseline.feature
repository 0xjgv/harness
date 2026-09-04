Feature: Ratcheted floors come from .harness-baseline
  Count-based gates read their floor from .harness-baseline. A repo that has
  never recorded one must still be green on day one, so the gate degrades to
  report-only instead of demanding perfection.

  Scenario: Complexity is report-only without a recorded floor
    Given a project with no .harness-baseline
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "report-only: no .harness-baseline floor"
    And the output contains "suppressions --update-baseline"

  Scenario: Complexity enforces the recorded floor
    Given a project with a complexity floor of 0
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "Complexity (lizard)"
    And the output does not contain "report-only"

  Scenario: CRAP is report-only without a recorded floor, even under --enforce
    Given a coverage artifact for a high-CCN, zero-coverage function
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 0
    And the output contains "report-only: no .harness-baseline floor"
    And the output contains "suppressions --update-baseline"

  # The fixture is a real crate with one orphan file, so with cargo-modules
  # installed this exercises report-only against a count of 1. Asserted as
  # "never fails" rather than by label because without cargo-modules the gate
  # skips instead, and a copy-pasted template must be green either way.
  Scenario: Arch cannot fail before a floor is recorded
    Given a project with an arch.toml and no .harness-baseline
    When I run "harness arch"
    Then the exit code is 0
    And the output does not contain "✗"
