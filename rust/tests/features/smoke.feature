Feature: Rust template workspace
  A clean clone must become an agent-ready workspace through one deterministic command.
  Repeated and offline runs must preserve the exact managed state.

  Scenario: Read the crate name
    Given a fresh crate handle
    When I read the crate name
    Then the name is not empty

  Scenario: Fresh workspace converges with locked dependencies
    Given an isolated clean Rust template repository
    When I run the Rust workspace target online
    Then the workspace command succeeds
    And the workspace operations run in order:
      | operation                                                            |
      | preflight rust                                                       |
      | install rust                                                         |
      | exec rust -- cargo fetch --locked                                    |
      | exec rust -- cargo build --locked                                    |
      | sync-skills                                                          |
      | install-hooks                                                        |
      | verify rust                                                          |
      | exec rust -- cargo run --quiet --locked --bin harness -- check        |
      | verify rust                                                          |

  Scenario: Warm workspace is reusable online and offline
    Given an isolated warm Rust template repository
    When I rerun the Rust workspace target online and bootstrap offline
    Then both workspace commands succeed
    And dependencies use locked Cargo fetch and build commands
    And offline dependency restoration disables Cargo networking
    And the managed workspace snapshot is unchanged

  Scenario: Cold offline setup stops before workspace mutation
    Given an isolated cold offline Rust template repository
    When I run the Rust workspace target offline
    Then the workspace command fails during tool installation
    And neither hooks nor skills are modified

  Scenario: An unmanaged hook stops setup before downloads or mutation
    Given an isolated Rust template repository with an unmanaged hook
    When I run the Rust workspace target online
    Then the workspace command fails during preflight
    And no tools are downloaded
    And neither hooks nor skills are modified
