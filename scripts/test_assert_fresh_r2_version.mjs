import test from 'node:test';
import assert from 'node:assert/strict';
import { assertFreshR2Version } from './assert-fresh-r2-version.mjs';

const missing = () => Promise.reject({ $metadata: { httpStatusCode: 404 } });

test('core GA checks five target keys; direct GA checks RPM too', async () => {
  const keys = [];
  const head = (key) => { keys.push(key); return missing(); };
  await assertFreshR2Version({ version: '0.10.94', head });
  assert.equal(keys.length, 5);
  assert.equal(keys.some((key) => key.endsWith('.rpm')), false);
  keys.length = 0;
  await assertFreshR2Version({ version: '0.10.94', includeRpm: true, head });
  assert.equal(keys.length, 6);
  assert.equal(keys.some((key) => key.endsWith('.rpm')), true);
});

test('any existing R2 object blocks a normal GA rerun', async () => {
  await assert.rejects(assertFreshR2Version({ version: '0.10.94',
    head: () => Promise.resolve({}) }), /already exists/);
});

test('R2 permission or transient errors fail closed', async () => {
  await assert.rejects(assertFreshR2Version({ version: '0.10.94',
    head: () => Promise.reject({ $metadata: { httpStatusCode: 403 } }) }),
  /status unknown/);
});
