Feature: check and pre-commit test only the packages a change touches
  Running the whole suite after every edit does not scale into an existing
  repo, so the local stages map each changed .go file to its package and run
  `go test ./<pkg>/...` for those alone. An empty scope warns and skips — it
  never widens to the whole tree. --all and ci run the whole suite.

  Scenario: Only the changed package's tests run
    Given a project with tests in two packages and a change staged in one
    When I run "harness pre-commit --verbose"
    Then the exit code is 0
    And the output contains "go test ./pkga/..."
    And the output does not contain "./pkgb/..."

  Scenario: A changed package with no tests warns instead of failing
    Given a project with a change staged in a package that has no tests
    When I run "harness pre-commit"
    Then the exit code is 0
    And the output contains "no *_test.go in pkgc"

  Scenario: A change that maps to no package skips instead of widening
    Given a project with a change staged in a build-ignored runner file only
    When I run "harness pre-commit"
    Then the exit code is 0
    And the output contains "no changed Go packages"
    And the output does not contain "go test ./..."

  Scenario: --all widens the gate to the whole suite on purpose
    Given a project with tests in two packages and a change staged in one
    When I run "harness pre-commit --all --verbose"
    Then the exit code is 0
    And the output contains "go test ./..."
