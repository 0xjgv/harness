Feature: Package is importable
  The src module must be importable from a fresh runtime.
  This smoke scenario proves cucumber + step defs are wired end-to-end.

  Scenario: Import src module
    Given a fresh runtime
    When I import src
    Then no exception is raised
