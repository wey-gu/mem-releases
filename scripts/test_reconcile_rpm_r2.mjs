import test from 'node:test';
import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';
import { reconcileRpmR2 } from './reconcile-rpm-r2.mjs';

const bytes = Buffer.from('validated RC RPM');
const digest = `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
const missing = () => Promise.reject({ $metadata: { httpStatusCode: 404 } });

function fixture(existing) {
  const state = { current: existing, puts: [] };
  return { state, options: {
    version: '0.10.94', file: '/tmp/Nowledge.Mem-0.10.94-1.x86_64.rpm',
    digestFile: async () => digest,
    head: async () => state.current === undefined ? missing() : {},
    get: async () => Readable.from([state.current]),
    put: async (key) => { state.puts.push(key); state.current = bytes; },
  } };
}

test('absent RPM is uploaded once and read back', async () => {
  const { state, options } = fixture(undefined);
  await reconcileRpmR2(options);
  assert.deepEqual(state.puts, ['app/0.10.94/x86_64-unknown-linux-gnu.rpm']);
});

test('matching partial RPM promotion resumes without overwriting', async () => {
  const { state, options } = fixture(bytes);
  await reconcileRpmR2(options);
  assert.deepEqual(state.puts, []);
});

test('different existing R2 bytes fail before upload', async () => {
  const { state, options } = fixture(Buffer.from('different'));
  await assert.rejects(reconcileRpmR2(options), /existing R2 SHA-256 differs/);
  assert.deepEqual(state.puts, []);
});

test('R2 permission error fails closed', async () => {
  const { options } = fixture(undefined);
  options.head = async () => { throw { $metadata: { httpStatusCode: 403 } }; };
  await assert.rejects(reconcileRpmR2(options), /R2 status unknown/);
});
