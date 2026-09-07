Feature: The runner reports bun runtime drift without failing
  package.json's `packageManager` field pins the bun runtime, the last input the
  lockfile cannot hold. The runner reports drift at startup and never fails on
  it — an adopting repo running another bun still gets a working harness.

  Scenario: A mismatched runtime warns and still runs the command
    Given a project pinned to bun "0.0.1"
    When I run "harness clean"
    Then the exit code is 0
    And the output contains "does not match pinned bun@0.0.1"

  Scenario: A matching runtime says nothing
    Given a project pinned to the running bun version
    When I run "harness clean"
    Then the exit code is 0
    And the output does not contain "does not match pinned"

  Scenario: A matching runtime is reported under --verbose
    Given a project pinned to the running bun version
    When I run "harness clean --verbose"
    Then the exit code is 0
    And the output contains "matches pinned bun@"

  Scenario: A pin that names bun but does not parse is called out
    Given a project pinned to bun "^1.4"
    When I run "harness clean"
    Then the exit code is 0
    And the output contains "Unreadable bun pin"

  Scenario: An unpinned project says nothing
    Given a project with no packageManager field
    When I run "harness clean --verbose"
    Then the exit code is 0
    And the output does not contain "pinned bun@"
