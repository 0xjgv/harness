import assert from 'node:assert/strict';
import { Given, Then, When } from '@cucumber/cucumber';

interface SmokeWorld {
  error: unknown;
  module: unknown;
}

Given('a fresh runtime', function (this: SmokeWorld) {
  this.error = null;
  this.module = null;
});

When('I import src', async function (this: SmokeWorld) {
  try {
    this.module = await import('../../../src/index');
  } catch (exc) {
    this.error = exc;
  }
});

Then('no exception is raised', function (this: SmokeWorld) {
  assert.equal(this.error, null, `unexpected error: ${this.error}`);
  assert.notEqual(this.module, null);
});
