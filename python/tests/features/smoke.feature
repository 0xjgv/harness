Feature: Package is importable
  The src package must be importable from a fresh interpreter.
  This smoke scenario proves behave + step defs are wired end-to-end.

  Scenario: Import src package
    Given a fresh python environment
    When I import src
    Then no exception is raised
