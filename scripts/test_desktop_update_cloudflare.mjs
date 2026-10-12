import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, copyFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { buildPlan, executePlan } from './desktop-update-operation.mjs';
import { previewFiles, bindingTransport } from './desktop-update-cloudflare.mjs';

const request = { action: 'hold', expected_revision: '9', expected_stable_version: '0.10.98',
  expected_candidate_version: '0.10.99', operation_id: 'feee6317-9774-46c4-b258-cec5bafdd8f7', reason: 'Reviewed candidate retirement' };

test('external preview binds only selected existing authority, with no route/migration/secret', () => {
  for (const environment of ['staging', 'production']) {
    const result = previewFiles(buildPlan(request), environment);
    assert.equal(result.config.workers_dev, false);
    assert.equal(result.config.durable_objects.bindings.length, 1);
    assert.equal(result.config.durable_objects.bindings[0].script_name,
      environment === 'production' ? 'nowledge-mem-backbone-prod' : 'nowledge-mem-backbone-staging');
    assert.equal(result.config.migrations, undefined); assert.equal(result.config.routes, undefined);
    assert.equal(result.config.secrets_store_secrets, undefined); assert.equal(result.digest.length, 64);
  }
  assert.throws(() => previewFiles(buildPlan(request), 'wrong'), /Explicit production or staging/);
});
test('tampered acceptance plan is refused before preview creation', () => {
  const plan = buildPlan(request); plan.request.action = 'complete';
  assert.throws(() => previewFiles(plan, 'production'), /differs from validated/);
});
test('loopback adapter maps only approved authority routes and never forwards credentials', async () => {
  const seen = [];
  const transport = bindingTransport('http://127.0.0.1:9876/', async (url, init) => { seen.push({ url, init }); return Response.json({}); });
  const init = { method: 'GET', headers: { Authorization: 'must-not-forward', Cookie: 'must-not-forward' } };
  await transport('https://backbone-mem.nowledge.co/admin/desktop-updates', init);
  await transport('https://backbone-mem.nowledge.co/admin/desktop-updates/history?limit=100', init);
  await transport('https://backbone-mem.nowledge.co/admin/desktop-updates', { ...init, method: 'POST', body: '{}' });
  assert.deepEqual(seen.map(r => r.url.pathname), ['/status', '/history', '/operation']);
  for (const r of seen) { assert.equal(r.init.headers.Authorization, undefined); assert.equal(r.init.headers.Cookie, undefined); }
  assert.throws(() => transport('https://other.test/admin/desktop-updates', init), /Unexpected authority origin/);
  assert.throws(() => transport('https://backbone-mem.nowledge.co/admin/customer-data', init), /Unexpected authority route/);
  assert.throws(() => bindingTransport('http://192.168.1.2:9876/'), /loopback-only/);
});

async function withWorker(plan, run) {
  const directory = await mkdtemp(join(tmpdir(), 'mem-update-binding-test-'));
  try {
    const files = previewFiles(plan, 'production');
    await writeFile(join(directory, 'approved-plan.mjs'), files.module);
    await copyFile(new URL('./desktop-update-binding-worker.mjs', import.meta.url), join(directory, 'worker.mjs'));
    const worker = (await import(pathToFileURL(join(directory, 'worker.mjs')))).default;
    const calls = [];
    const env = { CONTROL: {
      idFromName: name => { assert.equal(name, 'desktop:production'); return 'fixed-id'; },
      get: id => { assert.equal(id, 'fixed-id'); return { fetch: async (url, init) => { calls.push({ url, init }); return Response.json({ success: true }); } }; },
    } };
    await run(worker, env, calls);
  } finally { await rm(directory, { recursive: true, force: true }); }
}
test('actual binding worker permits only pinned operation with fixed audit actor', async () => {
  const plan = buildPlan(request);
  await withWorker(plan, async (worker, env, calls) => {
    const response = await worker.fetch(new Request('https://preview.test/operation', { method: 'POST', body: JSON.stringify(plan.request),
      headers: { 'X-Desktop-Operator': 'impersonated-human' } }), env);
    assert.equal(response.status, 200); assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'https://control/operation');
    assert.equal(calls[0].init.headers['X-Desktop-Operator'], 'release-ci');
    const modified = { ...plan.request, action: 'complete' };
    assert.equal((await worker.fetch(new Request('https://preview.test/operation', { method: 'POST', body: JSON.stringify(modified) }), env)).status, 403);
    assert.equal((await worker.fetch(new Request('https://preview.test/customer-data'), env)).status, 403);
    assert.equal((await worker.fetch(new Request('https://preview.test/status?unapproved=true'), env)).status, 403);
    assert.equal(calls.length, 1);
  });
});
test('read-only preview cannot invoke operation even if requested', async () => {
  await withWorker(buildPlan({ action: 'status' }), async (worker, env, calls) => {
    assert.equal((await worker.fetch(new Request('https://preview.test/health'), env)).status, 200);
    assert.equal(calls.length, 0);
    assert.equal((await worker.fetch(new Request('https://preview.test/operation', { method: 'POST', body: JSON.stringify(request) }), env)).status, 403);
    assert.equal(calls.length, 0);
    await worker.fetch(new Request('https://preview.test/status'), env);
    assert.equal(calls[0].url, 'https://control/status');
  });
});
test('core status uses authenticated binding without any operator password', async () => {
  const result = await executePlan(buildPlan({ action: 'status' }), { authorityFetch: async (_url, init) => {
    assert.equal(init.headers.Authorization, undefined); return Response.json({ revision: 9, mode: 'published' });
  } });
  assert.equal(result.revision, 9);
});
