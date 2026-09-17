Feature: The stop hook judges the change, not the tree
  After every agent turn the stop hook formats changed files, then blocks (exit 2,
  findings on stderr, nothing on stdout) on lint left on changed lines and on
  over-limit functions the change touched. It prints nothing when clean, and it
  exits 1 instead of blocking a stop the agent is already continuing from.

  Scenario: A clean tree stops silently
    Given a crate in "."
    And the change is committed
    When I run "harness stop-hook"
    Then the hook exits 0 silently

  Scenario: Only the touched over-limit function blocks, once per stop
    Given a crate in "proj"
    And "src/lib.rs" gains "legacy" with 20 branches
    And the base is committed on main
    And "src/lib.rs" gains "fresh" with 20 branches
    When I run "harness stop-hook"
    Then the hook exits 2 with stderr:
      """
      stop-hook failed: Complexity
      src/lib.rs:69: fresh CCN 21 (limit 15)
      """
    Given the hook input is '{"stop_hook_active": true}'
    When I run "harness stop-hook"
    Then the hook exits 1 with stderr:
      """
      stop-hook failed: Complexity
      src/lib.rs:69: fresh CCN 21 (limit 15)
      harness: already blocked once on this stop; not blocking again
      """

  Scenario: The PostToolUse hook formats the edited file and asks for a re-read
    Given a crate in "."
    And "src/messy.rs" is written as:
      """
      pub fn messy()->i32{2}
      """
    And the hook input names "src/messy.rs"
    When I run "harness post-edit --hook"
    Then the hook exits 0 with stdout:
      """
      {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"harness: reformatted src/messy.rs; re-read it before editing it again"}}
      """
    When I run "harness post-edit --hook"
    Then the hook exits 0 silently

  Scenario: Pre-commit stages the mirror of a staged CLAUDE.md
    Given a crate in "."
    And the change is committed
    And "CLAUDE.md" is written as:
      """
      v2
      """
    And "CLAUDE.md" is staged
    When I run "harness pre-commit"
    Then the exit code is 0
    And the output contains "AGENTS.md ← CLAUDE.md (staged)"
    And the staged files are "AGENTS.md CLAUDE.md"
