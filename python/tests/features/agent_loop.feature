Feature: The stop hook reports what this change made worse, and says so out loud
  A Stop hook fires after every agent turn. Reporting the whole tree's standing
  complexity there hands the model the same pre-existing table every time and asks it
  to fix code the change never touched; reporting nothing but a gate name tells it a
  gate is red without telling it what to fix. So the complexity gate here is a delta —
  over the limit now, and new or worse than the base version — and the findings ride
  out on stderr, which is the only stream Claude Code feeds back to the model.

  Scenario: A function this change introduced over the limit blocks the stop
    Given a git project whose committed code is under every complexity limit
    And the change adds a function with CCN 16 to src/
    When I run "harness stop-hook"
    Then the exit code is 2
    And the output contains "CCN new→16"
    And the output contains "stop-hook failed: Complexity delta (lizard)"

  Scenario: A pre-existing complex function this change did not touch does not
    Given a git project whose committed code already has a function with CCN 16
    And the change appends a comment to that file
    When I run "harness stop-hook"
    Then the exit code is 0
    And the output does not contain "CCN"
