Feature: The baseline is a ratchet, not a wall
  `.harness-baseline` records where the repo already is — coverage, complexity,
  duplication, CRAP and suppressions — so adoption never starts on a red gate, and each
  number may only move down. A metric with no recorded floor reports instead of
  blocking. The merge semantics themselves (drop, preserve, abort) are unit
  tested against `writeBaseline`; these scenarios are the end-to-end slice.

  Scenario: Updating the baseline records every metric it could measure
    Given a project with no baseline
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the baseline file contains "coverage.min"
    And the baseline file contains "complexity.max_violations 0"
    And the baseline file contains "duplication.max_blocks 0"
    And the baseline file contains "crap.max_violations"

  Scenario: Complexity is report-only until a floor is recorded
    Given a project with a CCN-21 function and no baseline
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "report-only: no .harness-baseline floor"
    And the output contains "suppressions --update-baseline"

  Scenario: The complexity floor blocks a new violation
    Given a project with a CCN-21 function and a baseline line "complexity.max_violations 0"
    When I run "harness complexity"
    Then the exit code is 1
    And the output contains "Complexity (lizard)"

  Scenario: The duplication floor blocks a newly copy-pasted block
    Given a project with two copies of the same function and a baseline line "duplication.max_blocks 0"
    When I run "harness complexity"
    Then the exit code is 1
    And the output contains "Duplicate blocks (lizard, baseline 0)"
    And the output contains "block(s)"

  Scenario: The CRAP floor tolerates exactly the recorded offenders
    Given a coverage artifact for a high-CCN, zero-coverage function
    And the baseline line "crap.max_violations 1"
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 0
    And the output contains "baseline 1"

  Scenario: CRAP is report-only until a floor is recorded, --enforce included
    Given a coverage artifact for a high-CCN, zero-coverage function
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 0
    And the output contains "report-only: no .harness-baseline floor"
    And the output contains "suppressions --update-baseline"
