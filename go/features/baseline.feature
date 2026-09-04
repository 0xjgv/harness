Feature: Ratcheted floors in .harness-baseline
  A repo adopting the harness starts wherever it already is. Without a
  recorded floor the complexity and duplication gates measure and report;
  with one they block any growth past it. `suppressions --update-baseline`
  records the floors it can measure, drops the ones it cannot, and never
  disturbs a key it does not own.

  Scenario: Complexity is report-only when no floor is recorded
    Given a function above the complexity threshold
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "report-only: no .harness-baseline floor"

  Scenario: Complexity passes at the recorded floor
    Given a function above the complexity threshold
    And a .harness-baseline recording 1 complexity violations
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "baseline 1"

  Scenario: Complexity fails above the recorded floor
    Given a function above the complexity threshold
    And a .harness-baseline recording 0 complexity violations
    When I run "harness complexity"
    Then the exit code is 1
    And the output contains "do not raise the threshold"

  Scenario: Duplication is report-only when no floor is recorded
    Given a duplicated block of code
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "Duplication (lizard, report-only: no .harness-baseline floor)"
    And the output contains "run `go run harness.go suppressions --update-baseline` to record a floor"

  Scenario: Duplication passes at the recorded floor
    Given a duplicated block of code
    And a .harness-baseline carrying "duplication.max_blocks 1"
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "Duplication (lizard, baseline 1)"

  Scenario: Duplication fails above the recorded floor
    Given a duplicated block of code
    And a .harness-baseline carrying "duplication.max_blocks 0"
    When I run "harness complexity"
    Then the exit code is 1
    And the output contains "1 duplicate block(s)"
    And the output contains "extract the duplicated code into one function"

  Scenario: Updating the baseline records the duplicate-block floor
    Given a duplicated block of code
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the .harness-baseline file contains "duplication.max_blocks 1"

  Scenario: Updating the baseline records measured floors and preserves unknown keys
    Given a function above the complexity threshold
    And a .harness-baseline carrying "mutation.min 40"
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the output contains "complexity.max_violations 1"
    And the .harness-baseline file contains "mutation.min 40"

  Scenario: Updating the baseline records the coverage floor
    Given a test that exercises the stub function
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the output contains "coverage.min"
    And the .harness-baseline file contains "coverage.min"

  Scenario: Updating the baseline drops a floor it cannot measure
    Given a function above the complexity threshold
    And a .harness-baseline carrying "crap.max_violations 5"
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the output contains "crap.max_violations: dropped"
    And the .harness-baseline file does not contain "crap.max_violations"

  Scenario: CRAP is report-only when no floor is recorded, even under --enforce
    Given a coverage artifact for a high-CCN, zero-coverage function
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 0
    And the output contains "report-only: no .harness-baseline floor"
    And the output contains "suppressions --update-baseline"

  Scenario: CRAP fails above the recorded floor under --enforce
    Given a coverage artifact for a high-CCN, zero-coverage function
    And a .harness-baseline carrying "crap.max_violations 0"
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 1
    And the output contains "(baseline 0)"

  Scenario: CRAP passes at the recorded floor
    Given a coverage artifact for a high-CCN, zero-coverage function
    And a .harness-baseline carrying "crap.max_violations 2"
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 0
    And the output contains "(baseline 2)"
