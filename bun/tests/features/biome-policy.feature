Feature: Biome guards maintainable application code
  The Bun template should catch discarded error context and flag code that is
  difficult to understand without duplicating the harness complexity gate.

  Scenario: Recommended application lint rules are enabled
    Given the Bun template's Biome configuration
    Then useErrorCause is an error
    And cognitive complexity above 20 is a warning for application code
    But cognitive complexity is disabled for the harness runner
