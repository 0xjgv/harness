Feature: Scoped mutation testing against a mutation.min floor
  Mutation testing scores the tests, not the code: what share of the mutants
  a run produced did the suite kill. `--report=` scores a gremlins report
  that already exists — the same gate, without paying minutes for a fresh
  run — so these scenarios exercise the floor comparison itself.

  Scenario: Mutation is report-only when no floor is recorded
    Given a gremlins report with 3 killed and 1 surviving mutants
    When I run "harness mutation --report=gremlins-report.json"
    Then the exit code is 0
    And the output contains "75% mutants killed"
    And the output contains "report-only: no .harness-baseline floor"
    And the output contains "suppressions --update-baseline --with-mutation"

  Scenario: Mutation passes at the recorded floor
    Given a gremlins report with 3 killed and 1 surviving mutants
    And a .harness-baseline carrying "mutation.min 75"
    When I run "harness mutation --report=gremlins-report.json"
    Then the exit code is 0
    And the output contains "75% mutants killed (baseline 75)"

  Scenario: Mutation warns below the recorded floor
    Given a gremlins report with 3 killed and 1 surviving mutants
    And a .harness-baseline carrying "mutation.min 100"
    When I run "harness mutation --report=gremlins-report.json"
    Then the exit code is 0
    And the output contains "(baseline 100) (advisory)"

  Scenario: Mutation fails below the recorded floor under --enforce
    Given a gremlins report with 3 killed and 1 surviving mutants
    And a .harness-baseline carrying "mutation.min 100"
    When I run "harness mutation --report=gremlins-report.json --enforce"
    Then the exit code is 1
    And the output contains "75% mutants killed (baseline 100)"

  Scenario: Mutation has nothing to score when no mutant ran
    Given a gremlins report with 0 killed and 0 surviving mutants
    And a .harness-baseline carrying "mutation.min 100"
    When I run "harness mutation --report=gremlins-report.json --enforce"
    Then the exit code is 0
    And the output contains "nothing to score"

  Scenario: An unreadable report fails rather than scoring as unmeasurable
    Given a gremlins report with 3 killed and 1 surviving mutants
    When I run "harness mutation --report=no-such-report.json"
    Then the exit code is 1
    And the output contains "cannot read gremlins report"

  Scenario: A path argument that is not a package directory fails
    Given a gremlins report with 3 killed and 1 surviving mutants
    When I run "harness mutation ./no-such-package --enforce"
    Then the exit code is 1
    And the output contains "is not a package directory"

  Scenario: A JSON file that is not a gremlins report is rejected
    Given a JSON file that is not a gremlins report
    When I run "harness mutation --report=not-a-report.json"
    Then the exit code is 1
    And the output contains "is not a gremlins report"

  Scenario: Updating the baseline leaves mutation.min alone without --with-mutation
    Given a function above the complexity threshold
    And a .harness-baseline carrying "mutation.min 40"
    When I run "harness suppressions --update-baseline"
    Then the exit code is 0
    And the output does not contain "mutation.min"
    And the .harness-baseline file contains "mutation.min 40"
