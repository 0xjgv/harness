Feature: Crate exposes its name
  The library crate must expose a non-empty NAME constant.
  This smoke scenario proves cucumber + step definitions are wired end-to-end.

  Scenario: Read the crate name
    Given a fresh crate handle
    When I read the crate name
    Then the name is not empty
