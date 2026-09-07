Feature: Python template workspace
  A clean clone must become an agent-ready workspace through one deterministic command.
  Repeated and offline runs must preserve the exact managed state.

  Scenario: Import src package
    Given a fresh python environment
    When I import src
    Then no exception is raised

  Scenario: Fresh workspace converges with locked dependencies
    Given an isolated clean Python template repository
    When I run the Python workspace target online
    Then the workspace command succeeds
    And the workspace operations run in order:
      | operation                           |
      | preflight python                    |
      | install python                      |
      | exec python -- uv sync --locked     |
      | sync-skills                         |
      | install-hooks                       |
      | verify python                       |
      | exec python -- uv run --frozen --no-sync harness check |
      | verify python                       |

  Scenario: Warm workspace is reusable online and offline
    Given an isolated warm Python template repository
    When I rerun the Python workspace target online and bootstrap offline
    Then both workspace commands succeed
    And online dependencies use "uv sync --locked"
    And offline dependencies use "uv sync --locked --offline"
    And the managed workspace snapshot is unchanged

  Scenario: Cold offline setup stops before workspace mutation
    Given an isolated cold offline Python template repository
    When I run the Python workspace target offline
    Then the workspace command fails during tool installation
    And neither hooks nor skills are modified

  Scenario: An unmanaged hook stops setup before downloads or mutation
    Given an isolated Python template repository with an unmanaged hook
    When I run the Python workspace target online
    Then the workspace command fails during preflight
    And no tools are downloaded
    And neither hooks nor skills are modified
