Feature: Mutation testing is scoped, advisory, and ratcheted
  The mutation command scores the share of injected bugs the suite kills and
  compares it to the mutation.min floor in .harness-baseline. It is advisory by
  default so a slow, noisy signal never turns a build red on its own.

  # A real cargo-mutants pass costs minutes, so these scenarios score a fixture
  # outcomes.json via HARNESS_MUTATION_OUTCOMES instead of running the tool.
  # --all is passed so the run is not first skipped for having an empty diff.

  Scenario: Mutation is report-only without a recorded floor
    Given a cargo-mutants outcomes file where 3 of 4 mutants are killed
    When I run "harness mutation --all --enforce"
    Then the exit code is 0
    And the output contains "75% of mutants killed"
    And the output contains "report-only: no .harness-baseline floor"
    And the output contains "suppressions --update-baseline --with-mutation"

  Scenario: Meeting the recorded floor passes
    Given a cargo-mutants outcomes file where 3 of 4 mutants are killed
    And a recorded mutation floor of 75
    When I run "harness mutation --all"
    Then the exit code is 0
    And the output contains "75% of mutants killed (baseline 75%)"
    And the output does not contain "advisory"

  Scenario: Missing the recorded floor warns but does not fail
    Given a cargo-mutants outcomes file where 3 of 4 mutants are killed
    And a recorded mutation floor of 90
    When I run "harness mutation --all"
    Then the exit code is 0
    And the output contains "(advisory)"

  Scenario: Enforce mode exits 1 when the recorded floor is missed
    Given a cargo-mutants outcomes file where 3 of 4 mutants are killed
    And a recorded mutation floor of 90
    When I run "harness mutation --all --enforce"
    Then the exit code is 1
    And the output does not contain "(advisory)"

  Scenario: A run that generated no mutants is report-only, not zero percent
    Given a cargo-mutants outcomes file where no mutants were generated
    And a recorded mutation floor of 90
    When I run "harness mutation --all --enforce"
    Then the exit code is 0
    And the output contains "no mutants were generated (report-only)"

  Scenario: Nothing changed under src means nothing to mutate
    Given a project with no .harness-baseline
    When I run "harness mutation"
    Then the exit code is 0
    And the output contains "Mutation skipped: no changed sources under src/"
