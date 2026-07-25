import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { Given, Then } from '@cucumber/cucumber';

interface RuleWithOptions {
  level: string;
  options?: Record<string, unknown>;
}

type RuleConfig = string | RuleWithOptions;

interface BiomeOverride {
  includes?: string[];
  linter?: {
    rules?: {
      complexity?: {
        noExcessiveCognitiveComplexity?: RuleConfig;
      };
    };
  };
}

interface BiomeConfig {
  linter: {
    rules: {
      style: {
        useErrorCause?: RuleConfig;
      };
      complexity?: {
        noExcessiveCognitiveComplexity?: RuleConfig;
      };
    };
  };
  overrides?: BiomeOverride[];
}

interface BiomePolicyWorld {
  config: BiomeConfig;
}

// biome.json lives at the template root; steps run from tests/features/steps.
const BIOME_JSON = join(import.meta.dir, '..', '..', '..', 'biome.json');

Given("the Bun template's Biome configuration", async function (this: BiomePolicyWorld) {
  const raw = await readFile(BIOME_JSON, 'utf8');
  this.config = JSON.parse(raw) as BiomeConfig;
});

Then('useErrorCause is an error', function (this: BiomePolicyWorld) {
  assert.equal(this.config.linter.rules.style.useErrorCause, 'error');
});

Then(
  'cognitive complexity above {int} is a warning for application code',
  function (this: BiomePolicyWorld, threshold: number) {
    const rule = this.config.linter.rules.complexity?.noExcessiveCognitiveComplexity;
    assert.ok(
      rule !== undefined && typeof rule === 'object',
      `expected noExcessiveCognitiveComplexity to be configured with options, got ${JSON.stringify(rule)}`,
    );
    const ruleOptions = rule as RuleWithOptions;
    assert.equal(ruleOptions.level, 'warn');
    assert.equal(ruleOptions.options?.maxAllowedComplexity, threshold);
  },
);

Then('cognitive complexity is disabled for the harness runner', function (this: BiomePolicyWorld) {
  const override = this.config.overrides?.find((entry) => entry.includes?.includes('harness.ts'));
  assert.ok(override, 'expected a biome.json overrides entry targeting harness.ts');
  assert.equal(override?.linter?.rules?.complexity?.noExcessiveCognitiveComplexity, 'off');
});
