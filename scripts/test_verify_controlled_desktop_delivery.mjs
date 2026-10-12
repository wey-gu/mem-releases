import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyControlledDesktopDelivery } from './verify-controlled-desktop-delivery.mjs';

const version = '0.10.100';
const filenames = {
  mac: 'aarch64-apple-darwin.dmg', mac_intel: 'x86_64-apple-darwin.dmg',
  win: 'x86_64-pc-windows-msvc.exe', 'linux-appimage': 'x86_64-unknown-linux-gnu.AppImage',
  'linux-deb': 'x86_64-unknown-linux-gnu.deb', 'linux-rpm': 'x86_64-unknown-linux-gnu.rpm',
};
const manifest = { version, source_sha: 'a'.repeat(40), artifacts: Object.fromEntries(
  Object.entries(filenames).map(([p, f]) => [p, { key: `app/${version}/${f}`, size: 123, sha256: 'b'.repeat(64) }])) };
function options(mutate = () => {}, extras = {}) {
  const calls = [];
  return { manifest, previousVersion: '0.10.99', clock: () => 100,
    fetcher: async (url, init) => {
      calls.push({ url, init });
      assert.equal(url.searchParams.get('update_control'), '1');
      assert.equal(init.headers['User-Agent'], 'Nowledge-Mem/0.10.99 (Tauri)');
      assert.equal(init.headers['Cache-Control'], 'no-cache');
      assert.equal(init.redirect, 'error');
      const platform = url.searchParams.get('platform'), file = filenames[platform];
      const body = { platform, version, target: file.slice(0, file.lastIndexOf('.')),
        download_url: `https://download-mem.nowledge.co/app/${version}/${file}`, file_size: 123,
        control: { protocol: 1, status: 'allowed', revision: 12,
          request_id: url.searchParams.get('request_id'), sha256: 'b'.repeat(64),
          issued_at: '2026-10-12T01:00:00Z', expires_at: '2026-10-12T01:01:00Z' } };
      const headers = new Headers({ 'cache-control': 'no-store' });
      mutate(body, headers, platform);
      return new Response(JSON.stringify(body), { headers });
    }, calls, ...extras };
}

test('qualifies six exact native requests with independent artifact evidence', async () => {
  const opts = options();
  const receipts = await verifyControlledDesktopDelivery(opts);
  assert.equal(receipts.length, 6);
  assert.deepEqual(receipts.map(r => r.platform), Object.keys(filenames));
  assert.equal(new Set(receipts.map(r => r.request_id)).size, 6);
});

test('rejects stale controlled candidate even when legacy latest is healthy', async () => {
  const opts = options(body => { body.version = '0.10.99'; });
  const fetcher = opts.fetcher;
  opts.fetcher = (url, init) => url.searchParams.get('update_control') === '1'
    ? fetcher(url, init) : Promise.resolve(Response.json({ version }));
  assert.equal((await (await opts.fetcher(new URL('https://backbone-mem.nowledge.co/latest'))).json()).version, version);
  await assert.rejects(verifyControlledDesktopDelivery(opts), /controlled version expected 0\.10\.100, got 0\.10\.99/);
});

for (const [name, mutate, error] of [
  ['stopped authority', b => { b.control.status = 'stopped'; }, /not an allowed/],
  ['missing control', b => { delete b.control; }, /not an allowed/],
  ['request replay', b => { b.control.request_id = 'bad'; }, /request ID mismatch/],
  ['mutable artifact alias', b => { b.download_url = 'https://download-mem.nowledge.co/app/latest/test.dmg'; }, /URL mismatch/],
  ['wrong checksum', b => { b.control.sha256 = 'c'.repeat(64); }, /size\/SHA-256 mismatch/],
  ['wrong size', b => { b.file_size++; }, /size\/SHA-256 mismatch/],
  ['missing no-store', (_b, h) => { h.delete('cache-control'); }, /must be no-store/],
  ['wrong lifetime', b => { b.control.expires_at = '2026-10-12T01:02:00Z'; }, /decision lifetime/],
  ['wrong target', b => { b.target = 'wrong'; }, /platform\/target mismatch/],
  ['changed authority revision', (b, _h, p) => { if (p === 'linux-rpm') b.control.revision++; }, /Authority changed/],
]) test(`rejects ${name}`, async () => {
  await assert.rejects(verifyControlledDesktopDelivery(options(mutate)), error);
});

test('requires all six artifacts and previous native version before network access', async () => {
  const opts = options();
  await assert.rejects(verifyControlledDesktopDelivery({ ...opts, previousVersion: undefined }), /Previous native desktop version/);
  await assert.rejects(verifyControlledDesktopDelivery({ ...opts, manifest: { ...manifest, artifacts: { mac: manifest.artifacts.mac } } }), /all six native platforms/);
  assert.equal(opts.calls.length, 0);
});
test('refuses reused request IDs', async () => {
  await assert.rejects(verifyControlledDesktopDelivery(options(undefined, { requestId: () => 'abcdef' })), /valid and unique/);
});
test('charges request time against decision validity without using server clock offset', async () => {
  let calls = 0;
  await assert.rejects(verifyControlledDesktopDelivery(options(undefined, { clock: () => calls++ * 60_000 })), /decision lifetime/);
});
