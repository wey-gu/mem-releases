import test from 'node:test';
import assert from 'node:assert/strict';
import worker from './desktop-upgrade-pause-worker.mjs';

const env = {
  PAUSED_VERSION: '0.10.102',
  FALLBACK_VERSION: '0.10.101',
  FALLBACK_PUBLISHED_AT: '2026-10-13T00:50:08Z',
  PAUSE_OPERATION_ID: '00000000-0000-4000-8000-000000000102',
  BACKBONE: {
    fetch: async (request) => Response.json({ delegated: request.url }),
  },
};

test('delegates protocol-1 latest requests to the controlled authority', async () => {
  const url = 'https://backbone-mem.nowledge.co/latest?platform=mac&update_control=1';
  const response = await worker.fetch(new Request(url), env);
  assert.deepEqual(await response.json(), { delegated: url });
});

test('serves previous stable metadata for every legacy latest platform', async () => {
  const expected = {
    mac: ['aarch64-apple-darwin', 'aarch64-apple-darwin.dmg'],
    'mac-intel': ['x86_64-apple-darwin', 'x86_64-apple-darwin.dmg'],
    win: ['x86_64-pc-windows-msvc', 'x86_64-pc-windows-msvc.exe'],
    linux: ['x86_64-unknown-linux-gnu', 'x86_64-unknown-linux-gnu.AppImage'],
    'linux-appimage': ['x86_64-unknown-linux-gnu', 'x86_64-unknown-linux-gnu.AppImage'],
    'linux-deb': ['x86_64-unknown-linux-gnu', 'x86_64-unknown-linux-gnu.deb'],
    'linux-rpm': ['x86_64-unknown-linux-gnu', 'x86_64-unknown-linux-gnu.rpm'],
  };
  for (const [platform, [target, file]] of Object.entries(expected)) {
    const response = await worker.fetch(new Request(
      `https://backbone-mem.nowledge.co/latest?platform=${platform}`,
    ), env);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('cache-control'), /no-store/i);
    assert.equal(response.headers.get('x-mem-latest-fallback'), '0.10.101');
    assert.deepEqual(await response.json(), {
      version: '0.10.101',
      platform,
      target,
      download_url: `https://download-mem.nowledge.co/app/0.10.101/${file}`,
      release_notes: '',
      pub_date: '2026-10-13T00:50:08Z',
      file_size: null,
    });
  }
});

test('redirects download latest to the previous stable artifact', async () => {
  const response = await worker.fetch(new Request(
    'https://backbone-mem.nowledge.co/download/latest/mac-intel',
  ), env);
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('location'),
    'https://download-mem.nowledge.co/app/0.10.101/x86_64-apple-darwin.dmg');
  assert.equal(response.headers.get('x-mem-latest-fallback'), '0.10.101');
});

test('keeps updater, paused version, and package repository fail closed', async () => {
  for (const [url, expectedStatus] of [
    ['https://backbone-mem.nowledge.co/update.json?current_version=0.10.100', 204],
    ['https://backbone-mem.nowledge.co/download/0.10.102/mac', 503],
    ['https://download-mem.nowledge.co/app/0.10.102/aarch64-apple-darwin.dmg', 503],
    ['https://download-mem.nowledge.co/apt/dists/stable/InRelease', 503],
  ]) {
    const response = await worker.fetch(new Request(url), env);
    assert.equal(response.status, expectedStatus);
    assert.match(response.headers.get('cache-control'), /no-store/i);
  }
});

test('fails closed when fallback metadata is invalid', async () => {
  await assert.rejects(worker.fetch(new Request(
    'https://backbone-mem.nowledge.co/latest?platform=mac',
  ), { ...env, FALLBACK_VERSION: 'latest' }), /clean semver/);
  const response = await worker.fetch(new Request(
    'https://backbone-mem.nowledge.co/latest?platform=unknown',
  ), env);
  assert.equal(response.status, 400);
});

test('never redirects to the paused version or a newer release', async () => {
  for (const fallback of ['1.4.0', '1.10.0', '1.04.0']) {
    await assert.rejects(worker.fetch(new Request(
      'https://backbone-mem.nowledge.co/download/latest/mac',
    ), { ...env, PAUSED_VERSION: '1.4.0', FALLBACK_VERSION: fallback }));
  }
});
