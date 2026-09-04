Feature: The test gate scopes to the change set
  check and pre-commit run only the tests that reach the edit, so the gate stays
  proportional to the change. --all, ci and pre-push still run the whole suite.

  Scenario: A clean tree skips the gate instead of widening it
    Given a committed project with a test for each source
    When I run "harness test"
    Then the exit code is 0
    And the output contains "no changed TypeScript files"

  Scenario: An edited source runs the tests that reach it
    Given a committed project with one source edited
    When I run "harness test"
    Then the exit code is 0
    And the output contains "Tests (changed)"
    And the output contains "1 passed"

  Scenario: A base ref scopes to the commits the branch adds
    Given a committed project with one source edited in the last commit
    When I run "harness test --base=HEAD~1"
    Then the exit code is 0
    And the output contains "Tests (changed)"
    And the output contains "1 passed"

  Scenario: A source no test reaches warns without failing
    Given a committed project with an untested source added
    When I run "harness test"
    Then the exit code is 0
    And the output contains "no test imports src/orphan.ts"

  Scenario: --all runs the whole suite
    Given a committed project with a test for each source
    When I run "harness test --all"
    Then the exit code is 0
    And the output does not contain "(changed)"
