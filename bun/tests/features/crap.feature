Feature: CRAP gate is advisory by default, enforceable on demand
  The crap command surfaces high-risk functions (complex + undertested)
  without blocking CI unless --enforce is passed. Both modes compare the
  offender count to the `crap.max_violations` floor in `.harness-baseline`;
  with no floor recorded the gate is report-only (see baseline.feature).

  Scenario: Advisory mode exits 0 above the recorded floor
    Given a coverage artifact for a high-CCN, zero-coverage function
    And the baseline line "crap.max_violations 0"
    When I run "harness crap --max=0"
    Then the exit code is 0
    And the output contains "(advisory)"

  Scenario: Enforce mode exits 1 above the recorded floor
    Given a coverage artifact for a high-CCN, zero-coverage function
    And the baseline line "crap.max_violations 0"
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 1
    And the output does not contain "(advisory)"

  Scenario: Missing coverage artifact regenerates before scoring
    Given no coverage artifact
    And the baseline line "crap.max_violations 0"
    When I run "harness crap"
    Then the exit code is 0
    And the output contains "Coverage (run)"
    And the output contains "(advisory)"
