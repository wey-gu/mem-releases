import test from 'node:test';
import assert from 'node:assert/strict';
import { reconcileRpmReleaseAsset } from './reconcile-rpm-release-asset.mjs';

function setup(assets = []) {
  const state = { assets: [...assets], uploads: [] };
  const gh = (args) => {
    if (args[1] === 'view') return JSON.stringify({ isDraft: false, assets: state.assets });
    if (args[1] === 'upload') {
      state.uploads.push(args.at(-1));
      state.assets.push({ name: 'mem-0.10.94.rpm', digest: 'sha256:expected' });
      return '';
    }
    throw new Error('unexpected gh call');
  };
  const options = { tag: 'v0.10.94', repo: 'wey-gu/mem-releases',
    file: '/tmp/mem-0.10.94.rpm', gh,
    digestFile: async () => 'sha256:expected' };
  return { state, options };
}

test('published GA can receive an absent RPM once', async () => {
  const { state, options } = setup();
  await reconcileRpmReleaseAsset(options);
  assert.deepEqual(state.uploads, ['/tmp/mem-0.10.94.rpm']);
});

test('matching published RPM is idempotent', async () => {
  const { state, options } = setup([{ name: 'mem-0.10.94.rpm', digest: 'sha256:expected' }]);
  await reconcileRpmReleaseAsset(options);
  assert.deepEqual(state.uploads, []);
});

test('different published RPM fails in preflight before public mutation', async () => {
  const { state, options } = setup([{ name: 'mem-0.10.94.rpm', digest: 'sha256:other' }]);
  await assert.rejects(reconcileRpmReleaseAsset({ ...options, checkOnly: true }),
    /SHA-256 differs/);
  assert.deepEqual(state.uploads, []);
});

test('another RPM filename on GA fails before a second RPM is added', async () => {
  const { state, options } = setup([{ name: 'other-0.10.94.rpm', digest: 'sha256:expected' }]);
  await assert.rejects(reconcileRpmReleaseAsset({ ...options, checkOnly: true }),
    /conflicting or duplicate GA RPM/);
  assert.deepEqual(state.uploads, []);
});

test('check-only does not attach a missing RPM', async () => {
  const { state, options } = setup();
  await reconcileRpmReleaseAsset({ ...options, checkOnly: true });
  assert.deepEqual(state.uploads, []);
});
