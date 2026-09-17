Feature: CLAUDE.md is canonical and AGENTS.md mirrors it
  The stop hook carries an uncommitted CLAUDE.md edit into AGENTS.md; pre-commit
  stages the mirror of a staged CLAUDE.md. A hand edit to AGENTS.md alone is never
  synced over: pre-commit reports it. Tests run at pre-push, not pre-commit.

  Scenario: The stop hook mirrors every uncommitted CLAUDE.md edit
    Given a repo whose CLAUDE.md and AGENTS.md agree
    And "AGENTS.md" is written as:
      """
      v1 hand edit
      """
    And "CLAUDE.md" is written as:
      """
      v2
      """
    When I run the hook "harness stop-hook"
    Then the hook exits 0 silently
    And "AGENTS.md" reads:
      """
      v2
      """
    Given "CLAUDE.md" is written as:
      """
      v3
      """
    And "CLAUDE.md" is staged
    When I run the hook "harness stop-hook"
    Then the hook exits 0 silently
    And "AGENTS.md" reads:
      """
      v3
      """

  Scenario: The stop hook leaves an AGENTS.md-only edit
    Given a repo whose CLAUDE.md and AGENTS.md agree
    And "AGENTS.md" is written as:
      """
      hand edit
      """
    When I run the hook "harness stop-hook"
    Then the hook exits 0 silently
    And "AGENTS.md" reads:
      """
      hand edit
      """

  Scenario: Pre-commit stages the mirror of a staged CLAUDE.md
    Given a repo whose CLAUDE.md and AGENTS.md agree
    And "CLAUDE.md" is written as:
      """
      v2
      """
    And "CLAUDE.md" is staged
    When I run the hook "harness pre-commit"
    Then the exit code is 0
    And the output contains "AGENTS.md ← CLAUDE.md (staged)"
    And the staged files are "AGENTS.md CLAUDE.md"

  Scenario: Pre-commit fails on a mirror edited alone
    Given a repo whose CLAUDE.md and AGENTS.md agree
    And "AGENTS.md" is written as:
      """
      hand edit
      """
    And "AGENTS.md" is staged
    When I run the hook "harness pre-commit"
    Then the exit code is 1
    And the output contains "AGENTS.md differs from CLAUDE.md"

  Scenario: Pre-commit fails on a hand-edited mirror staged with source
    Given a repo whose CLAUDE.md and AGENTS.md agree
    And "AGENTS.md" is written as:
      """
      hand edit
      """
    And "src/lib.rs" is written as:
      """
      pub const VALUE: i32 = 1;
      """
    And "AGENTS.md" is staged
    And "src/lib.rs" is staged
    When I run the hook "harness pre-commit"
    Then the exit code is 1
    And the output contains "AGENTS.md differs from CLAUDE.md"
    And "AGENTS.md" reads:
      """
      hand edit
      """

  Scenario: Pre-commit fixes staged Rust files and no longer runs the tests
    Given a crate in "."
    And the change is committed
    And a line is appended to "src/lib.rs"
    And "src/lib.rs" is staged
    When I run the hook "harness pre-commit"
    Then the exit code is 0
    And the output contains "Clippy fix"
    And the output does not contain "Tests"
