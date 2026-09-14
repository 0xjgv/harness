Feature: Pre-push branch guard refuses protected branches
  The branch-guard command refuses a push that would land on main or master.
  Without git pre-push stdin refs it falls back to the current branch, which is
  what these scenarios exercise in a throwaway repo.

  Scenario: Push to main is refused
    Given a git repo on branch "main"
    When I run "harness branch-guard"
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"

  Scenario: Push to a feature branch passes
    Given a git repo on branch "feature/guard"
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard"

  Scenario: Deleting main is refused
    Given a git repo on branch "feature/guard"
    And the push refs are "(delete) 0000000000000000000000000000000000000000 refs/heads/main def456"
    When I run "harness branch-guard"
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"

  Scenario: Deleting a feature branch passes
    Given a git repo on branch "feature/guard"
    And the push refs are "(delete) 0000000000000000000000000000000000000000 refs/heads/feature/old def456"
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard"

  Scenario: A tag push is not a protected branch
    Given a git repo on branch "main"
    And the push refs are "refs/tags/v1 abc123 refs/tags/v1 def456"
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard"

  Scenario: The override lets a push to main through
    Given a git repo on branch "main"
    And the protected-push override is set
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard override: main"

  Scenario: Malformed forwarded refs fall back to the current branch
    Given a git repo on branch "main"
    And the push refs are "garbage"
    When I run "harness branch-guard"
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"

  Scenario: Pre-push refuses before any other gate runs
    Given a git repo on branch "main"
    When I run "harness pre-push"
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"
    And the output does not contain "Arch config"
    And the output does not contain "agents-md-drift"
    And the output does not contain "Clippy"
