import test from 'node:test';
import assert from 'node:assert/strict';
import { buildDesktopUpdateManifest } from './build-desktop-update-manifest.mjs';
import { handleOperatorRequest } from './desktop-update-operator-worker.mjs';

const token = 'a'.repeat(32);
const baseEnv = {
  TARGET_ORIGIN: 'https://backbone-mem.nowledge.co',
  OPERATOR_NAME: 'hawkingrei',
  INVOCATION_NONCE: 'd'.repeat(64),
  EXPIRES_AT: '2999-01-01T00:00:00.000Z',
  DESKTOP_UPDATE_OPERATOR_TOKENS_STORE: {
    get: async () => JSON.stringify({ hawkingrei: token }),
  },
};

function request(body = { action: 'status' }, nonce = 'd'.repeat(64)) {
  return new Request('https://operator.invalid/invoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Invocation-Nonce': nonce },
    body: JSON.stringify(body),
  });
}

test('uses the selected named operator without returning its credential', async () => {
  const fetcher = async (url, init) => {
    assert.equal(String(url), 'https://backbone-mem.nowledge.co/admin/desktop-updates');
    assert.equal(init.headers.Authorization, `Bearer ${token}`);
    return Response.json({ revision: 14, mode: 'stopped' });
  };
  const response = await handleOperatorRequest(request(), baseEnv, fetcher);
  assert.equal(response.status, 200);
  const text = await response.text();
  assert.doesNotMatch(text, new RegExp(token));
  assert.deepEqual(JSON.parse(text), { revision: 14, mode: 'stopped' });
});

test('uses manual redirects supported by the deployed runtime and rejects redirects', async () => {
  let calls = 0;
  const fetcher = async (url, init) => {
    calls++;
    assert.equal(init.redirect, 'manual');
    return new Response(null, { status: 302, headers: { Location: 'https://elsewhere.invalid/' } });
  };
  const response = await handleOperatorRequest(request(), baseEnv, fetcher);
  assert.equal(response.status, 502);
  assert.equal((await response.json()).error_code, 'operator_upstream_redirect');
  assert.equal(calls, 1);
});

test('fails closed for a bad nonce or mismatched receipt actor', async () => {
  assert.equal((await handleOperatorRequest(request(undefined, 'wrong'), baseEnv)).status, 401);
  const response = await handleOperatorRequest(request({
    action: 'recover', expected_revision: 14, operation_id: '00000000-0000-4000-8000-000000000000', reason: 'Release',
  }), baseEnv, async () => Response.json({ actor: 'someone-else' }));
  assert.equal(response.status, 502);
  assert.equal((await response.json()).error_code, 'operator_identity_mismatch');
});

function release(version) {
  const names = [
    `Nowledge.Mem_${version}_aarch64.dmg`,
    `Nowledge.Mem_${version}_x64.dmg`,
    `Nowledge.Mem_${version}_x64-setup.exe`,
    `Nowledge.Mem_${version}_amd64.AppImage`,
    `Nowledge.Mem_${version}_amd64.deb`,
    `Nowledge.Mem-${version}-1.x86_64.rpm`,
  ];
  return { assets: names.map((name) => ({ name, size: 123, digest: `sha256:${'b'.repeat(64)}` })) };
}

test('builds a six-platform manifest from matching Release and R2 identities', async () => {
  const manifest = await buildDesktopUpdateManifest({
    version: '0.10.101',
    sourceSha: 'c'.repeat(40),
    releaseNotes: 'https://mem.nowledge.co/changelog#0.10.101',
    pubDate: '2026-10-12T18:00:00Z',
    release: release('0.10.101'),
    headObject: async () => ({ ContentLength: 123, ETag: '"etag"' }),
  });
  assert.deepEqual(Object.keys(manifest.artifacts).sort(),
    ['linux-appimage', 'linux-deb', 'linux-rpm', 'mac', 'mac_intel', 'win']);
  assert.equal(manifest.artifacts.mac.etag, 'etag');
  assert.equal(manifest.artifacts['linux-rpm'].key,
    'app/0.10.101/x86_64-unknown-linux-gnu.rpm');
});

test('rejects a Release-to-R2 size mismatch', async () => {
  await assert.rejects(buildDesktopUpdateManifest({
    version: '0.10.101',
    sourceSha: 'c'.repeat(40),
    releaseNotes: '',
    pubDate: '2026-10-12T18:00:00Z',
    release: release('0.10.101'),
    headObject: async () => ({ ContentLength: 122, ETag: 'etag' }),
  }), /R2 size differs/);
});
