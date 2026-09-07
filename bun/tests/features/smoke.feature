Feature: Bun template workspace
  A clean clone must become an agent-ready workspace through one deterministic command.
  Repeated and offline runs must preserve the exact managed state.

  Scenario: Import src module
    Given a fresh runtime
    When I import src
    Then no exception is raised

  Scenario: Fresh workspace converges with frozen dependencies
    Given an isolated clean Bun template repository
    When I run the Bun workspace target online
    Then the workspace command succeeds
    And the workspace operations run in order:
      | operation                                  |
      | preflight bun                              |
      | install bun                                |
      | exec bun -- bun install --frozen-lockfile |
      | sync-skills                                |
      | install-hooks                              |
      | verify bun                                 |
      | exec bun -- bun harness.ts check           |
      | verify bun                                 |

  Scenario: Warm workspace is reusable online and offline
    Given an isolated warm Bun template repository
    When I rerun the Bun workspace target online and bootstrap offline
    Then both workspace commands succeed
    And online dependencies use "bun install --frozen-lockfile"
    And offline dependencies use "bun install --frozen-lockfile --offline"
    And the managed workspace snapshot is unchanged

  Scenario: Cold offline setup stops before workspace mutation
    Given an isolated cold offline Bun template repository
    When I run the Bun workspace target offline
    Then the workspace command fails during tool installation
    And neither hooks nor skills are modified

  Scenario: An unmanaged hook stops setup before downloads or mutation
    Given an isolated Bun template repository with an unmanaged hook
    When I run the Bun workspace target online
    Then the workspace command fails during preflight
    And no tools are downloaded
    And neither hooks nor skills are modified
