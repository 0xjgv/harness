Feature: The stop hook judges the change, not the tree
  After every agent turn the stop hook formats changed files, then blocks (exit 2,
  findings on stderr, nothing on stdout) only on lint left on changed lines and on
  functions the change pushed over a lizard limit. It prints nothing when clean,
  exits 1 when a tool cannot run, and exits 1 when the same findings come back
  while the agent is already continuing from a stop (loop guard).

  Scenario: A clean tree stops silently
    Given a crate in "."
    And the change is committed
    When I run the hook "harness stop-hook"
    Then the hook exits 0 silently

  Scenario: An untouched over-limit function does not block
    Given a crate in "proj"
    And "src/lib.rs" has "busy" with 20 branches
    And the base is committed on main
    And a line is appended to "src/lib.rs"
    And the change is committed
    When I run the hook "harness stop-hook"
    Then the hook exits 0 silently

  Scenario: A new over-limit function blocks, then the loop guard lets go
    Given a crate in "proj"
    And the base is committed on main
    And "src/lib.rs" has "busy" with 20 branches
    And the change is committed
    And the hook input is '{"stop_hook_active": true}'
    When I run the hook "harness stop-hook"
    Then the hook exits 2 with stderr:
      """
      stop-hook failed: Complexity
      src/lib.rs:5: busy CCN new→21 (limit 15)
      """
    And the loop guard holds "stop-hook-proj"
    When I run the hook "harness stop-hook"
    Then the hook exits 1 with stderr:
      """
      stop-hook failed: Complexity
      src/lib.rs:5: busy CCN new→21 (limit 15)
      harness: same findings as the previous stop; not blocking again
      """
    Given "src/lib.rs" is written as:
      """
      pub fn helper() -> i32 {
          1
      }
      """
    When I run the hook "harness stop-hook"
    Then the hook exits 0 silently
    And the loop guard holds ""

  Scenario: A function made worse blocks
    Given a crate in "."
    And "src/lib.rs" has "busy" with 16 branches
    And the base is committed on main
    And "src/lib.rs" has "busy" with 18 branches
    When I run the hook "harness stop-hook"
    Then the hook exits 2 with stderr:
      """
      stop-hook failed: Complexity
      src/lib.rs:5: busy CCN 17→19 (limit 15)
      """

  Scenario: A function made better, though still over the limit, passes
    Given a crate in "."
    And "src/lib.rs" has "busy" with 16 branches
    And the base is committed on main
    And "src/lib.rs" has "busy" with 15 branches
    When I run the hook "harness stop-hook"
    Then the hook exits 0 silently

  Scenario: Lint on a changed line blocks
    Given a crate in "."
    And the change is committed
    And "src/lib.rs" is written as:
      """
      pub fn helper() -> i32 {
          1
      }

      pub fn lint() {
          let unused = 1;
      }
      """
    When I run the hook "harness stop-hook"
    Then the hook exits 2 with stderr:
      """
      stop-hook failed: Lint
      src/lib.rs:6: unused_variables unused variable: `unused`
      """

  Scenario: Lint on an unchanged line does not block
    Given a crate in "."
    And "src/lib.rs" is written as:
      """
      pub fn helper() -> i32 {
          1
      }

      pub fn lint() {
          let unused = 1;
      }
      """
    And the change is committed
    And a line is appended to "src/lib.rs"
    When I run the hook "harness stop-hook"
    Then the hook exits 0 silently

  Scenario: A tool that cannot run exits 1, not 2
    Given a crate in "."
    And the change is committed
    And "Cargo.toml" is written as:
      """
      [package]
      name = 5
      """
    And a line is appended to "src/lib.rs"
    When I run the hook "harness stop-hook"
    Then the exit code is 1
    And the stderr starts with "stop-hook: Lint could not run: clippy exited 101: "
    And the output does not contain "stop-hook failed"

  Scenario: The PostToolUse hook formats the edited file and asks for a re-read
    Given a crate in "."
    And "src/messy.rs" is written as:
      """
      pub fn messy()->i32{2}
      """
    And the hook input names "src/messy.rs"
    When I run the hook "harness post-edit --hook"
    Then the hook exits 0 with stdout:
      """
      {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"harness: reformatted src/messy.rs; re-read it before editing it again"}}
      """
    And "src/messy.rs" reads:
      """
      pub fn messy() -> i32 {
          2
      }
      """
    When I run the hook "harness post-edit --hook"
    Then the hook exits 0 silently
    Given the hook input names a file outside the crate
    When I run the hook "harness post-edit --hook"
    Then the hook exits 0 silently
    Given the hook input is '{not json'
    When I run the hook "harness post-edit --hook"
    Then the hook exits 0 silently

  Scenario: Post-edit formats changed files, never an untouched child module
    Given a crate in "."
    And "src/lib.rs" is written as:
      """
      mod other;
      pub fn helper()->i32{1}
      """
    And "src/other.rs" is written as:
      """
      pub fn other()->i32{2}
      """
    And the base is committed on main
    And a line is appended to "src/lib.rs"
    When I run the hook "harness post-edit"
    Then the exit code is 0
    And "src/lib.rs" reads:
      """
      mod other;
      pub fn helper() -> i32 {
          1
      }

      pub const VALUE: i32 = 2;
      """
    And "src/other.rs" reads:
      """
      pub fn other()->i32{2}
      """
