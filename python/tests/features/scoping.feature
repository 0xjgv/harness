Feature: Scoped gates only ever touch what changed
  fix/format/lint/typecheck resolve one git-derived file list — working tree +
  index + untracked + the diff against the base ref — and skip when it is empty.
  Adopting this harness into a large existing repo must not report that repo's
  pre-existing violations, and must never widen an empty scope to the whole tree.

  Scenario: A clean tree skips the gate instead of linting everything
    Given a git project with a lint tool stub and no changes
    When I run "harness lint"
    Then the exit code is 0
    And the output contains "no changed Python files"
    And the output contains "skipped"
    And the output does not contain "src/untouched.py"

  Scenario: One changed file scopes the gate to that file alone
    Given a git project with a lint tool stub and one changed Python file
    When I run "harness lint --verbose"
    Then the exit code is 0
    And the output contains "src/changed.py"
    And the output does not contain "src/untouched.py"

  Scenario: --all widens the gate to the whole tree on purpose
    Given a git project with a lint tool stub and one changed Python file
    When I run "harness lint --all --verbose"
    Then the exit code is 0
    And the output contains "check ."
    And the output does not contain "src/changed.py"

  Scenario: A pre-existing violation on an untouched line is not reported
    Given a legacy file with a comment appended to its last line
    And the linter reports "E501" on line 1 of src/legacy.py
    When I run "harness lint"
    Then the exit code is 0
    And the output contains "0/1 on changed lines"
    And the output does not contain "E501"

  Scenario: A violation on a line this change wrote is reported
    Given a legacy file with a comment appended to its last line
    And the linter reports "E501" on line 4 of src/legacy.py
    When I run "harness lint"
    Then the exit code is 1
    And the output contains "src/legacy.py:4:1: E501"

  Scenario: A whole-file violation is reported wherever it sits
    Given a legacy file with a comment appended to its last line
    And the linter reports "F401" on line 1 of src/legacy.py
    When I run "harness lint"
    Then the exit code is 1
    And the output contains "src/legacy.py:1:1: F401"

  Scenario: --whole-file keeps the file scope but checks every line
    Given a legacy file with a comment appended to its last line
    And the linter reports "E501" on line 1 of src/legacy.py
    When I run "harness lint --whole-file --verbose"
    Then the exit code is 0
    And the output contains "src/legacy.py"
    And the output does not contain "--output-format=json"
