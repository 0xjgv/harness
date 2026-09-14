Feature: Branch guard refuses pushes to protected branches
  The branch guard keeps main and master human-owned: agents push feature
  branches and open a PR instead.

  Scenario: Push to main is refused
    Given a git repository on branch "main"
    When I run "harness branch-guard"
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"

  Scenario: Push to a feature branch passes
    Given a git repository on branch "feature/guard"
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard"

  Scenario: Forwarded refs outrank the checked-out branch
    Given a git repository on branch "main"
    And the push updates "refs/heads/feature"
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard"

  Scenario: Deleting main is refused
    Given a git repository on branch "feature/guard"
    And the push deletes "refs/heads/main"
    When I run "harness branch-guard"
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"

  Scenario: Deleting a feature branch passes
    Given a git repository on branch "feature/guard"
    And the push deletes "refs/heads/feature"
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard"

  Scenario: A tag ref named main passes
    Given a git repository on branch "feature/guard"
    And the push updates "refs/tags/main"
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard"

  Scenario: The override lets a human push to main
    Given a git repository on branch "main"
    And the protected-push override is set
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard override: main"

  Scenario: Malformed forwarded refs on main are refused
    Given a git repository on branch "main"
    And the push forwards "garbage"
    When I run "harness branch-guard"
    Then the exit code is 1
    And the output contains "Push targets protected branch: main"

  # A tag-only ref list is a well-formed, parseable record — a real answer,
  # not "no refs forwarded" — so it must NOT fall back to the checked-out
  # branch. Pushing a tag from main (`git push origin v1.0`) is legitimate.
  Scenario: A tag-only ref list on main passes
    Given a git repository on branch "main"
    And the push updates "refs/tags/main"
    When I run "harness branch-guard"
    Then the exit code is 0
    And the output contains "Branch guard"
