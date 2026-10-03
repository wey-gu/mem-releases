import test from 'node:test';
import assert from 'node:assert/strict';
import { basename } from 'node:path';
import { reconcileDraftAssets } from './upload-draft-release-assets.mjs';

function fixture({ assets = [], isDraft = true, failFirstUpload = false,
  uploadDigest } = {}) {
  const state = { assets: [...assets], isDraft, uploads: [], failFirstUpload };
  const gh = (args) => {
    if (args[1] === 'view') return JSON.stringify(state);
    if (args[1] === 'upload') {
      const file = args.at(-1);
      const name = basename(file);
      if (state.failFirstUpload) {
        state.failFirstUpload = false;
        state.assets.push({ name, digest: 'sha256:one' });
        throw new Error('concurrent upload');
      }
      if (state.assets.some((asset) => asset.name === name)) {
        throw new Error('asset already exists');
      }
      state.uploads.push(name);
      state.assets.push({ name, digest: uploadDigest ??
        (name === 'one.dmg' ? 'sha256:one' : 'sha256:two') });
      return '';
    }
    throw new Error(`unexpected gh call: ${args}`);
  };
  return { state, gh };
}

const digestFile = async (file) => basename(file) === 'one.dmg'
  ? 'sha256:one' : 'sha256:two';
const options = (gh, files = ['/tmp/one.dmg', '/tmp/two.dmg']) => ({
  tag: 'v0.10.94', repo: 'wey-gu/mem-releases', files, gh, digestFile,
});

test('partial draft retry skips identical bytes and uploads only missing assets', async () => {
  const { state, gh } = fixture({ assets: [{ name: 'one.dmg', digest: 'sha256:one' }] });
  await reconcileDraftAssets(options(gh));
  assert.deepEqual(state.uploads, ['two.dmg']);
});

test('different or missing digest fails before any upload', async () => {
  for (const digest of ['sha256:other', null]) {
    const { state, gh } = fixture({ assets: [{ name: 'one.dmg', digest }] });
    await assert.rejects(reconcileDraftAssets(options(gh)), /SHA-256 differs/);
    assert.deepEqual(state.uploads, []);
  }
});

test('published release is immutable even when asset is absent', async () => {
  const { state, gh } = fixture({ isDraft: false });
  await assert.rejects(reconcileDraftAssets(options(gh)), /published/);
  assert.deepEqual(state.uploads, []);
});

test('identical concurrent upload is accepted without clobber', async () => {
  const { state, gh } = fixture({ failFirstUpload: true });
  await reconcileDraftAssets(options(gh, ['/tmp/one.dmg']));
  assert.deepEqual(state.uploads, []);
  assert.equal(state.assets[0].digest, 'sha256:one');
});

test('duplicate local asset names are refused', async () => {
  const { gh } = fixture();
  await assert.rejects(reconcileDraftAssets(options(gh,
    ['/tmp/one.dmg', '/other/one.dmg'])), /duplicate local asset names/);
});

test('preflight checks existing draft digests without uploading missing assets', async () => {
  const { state, gh } = fixture({ assets: [{ name: 'one.dmg', digest: 'sha256:one' }] });
  await reconcileDraftAssets({ ...options(gh), checkOnly: true });
  assert.deepEqual(state.uploads, []);
  state.assets[0].digest = 'sha256:different';
  await assert.rejects(reconcileDraftAssets({ ...options(gh), checkOnly: true }),
    /SHA-256 differs/);
});

test('uploaded asset is read back and checked by digest', async () => {
  const { gh } = fixture({ uploadDigest: 'sha256:wrong' });
  await assert.rejects(reconcileDraftAssets(options(gh, ['/tmp/one.dmg'])),
    /uploaded asset SHA-256 does not match/);
});
