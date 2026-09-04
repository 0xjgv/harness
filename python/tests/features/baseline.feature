Feature: The baseline is a ratchet, not a wall
  `.harness-baseline` records where the repo already is — coverage, complexity,
  CRAP, dead code, mutation score and suppressions — so adoption never starts on a
  red gate, and each number may only move toward better. A metric with no recorded
  floor reports instead of blocking; a metric that could not be measured is never
  recorded at all.

  Scenario: A repo with no baseline is report-only and passes
    Given a project with no baseline
    When I run "harness suppressions"
    Then the exit code is 0
    And the output contains "report-only"
    And the output contains "to start ratcheting"

  Scenario: Updating the baseline records only the metrics it could measure
    Given a project with no baseline
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the baseline file contains "complexity.max_violations"
    And the baseline file contains "deadcode.max_findings"
    But the baseline file does not contain "coverage.min"
    And the baseline file does not contain "crap.max_violations"
    And the baseline file does not contain "mutation.min"

  Scenario: A shipped coverage floor never leaks into an adopting repo's baseline
    Given a project with a baseline line "coverage.min 100"
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the baseline file does not contain "coverage.min"

  Scenario: A metric that cannot be measured fails loudly and writes nothing
    Given a project whose tests cannot be imported
    When I run "harness suppressions --update-baseline"
    Then the exit code is 1
    And the output contains "not written"
    And the output contains "coverage.min"
    And the baseline file is unchanged

  Scenario: Complexity is report-only until a floor is recorded
    Given a project with a CCN-21 function and no baseline
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "report-only"

  Scenario: Dead code is report-only until a floor is recorded
    Given a project with 2 dead functions and no baseline
    When I run "harness deadcode"
    Then the exit code is 0
    And the output contains "report-only"

  Scenario: The dead-code floor tolerates exactly the recorded findings
    Given a project with 2 dead functions and a baseline line "deadcode.max_findings 2"
    When I run "harness deadcode"
    Then the exit code is 0
    And the output contains "baseline 2"

  Scenario: The dead-code floor blocks a new finding
    Given a project with 2 dead functions and a baseline line "deadcode.max_findings 1"
    When I run "harness deadcode"
    Then the exit code is 1
    And the output contains "2 finding(s) > baseline 1"

  Scenario: Updating the baseline preserves keys the harness does not measure
    Given a project with a baseline line "custom.thing 7"
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the baseline file contains "custom.thing 7"

  Scenario: The mutation floor survives an update that did not measure it
    Given a project with a baseline line "mutation.min 94"
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the baseline file contains "mutation.min 94"

  Scenario: Mutation testing skips a change that touched no app source
    Given a project with no baseline
    When I run "harness mutation"
    Then the exit code is 0
    And the output contains "no changed app sources"

  Scenario: Mutation testing skips a repo that has not installed mutmut
    Given a project with no baseline
    When I run "harness mutation --all"
    Then the exit code is 0
    And the output contains "mutmut is not installed"

  Scenario: The complexity floor tolerates exactly the recorded violations
    Given a project with a CCN-21 function and a baseline line "complexity.max_violations 1"
    When I run "harness complexity"
    Then the exit code is 0
    And the output contains "baseline 1"

  Scenario: The complexity floor blocks a new violation
    Given a project with a CCN-21 function and a baseline line "complexity.max_violations 0"
    When I run "harness complexity"
    Then the exit code is 1
    And the output contains "Complexity (lizard)"
