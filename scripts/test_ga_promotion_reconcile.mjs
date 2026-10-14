import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { observeR2, verifyR2 } from './reconcile-ga-r2.mjs';
import { readPauseRoutes } from './read-pause-routes.mjs';
import { ensureTag, observeTags, peelTag } from './reconcile-source-tag.mjs';
import { selectPublishReceipt } from './find-authority-receipt.mjs';
import { verifyDockerPromotion } from './verify-docker-promotion.mjs';

const sha256 = (value) => `sha256:${createHash('sha256').update(value).digest('hex')}`;
const missing = () => Promise.reject({ $metadata: { httpStatusCode: 404 } });

function r2(objects) {
  return {
    head: async (key) => (key in objects ? {} : missing()),
    get: async (key) => Readable.from([Buffer.from(objects[key])]),
  };
}

test('R2 observation hashes only existing objects and reports absence', async () => {
  const state = await observeR2({ version: '0.10.102', targets: ['a.dmg', 'b.deb'],
    ...r2({ 'app/0.10.102/a.dmg': 'bytes' }) });
  assert.deepEqual(state, { 'a.dmg': sha256('bytes'), 'b.deb': 'absent' });
});

test('an unreadable R2 object is never treated as absent', async () => {
  await assert.rejects(observeR2({ version: '0.10.102', targets: ['a.dmg'],
    head: async () => { throw { $metadata: { httpStatusCode: 403 } }; }, get: async () => null }),
  /R2 status unknown/);
});

test('R2 verification requires the exact expected bytes', async () => {
  const io = r2({ 'app/0.10.102/a.dmg': 'bytes' });
  await verifyR2({ version: '0.10.102', expected: { 'a.dmg': sha256('bytes') }, ...io });
  await assert.rejects(verifyR2({ version: '0.10.102', expected: { 'a.dmg': sha256('other') }, ...io }),
    /R2 holds sha256:.*, expected/);
  await assert.rejects(verifyR2({ version: '0.10.102', expected: { 'b.deb': sha256('x') }, ...io }),
    /R2 holds absent/);
});

function routesFetcher(result, extra = {}) {
  return async () => ({ status: 200, json: async () => ({ success: true, result, ...extra }) });
}

test('pause route observation returns only the pause Worker patterns', async () => {
  const patterns = await readPauseRoutes({ zoneId: 'a'.repeat(32), apiToken: 't', fetcher: routesFetcher([
    { pattern: 'backbone-mem.nowledge.co/latest*', script: 'nowledge-mem-upgrade-pause' },
    { pattern: 'download-mem.nowledge.co/apt/*', script: 'nowledge-mem-upgrade-pause' },
    { pattern: 'mem.nowledge.co/*', script: 'website' },
  ]) });
  assert.deepEqual(patterns, ['backbone-mem.nowledge.co/latest*', 'download-mem.nowledge.co/apt/*']);
  await assert.rejects(readPauseRoutes({ zoneId: 'a'.repeat(32), apiToken: 't',
    fetcher: routesFetcher([], { result_info: { total_pages: 2 } }) }), /pagination/);
  await assert.rejects(readPauseRoutes({ zoneId: 'a'.repeat(32), apiToken: 't',
    fetcher: async () => ({ status: 403, json: async () => ({}) }) }), /expected 200, got 403/);
});

// In-memory GitHub git refs API: refs/tags/<name> -> { type, sha }, tag objects.
function fakeGitHub({ refs = {}, tags = {}, gitlinks = {}, refStatus } = {}) {
  const state = { refs: { ...refs }, tags: { ...tags }, writes: [] };
  let counter = 0;
  const api = async (method, path, body) => {
    let match = path.match(/^repos\/([^/]+\/[^/]+)\/git\/ref\/tags\/(.+)$/);
    if (method === 'GET' && match) {
      const ref = state.refs[`${match[1]}:${decodeURIComponent(match[2])}`];
      return ref ? { status: 200, json: { object: ref } } : { status: 404, json: null };
    }
    match = path.match(/^repos\/([^/]+\/[^/]+)\/git\/tags\/(.+)$/);
    if (method === 'GET' && match) {
      const tag = state.tags[match[2]];
      return tag ? { status: 200, json: { object: tag } } : { status: 404, json: null };
    }
    match = path.match(/^repos\/([^/]+\/[^/]+)\/contents\/([^?]+)\?ref=(.+)$/);
    if (method === 'GET' && match) {
      const sha = gitlinks[`${match[1]}:${match[2]}@${match[3]}`];
      return sha ? { status: 200, json: { type: 'submodule', sha } } : { status: 404, json: null };
    }
    match = path.match(/^repos\/([^/]+\/[^/]+)\/git\/tags$/);
    if (method === 'POST' && match) {
      const sha = String(++counter).padStart(40, 'f');
      state.tags[sha] = { type: 'commit', sha: body.object };
      state.writes.push(['tag', match[1], body.tag]);
      return { status: 201, json: { sha } };
    }
    match = path.match(/^repos\/([^/]+\/[^/]+)\/git\/refs$/);
    if (method === 'POST' && match) {
      const key = `${match[1]}:${body.ref.replace('refs/tags/', '')}`;
      if (refStatus) return { status: refStatus, json: null };
      if (state.refs[key]) return { status: 422, json: null };
      state.refs[key] = { type: state.tags[body.sha] ? 'tag' : 'commit', sha: body.sha };
      state.writes.push(['ref', match[1], body.ref]);
      return { status: 201, json: {} };
    }
    throw new Error(`unexpected ${method} ${path}`);
  };
  return { state, api };
}

const MEM_SHA = 'a'.repeat(40);
const OTHER = 'c'.repeat(40);

test('annotated RC tags are peeled to their commit', async () => {
  const { api } = fakeGitHub({ refs: { 'o/r:v1.0.0-rc1': { type: 'tag', sha: 'e'.repeat(40) } },
    tags: { ['e'.repeat(40)]: { type: 'commit', sha: MEM_SHA } } });
  assert.equal(await peelTag({ api, repo: 'o/r', tag: 'v1.0.0-rc1' }), MEM_SHA);
  assert.equal(await peelTag({ api, repo: 'o/r', tag: 'v1.0.0' }), null);
});

test('a missing source tag is created once at the exact SHA and read back', async () => {
  const { state, api } = fakeGitHub();
  assert.equal(await ensureTag({ api, repo: 'o/r', tag: 'v1.0.0', sha: MEM_SHA, annotated: true,
    message: 'Nowledge Mem 1.0.0' }), 'created');
  assert.equal(await peelTag({ api, repo: 'o/r', tag: 'v1.0.0' }), MEM_SHA);
  assert.equal(await ensureTag({ api, repo: 'o/r', tag: 'v1.0.0', sha: MEM_SHA, annotated: true }), 'identical');
  assert.equal(state.writes.length, 2, 'the second call must not write');
  assert.equal(await ensureTag({ api, repo: 'o/r', tag: 'cli-publish-v1.0.0', sha: MEM_SHA,
    annotated: false }), 'created');
  assert.equal(state.refs['o/r:cli-publish-v1.0.0'].type, 'commit');
});

test('a tag at another commit is a conflict and is never moved', async () => {
  const { state, api } = fakeGitHub({ refs: { 'o/r:v1.0.0': { type: 'commit', sha: OTHER } } });
  await assert.rejects(ensureTag({ api, repo: 'o/r', tag: 'v1.0.0', sha: MEM_SHA, annotated: true }),
    /exists at c+, not the RC-recorded a+; refusing to move it/);
  assert.deepEqual(state.writes, []);
});

test('a failed ref write fails loudly', async () => {
  const { api } = fakeGitHub({ refStatus: 403 });
  await assert.rejects(ensureTag({ api, repo: 'o/r', tag: 'v1.0.0', sha: MEM_SHA, annotated: false }),
    /ref creation failed with HTTP 403/);
});

test('tag observation reads mem, community and the recorded community gitlink', async () => {
  const community = 'b'.repeat(40);
  const { api } = fakeGitHub({
    refs: {
      'nowledge-co/mem:v1.0.0-rc1': { type: 'commit', sha: MEM_SHA },
      'nowledge-co/community:v1.0.0-rc1': { type: 'commit', sha: community },
      'nowledge-co/mem:cli-publish-v1.0.0': { type: 'commit', sha: MEM_SHA },
    },
    gitlinks: { [`nowledge-co/mem:community@${MEM_SHA}`]: community },
  });
  assert.deepEqual(await observeTags({ memApi: api, communityApi: api, rc: '1.0.0-rc1', ga: '1.0.0' }), {
    mem: { rc: MEM_SHA, ga: null, cli: MEM_SHA },
    community: { rc: community, ga: null },
    communityGitlink: community,
  });
});

function receiptTree(entries) {
  const root = mkdtempSync(join(tmpdir(), 'receipts-'));
  for (const { runId, operationId, receipt, manifest = true } of entries) {
    const dir = join(root, runId, `desktop-update-receipt-${operationId}`);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'operation-receipt.json'), JSON.stringify({ operation_id: operationId, ...receipt }));
    if (manifest) writeFileSync(join(dir, 'candidate-manifest.json'), '{}');
  }
  return root;
}

const OP1 = '11111111-1111-4111-8111-111111111111';
const OP2 = '22222222-2222-4222-8222-222222222222';
const publish = { action: 'publish', mode: 'published', candidate_version: '1.0.0', revision: 17,
  stable_version: '0.9.9', actor: 'hawkingrei' };

test('the exact publish receipt is selected for a resumed unpause', () => {
  const root = receiptTree([
    { runId: '100', operationId: OP1, receipt: publish },
    { runId: '101', operationId: OP2, receipt: { ...publish, action: 'prepare', mode: 'draft', revision: 16 } },
  ]);
  assert.deepEqual(selectPublishReceipt({ root, ga: '1.0.0', revision: 17, stable: '0.9.9' }),
    { runId: '100', operationId: OP1, actor: 'hawkingrei', revision: 17 });
});

test('missing, ambiguous or mismatched receipts fail closed', () => {
  const none = receiptTree([{ runId: '100', operationId: OP1, receipt: { ...publish, revision: 16 } }]);
  assert.throws(() => selectPublishReceipt({ root: none, ga: '1.0.0', revision: 17, stable: '0.9.9' }),
    /found 0/);
  const twice = receiptTree([
    { runId: '100', operationId: OP1, receipt: publish },
    { runId: '102', operationId: OP2, receipt: publish },
  ]);
  assert.throws(() => selectPublishReceipt({ root: twice, ga: '1.0.0', revision: 17, stable: '0.9.9' }),
    /found 2/);
  const noManifest = receiptTree([{ runId: '100', operationId: OP1, receipt: publish, manifest: false }]);
  assert.throws(() => selectPublishReceipt({ root: noManifest, ga: '1.0.0', revision: 17, stable: '0.9.9' }),
    /found 0/);
});

function registry(entries) {
  return async (ref) => {
    if (!(ref in entries)) throw new Error(`${ref}: not found`);
    return entries[ref];
  };
}

const d = (n) => `sha256:${String(n).repeat(64)}`;
const multi = (digest, archs = ['amd64', 'arm64']) => ({ digest,
  manifests: [...archs.map((architecture) => ({ platform: { os: 'linux', architecture } })),
    { platform: { os: 'unknown', architecture: 'unknown' } }] });

test('Docker promotion is verified by digest and by both CPU platforms', async () => {
  const images = {
    'docker.io/nowledgelabs/mem:1.0.0-rc1': multi(d(1)),
    'docker.io/nowledgelabs/mem:1.0.0': multi(d(1)),
    'docker.io/nowledgelabs/mem-updater:1.0.0-rc1': multi(d(2)),
    'docker.io/nowledgelabs/mem-updater:1.0.0': multi(d(2)),
    'docker.io/nowledgelabs/mem:1.0.0-rc1-cuda': { digest: d(3) },
    'docker.io/nowledgelabs/mem:1.0.0-cuda': { digest: d(3) },
  };
  const ok = await verifyDockerPromotion({ rc: '1.0.0-rc1', ga: '1.0.0', gpu: ['cuda'], inspect: registry(images) });
  assert.equal(ok.length, 3);
  await assert.rejects(verifyDockerPromotion({ rc: '1.0.0-rc1', ga: '1.0.0', inspect: registry({
    ...images, 'docker.io/nowledgelabs/mem:1.0.0': multi(d(9)) }) }), /digest sha256:9+ != RC/);
  await assert.rejects(verifyDockerPromotion({ rc: '1.0.0-rc1', ga: '1.0.0', inspect: registry({
    ...images, 'docker.io/nowledgelabs/mem-updater:1.0.0': multi(d(2), ['amd64']) }) }), /lacks linux\/arm64/);
  await assert.rejects(verifyDockerPromotion({ rc: '1.0.0-rc1', ga: '1.0.0', gpu: ['vulkan'],
    inspect: registry(images) }), /not found/);
});
