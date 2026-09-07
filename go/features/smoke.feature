Feature: Go template workspace
  A clean clone must become an agent-ready workspace through one deterministic command.
  Repeated and offline runs must preserve the exact managed state.

  Scenario: Scan a directory with no suppressions
    Given an empty directory
    When the suppressions scanner runs
    Then it reports zero suppressions

  Scenario: Fresh workspace converges with readonly dependencies
    Given an isolated clean Go template repository
    When I run the Go workspace target online
    Then the workspace command succeeds
    And the workspace operations run in order:
      | operation                                                  |
      | preflight go                                               |
      | install go                                                 |
      | exec go -- env GOFLAGS=-mod=readonly go mod download       |
      | sync-skills                                                |
      | install-hooks                                              |
      | verify go                                                  |
      | exec go -- go run -mod=readonly harness.go check           |
      | verify go                                                  |

  Scenario: Warm workspace is reusable online and offline
    Given an isolated warm Go template repository
    When I rerun the Go workspace target online and bootstrap offline
    Then both workspace commands succeed
    And dependencies use "env GOFLAGS=-mod=readonly go mod download"
    And offline dependency restoration disables the Go proxy
    And the managed workspace snapshot is unchanged

  Scenario: Cold offline setup stops before workspace mutation
    Given an isolated cold offline Go template repository
    When I run the Go workspace target offline
    Then the workspace command fails during tool installation
    And neither hooks nor skills are modified

  Scenario: An unmanaged hook stops setup before downloads or mutation
    Given an isolated Go template repository with an unmanaged hook
    When I run the Go workspace target online
    Then the workspace command fails during preflight
    And no tools are downloaded
    And neither hooks nor skills are modified
