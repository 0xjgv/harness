Feature: CRAP gate is advisory by default, enforceable on demand
  The crap command surfaces high-risk functions (complex + undertested)
  without blocking CI unless --enforce is passed.

  Scenario: Advisory mode exits 0 when offenders exist
    Given a coverage artifact for a high-CCN, zero-coverage function
    When I run "harness crap --max=0"
    Then the exit code is 0
    And the output contains "(advisory)"

  Scenario: Enforce mode exits 1 when offenders exist
    Given a coverage artifact for a high-CCN, zero-coverage function
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 1
    And the output does not contain "(advisory)"

  Scenario: Missing coverage artifact fails with hint
    Given no coverage artifact
    When I run "harness crap"
    Then the exit code is 1
    And the output mentions running the coverage command first
