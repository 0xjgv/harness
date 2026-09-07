Feature: Pinned golangci-lint version
  The harness pins the golangci-lint release its lint config is tuned for.
  Every gate that shells out to golangci-lint reads the installed version from
  the tool's own banner first and warns on a mismatch — never fails, because
  adopters legitimately run their own version.

  Scenario: A mismatching installed version warns without failing
    Given golangci-lint on PATH reports version "9.9.9"
    When I run "harness lint" against that golangci-lint
    Then the shimmed exit code is 0
    And the shimmed output contains "golangci-lint 9.9.9 installed, 2.13.2 pinned"

  Scenario: The pinned version is silent
    Given golangci-lint on PATH reports version "2.13.2"
    When I run "harness lint" against that golangci-lint
    Then the shimmed exit code is 0
    And the shimmed output does not contain "pinned"

  Scenario: A go-install build of the pinned version is silent
    Given golangci-lint on PATH reports version "v2.13.2"
    When I run "harness lint" against that golangci-lint
    Then the shimmed exit code is 0
    And the shimmed output does not contain "pinned"
