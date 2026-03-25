import { expect, test } from 'bun:test';
import * as src from '../src/index';

test('module is importable', () => {
  expect(src).toBeDefined();
});
