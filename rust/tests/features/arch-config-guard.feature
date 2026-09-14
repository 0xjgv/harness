Feature: Arch config guard sees a whole branch on its first push
  Pushing a branch the remote does not have yet sends an all-zero remote sha.
  The guard diffs from the merge-base with origin/main, so an arch config change
  made in an earlier commit of that branch is still reported.

  Scenario: Arch config changed earlier in the branch is reported
    Given a new branch whose earlier commit changed the arch config
    And the push refs are "refs/heads/feature/arch <HEAD> refs/heads/feature/arch 0000000000000000000000000000000000000000"
    When I run "harness arch-config-guard --pre-push"
    Then the exit code is 1
    And the output contains "Arch config changed: arch.toml"
