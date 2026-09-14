Feature: Branch guard refuses pushes to protected branches
  The branch-guard command keeps agents off main/master; humans override it
  explicitly with HARNESS_ALLOW_PROTECTED_PUSH=1.

  Scenario: Push to main is refused
    Given a git repository on branch "main"
    When I run the branch guard with no push refs
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"

  Scenario: Push to a feature branch passes
    Given a git repository on branch "feature/branch-guard"
    When I run the branch guard with no push refs
    Then the exit code is 0
    And the output contains "Branch guard"

  Scenario: Deleting main is refused
    Given a git repository on branch "feature/branch-guard"
    When I run the branch guard with push refs "(delete) 0000000000000000000000000000000000000000 refs/heads/main def456"
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"

  Scenario: Deleting a feature branch passes
    Given a git repository on branch "feature/branch-guard"
    When I run the branch guard with push refs "(delete) 0000000000000000000000000000000000000000 refs/heads/feature def456"
    Then the exit code is 0
    And the output contains "Branch guard"
