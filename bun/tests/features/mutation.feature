Feature: Mutation score is advisory, ratcheted by mutation.min
  `mutation` runs Stryker over the source files a change touched (the whole tree
  with --all) and scores killed / (killed + survived). With no `mutation.min` in
  `.harness-baseline` it reports and passes; with one it warns on a miss, and
  only --enforce turns that into a non-zero exit — the floor is measured
  whole-tree, so a scoped run can fall under it with nothing regressed.

  Scenario: Nothing changed means nothing to mutate
    Given a project with a partly tested source file
    When I run "harness mutation"
    Then the exit code is 0
    And the output contains "no changed source files"

  Scenario: Without a recorded floor the score is report-only
    Given a project with a partly tested source file
    When I run "harness mutation --all"
    Then the exit code is 0
    And the output contains "report-only: no .harness-baseline floor"

  Scenario: A missed floor warns, and only --enforce fails
    Given a project with a partly tested source file
    And the baseline line "mutation.min 100"
    When I run "harness mutation --all"
    Then the exit code is 0
    And the output contains "(advisory)"
    When I run "harness mutation --all --enforce"
    Then the exit code is 1
    And the output does not contain "(advisory)"
