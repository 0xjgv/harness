Feature: CRAP gate is advisory by default, enforceable on demand
  The crap command surfaces high-risk functions (complex + undertested)
  without blocking CI unless --enforce is passed.

  # Unlike the python/bun/go templates, this template's harness tries to
  # auto-generate `target/llvm-cov/lcov.info` via `cargo llvm-cov` when the
  # artifact is missing. From an isolated tmp dir without Cargo.toml that
  # generation fails with "could not produce ..." — see scenario 3.

  Scenario: Advisory mode exits 0 when offenders exist
    Given a coverage artifact for a high-CCN, zero-coverage function
    When I run "harness crap --max=0"
    Then the exit code is 0
    And the output contains "(advisory)"

  Scenario: Enforce mode exits 1 when offenders exist
    Given a coverage artifact for a high-CCN, zero-coverage function
    When I run "harness crap --max=0 --enforce"
    Then the exit code is 1
    And the output does not contain "(advisory)"

  Scenario: Missing coverage artifact fails to generate one
    Given no coverage artifact
    When I run "harness crap"
    Then the exit code is 1
    And the output contains "could not produce"
