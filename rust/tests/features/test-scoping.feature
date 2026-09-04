Feature: The test gate scopes to the changed modules
  check and pre-commit run only the tests that map to changed files —
  `src/foo/bar.rs` becomes the libtest filter `foo::bar`, `tests/<name>.rs`
  becomes `--test <name>`, a `.feature` becomes the cucumber target. An empty
  change set warns and skips instead of widening to the whole tree, and
  `--all` opts out of scoping entirely. ci always runs the whole suite.

  # Scenarios run from an isolated tmp dir with no git repository and no
  # Cargo.toml, so the change set is empty and the cargo steps fail — the
  # assertions are on the test gate's own line, not on the overall verdict.

  Scenario: An empty change set skips the test gate instead of widening
    Given no coverage artifact
    When I run "harness check"
    Then the exit code is 1
    And the output contains "Tests: no changed Rust or .feature files"

  Scenario: --all opts out of scoping and runs the whole suite
    Given no coverage artifact
    When I run "harness check --all"
    Then the exit code is 1
    And the output does not contain "no changed Rust or .feature files"
