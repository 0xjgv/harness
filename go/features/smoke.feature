Feature: Suppressions scanner is wired
  The suppressions scanner is the template's sample library package.
  This smoke scenario proves godog + step definitions are wired end-to-end.

  Scenario: Scan a directory with no suppressions
    Given an empty directory
    When the suppressions scanner runs
    Then it reports zero suppressions
