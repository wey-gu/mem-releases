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
      if (state.assets.some((asset) => asset.name === name.replace(/ /g, '.'))) {
        throw new Error('asset already exists');
      }
      state.uploads.push(name);
      // Like GitHub: a stored asset name has its spaces replaced by dots.
      state.assets.push({ name: name.replace(/ /g, '.'), digest: uploadDigest ??
        (['one.dmg', 'Nowledge Mem_one.dmg'].includes(name) ? 'sha256:one' : 'sha256:two') });
      return '';
    }
    throw new Error(`unexpected gh call: ${args}`);
  };
  return { state, gh };
}

const digestFile = async (file) => ['one.dmg', 'Nowledge Mem_one.dmg'].includes(basename(file))
  ? 'sha256:one' : 'sha256:two';
const options = (gh, files = ['/tmp/one.dmg', '/tmp/two.dmg']) => ({
  tag: 'v0.10.94', repo: 'wey-gu/mem-releases', files, gh, digestFile,
  sleep: async () => {},
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

test('uploaded asset is read back and checked by digest', async () => {
  const { gh } = fixture({ uploadDigest: 'sha256:wrong' });
  await assert.rejects(reconcileDraftAssets(options(gh, ['/tmp/one.dmg'])),
    /uploaded asset SHA-256 does not match/);
});

test('a digest GitHub has not computed yet is awaited, not read as a mismatch', async () => {
  // 0.10.108 (run 40249609661): the upload was intact, but the read right
  // after it returned the asset without a digest.
  const { state, gh } = fixture({ uploadDigest: '' });
  let views = 0;
  const lagging = (args) => {
    const out = gh(args);
    if (args[1] === 'view') {
      views += 1;
      // The digest appears on the third read after the upload.
      if (views >= 4) for (const asset of state.assets) asset.digest ||= 'sha256:one';
    }
    return out;
  };
  let sleeps = 0;
  await reconcileDraftAssets({ ...options(lagging, ['/tmp/one.dmg']), sleep: async () => { sleeps += 1; } });
  assert.deepEqual(state.uploads, ['one.dmg']);
  assert.ok(sleeps >= 1);
});

test('a digest that never appears still fails after the wait', async () => {
  const { gh } = fixture({ uploadDigest: '' });
  let sleeps = 0;
  await assert.rejects(
    reconcileDraftAssets({ ...options(gh, ['/tmp/one.dmg']), sleep: async () => { sleeps += 1; } }),
    /does not match/);
  assert.equal(sleeps, 23);
});

test('a file name with a space matches the dotted name GitHub stores', async () => {
  // 0.10.108 (run 40249609661): "Nowledge Mem_0.10.108_aarch64.dmg" is
  // stored as "Nowledge.Mem_0.10.108_aarch64.dmg".
  const { state, gh } = fixture();
  await reconcileDraftAssets(options(gh, ['/tmp/Nowledge Mem_one.dmg']));
  assert.deepEqual(state.uploads, ['Nowledge Mem_one.dmg']);
  assert.equal(state.assets[0].name, 'Nowledge.Mem_one.dmg');
  // A rerun finds the stored asset and skips it instead of uploading again.
  await reconcileDraftAssets(options(gh, ['/tmp/Nowledge Mem_one.dmg']));
  assert.deepEqual(state.uploads, ['Nowledge Mem_one.dmg']);
});
