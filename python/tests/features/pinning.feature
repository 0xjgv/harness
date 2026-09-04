Feature: The uvx fallback runs the locked tool version
  When a tool is not installed in `.venv`, the harness falls back to `uvx`. The
  lock file holds the exact release `uv run` would have used, so the fallback pins
  that release instead of floating to whatever the index serves today. A missing
  lock costs the pin, never the run.

  Scenario: A locked tool is pinned in the uvx fallback
    Given a project with a uvx stub, no .venv, and a uv.lock pinning lizard to 1.22.2
    When I run "harness complexity --verbose"
    Then the exit code is 0
    And the output contains "uvx --from lizard==1.22.2 lizard"

  Scenario: A missing lock falls back to the unpinned tool without failing
    Given a project with a uvx stub, no .venv, and no uv.lock
    When I run "harness complexity --verbose"
    Then the exit code is 0
    And the output contains "uvx lizard"
    And the output does not contain "--from"
