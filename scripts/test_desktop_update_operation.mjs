import test from 'node:test';
import assert from 'node:assert/strict';
import { buildPlan, executePlan } from './desktop-update-operation.mjs';

const token = 'test-only-operator-credential-1234567890';
const files = {
  mac: 'aarch64-apple-darwin.dmg', mac_intel: 'x86_64-apple-darwin.dmg',
  win: 'x86_64-pc-windows-msvc.exe', 'linux-appimage': 'x86_64-unknown-linux-gnu.AppImage',
  'linux-deb': 'x86_64-unknown-linux-gnu.deb', 'linux-rpm': 'x86_64-unknown-linux-gnu.rpm',
};
const manifest = { version: '0.10.100', source_sha: 'a'.repeat(40), release_notes: '', pub_date: '2026-10-12T01:00:00Z',
  artifacts: Object.fromEntries(Object.entries(files).map(([p, file]) => [p, { key: `app/0.10.100/${file}`, size: 123, etag: 'exact-etag', sha256: 'b'.repeat(64) }])) };
function input(action, extra = {}) {
  return { action, expected_revision: '11', operation_id: '0ad3cba9-09e5-4e7a-8a07-5b9c83af4cf5', reason: 'Reviewed release delivery',
    expected_stable_version: '0.10.98', expected_candidate_version: action === 'prepare' ? 'none' : '0.10.100',
    manifest, previous_version: '0.10.99', ...extra };
}
function fixture(action, changes = {}) {
  let state = { revision: 11, mode: action === 'prepare' ? 'recovered' : 'draft', stable: { version: '0.10.98' },
    candidate: action === 'prepare' ? null : structuredClone(manifest), ...changes };
  const calls = [], records = [];
  const fetcher = async (url, init) => {
    assert.ok(url.startsWith('https://backbone-mem.nowledge.co/admin/desktop-updates'));
    assert.equal(init.redirect, 'error');
    assert.equal(init.headers.Authorization, `Bearer ${token}`);
    calls.push({ url, method: init.method, body: init.body });
    if (url.includes('/history')) return Response.json({ entries: [] });
    if (init.method === 'GET') return Response.json(state);
    const request = JSON.parse(init.body);
    assert.equal(request.expected_revision, state.revision);
    const mode = { prepare: 'draft', publish: 'published', hold: 'held', stop: 'stopped', recover: 'recovered' }[request.action];
    state = { ...state, revision: state.revision + 1, mode,
      candidate: request.action === 'prepare' ? request.manifest : request.action === 'recover' ? null : state.candidate };
    return Response.json({ ...request, revision: state.revision, mode, actor: 'test-operator' });
  };
  return { token, fetcher, calls, records, record: async (name, body) => records.push({ name, body }),
    verify: async ({ manifest: m, previousVersion }) => {
      assert.deepEqual(m, manifest); assert.equal(previousVersion, '0.10.99');
      return Object.keys(files).map(platform => ({ platform, revision: state.revision }));
    } };
}

test('prepares exact candidate with one POST and preserved stable baseline', async () => {
  const f = fixture('prepare'), plan = buildPlan(input('prepare'));
  await executePlan(plan, f);
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 1);
  const after = f.records.find(r => r.name === 'status-after.json').body;
  assert.equal(after.stable.version, '0.10.98'); assert.deepEqual(after.candidate, manifest);
});
test('publishes exact registered candidate and binds all six native checks to its revision', async () => {
  const f = fixture('publish'); await executePlan(buildPlan(input('publish')), f);
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 1);
  assert.equal(f.records.find(r => r.name === 'native-delivery-receipt.json').body.length, 6);
});
for (const [action, mode, expectedMode] of [['hold', 'published', 'held'], ['stop', 'published', 'stopped'], ['recover', 'held', 'recovered']]) {
  test(`${action} preserves stable identity and reads back its exact transition`, async () => {
    const f = fixture(action, { mode });
    await executePlan(buildPlan(input(action)), f);
    const after = f.records.find(r => r.name === 'status-after.json').body;
    assert.equal(after.stable.version, '0.10.98');
    assert.equal(after.mode, expectedMode);
    assert.equal(after.candidate?.version ?? null, action === 'recover' ? null : '0.10.100');
    assert.equal(f.calls.filter(c => c.method === 'POST').length, 1);
  });
}
test('missing credential fails before any HTTP', async () => {
  const f = fixture('publish');
  await assert.rejects(executePlan(buildPlan(input('publish')), { ...f, token: undefined }), /missing or invalid/);
  assert.equal(f.calls.length, 0);
});
test('admin denial is reported without leaking credentials or server body', async () => {
  const f = fixture('publish');
  await assert.rejects(executePlan(buildPlan(input('publish')), { ...f, fetcher: async () =>
    Response.json({ error_code: 'desktop_update_operator_denied', secret: token }, { status: 401 }) }),
  error => error.message === 'Authority HTTP 401: desktop_update_operator_denied');
  assert.equal(f.records.length, 0);
});
for (const [name, state, error] of [
  ['stale revision', { revision: 12 }, /revision changed/],
  ['stopped authority', { mode: 'stopped' }, /mode does not allow/],
  ['changed stable', { stable: { version: '0.10.97' } }, /identity changed/],
  ['changed candidate', { candidate: { version: '0.10.99' } }, /identity changed/],
  ['changed source', { candidate: { ...manifest, source_sha: 'c'.repeat(40) } }, /source SHA mismatch/],
  ['changed artifact', { candidate: { ...manifest, artifacts: { ...manifest.artifacts, mac: { ...manifest.artifacts.mac, size: 456 } } } }, /differs from qualified/],
]) test(`refuses ${name} without POST`, async () => {
  const f = fixture('publish', state);
  await assert.rejects(executePlan(buildPlan(input('publish')), f), error);
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 0);
});
test('does not retry a lost POST response or invent a new UUID', async () => {
  const f = fixture('publish'); let posts = 0;
  const fetcher = async (url, init) => {
    if (init.method === 'POST') { posts++; throw new Error('Simulated lost response'); }
    return f.fetcher(url, init);
  };
  await assert.rejects(executePlan(buildPlan(input('publish')), { ...f, fetcher }), /lost response/);
  assert.equal(posts, 1);
});
test('replays exact persisted UUID/body through server fingerprint check after lost response', async () => {
  const plan = buildPlan(input('publish')), receipt = { operation_id: plan.request.operation_id, action: 'publish', revision: 12 };
  const f = fixture('publish', { revision: 12, mode: 'published' }); let posts = 0;
  const fetcher = async (url, init) => {
    if (url.includes('/history')) return Response.json({ entries: [receipt] });
    if (init.method === 'POST') { posts++; assert.deepEqual(JSON.parse(init.body), plan.request); return Response.json(receipt); }
    return f.fetcher(url, init);
  };
  await executePlan(plan, { ...f, fetcher }); assert.equal(posts, 1);
});
test('changed UUID replay action is refused before POST', async () => {
  const plan = buildPlan(input('publish')), f = fixture('publish', { revision: 12 });
  const fetcher = async (url, init) => url.includes('/history') ? Response.json({ entries: [{ operation_id: plan.request.operation_id, action: 'stop', revision: 12 }] }) : f.fetcher(url, init);
  await assert.rejects(executePlan(plan, { ...f, fetcher }), /does not match the request/);
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 0);
});
test('unknown newer native revision fails without automatic rollback', async () => {
  const f = fixture('publish');
  await assert.rejects(executePlan(buildPlan(input('publish')), { ...f, verify: async () => Object.keys(files).map(platform => ({ platform, revision: 14 })) }), /do not match the published revision/);
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 1);
});
test('refuses tampered plan before network', async () => {
  const plan = buildPlan(input('hold')), f = fixture('hold'); plan.request.action = 'complete';
  await assert.rejects(executePlan(plan, f), /differs from validated/); assert.equal(f.calls.length, 0);
});
test('read-only status/history require no mutation inputs and never POST', async () => {
  for (const action of ['status', 'history']) {
    const f = fixture('publish'); await executePlan(buildPlan({ action }), f);
    assert.equal(f.calls.filter(c => c.method === 'POST').length, 0);
  }
});
test('rejects unsupported acceptance/bootstrap and incomplete mutation plans', () => {
  for (const action of ['complete', 'initialize']) assert.throws(() => buildPlan(input(action)), /intentionally unavailable/);
  assert.throws(() => buildPlan(input('prepare', { operation_id: '' })), /UUID-v4/);
  assert.throws(() => buildPlan(input('prepare', { expected_candidate_version: '' })), /Expected candidate/);
  assert.throws(() => buildPlan(input('publish', { previous_version: '0.10.100' })), /must be older/);
  assert.throws(() => buildPlan(input('prepare', { manifest: { ...manifest, artifacts: {} } })), /six platforms/);
});
