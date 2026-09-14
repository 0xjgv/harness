Feature: Branch guard keeps merge authority with the human
  pre-push refuses a push that lands on main or master. Agents push a feature
  branch and open a PR; a human overrides with HARNESS_ALLOW_PROTECTED_PUSH=1.

  Scenario: Push to main is refused
    Given a pre-push ref targeting "refs/heads/main"
    When the branch guard runs
    Then the branch guard exits 1
    And the branch guard output contains "Push targets protected branch: main"

  Scenario: Push to a feature branch passes
    Given a pre-push ref targeting "refs/heads/feature/topic"
    When the branch guard runs
    Then the branch guard exits 0
    And the branch guard output contains "Branch guard"

  Scenario: Deleting main is refused
    Given a pre-push deletion of "refs/heads/main"
    When the branch guard runs
    Then the branch guard exits 1
    And the branch guard output contains "Push targets protected branch: main"

  Scenario: Deleting a feature branch passes
    Given a pre-push deletion of "refs/heads/feature/topic"
    When the branch guard runs
    Then the branch guard exits 0
    And the branch guard output contains "Branch guard"
