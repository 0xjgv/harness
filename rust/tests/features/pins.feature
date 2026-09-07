Feature: Pinned cargo tool versions are visible but never blocking
  The runner pins the cargo subcommands CI installs (cargo-audit,
  cargo-llvm-cov, cargo-modules, cargo-mutants). A local install on a
  different version is reported so the drift is visible, but it never
  changes an exit code — adopters run whatever they have installed.

  # The scenarios put a fake `cargo` first on PATH so the reported version is
  # controlled; the shim exits 0 for every other subcommand.

  Scenario: A drifting cargo-mutants install warns without failing
    Given a cargo shim reporting cargo-mutants version "0.0.1"
    When I run "harness mutation"
    Then the exit code is 0
    And the output contains "!= pinned 27.0.0"

  Scenario: An install on the pinned version says nothing
    Given a cargo shim reporting cargo-mutants version "27.0.0"
    When I run "harness mutation"
    Then the exit code is 0
    And the output does not contain "!= pinned"
