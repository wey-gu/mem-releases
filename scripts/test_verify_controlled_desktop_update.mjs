import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyControlledDesktopUpdate } from './verify-controlled-desktop-update.mjs';

const targets = {
  mac: 'aarch64-apple-darwin',
  mac_intel: 'x86_64-apple-darwin',
  win: 'x86_64-pc-windows-msvc',
  'linux-appimage': 'x86_64-unknown-linux-gnu',
  'linux-deb': 'x86_64-unknown-linux-gnu',
  'linux-rpm': 'x86_64-unknown-linux-gnu',
};
const extensions = {
  mac: '.dmg', mac_intel: '.dmg', win: '.exe', 'linux-appimage': '.AppImage',
  'linux-deb': '.deb', 'linux-rpm': '.rpm',
};
const manifest = {
  version: '0.10.101',
  source_sha: 'a'.repeat(40),
  release_notes: 'https://mem.nowledge.co/changelog#0.10.101',
  pub_date: '2026-10-12T19:00:00.000Z',
  artifacts: Object.fromEntries(Object.entries(targets).map(([platform, target]) => [platform, {
    key: `app/0.10.101/${target}${extensions[platform]}`,
    size: 123,
    etag: 'etag',
    sha256: 'b'.repeat(64),
  }])),
};

function responseFor(url, init, override = {}) {
  const parsed = new URL(url);
  const platform = parsed.searchParams.get('platform');
  const requestId = parsed.searchParams.get('request_id');
  assert.equal(init.headers['User-Agent'], 'Nowledge-Mem/0.10.100 (Tauri)');
  const issued = new Date();
  return Response.json({
    version: manifest.version,
    platform,
    target: targets[platform],
    download_url: `https://download-mem.nowledge.co/${manifest.artifacts[platform].key}`,
    file_size: 123,
    release_notes: manifest.release_notes,
    pub_date: manifest.pub_date,
    control: {
      protocol: 1,
      status: 'allowed',
      revision: 17,
      request_id: requestId,
      issued_at: issued.toISOString(),
      expires_at: new Date(issued.getTime() + 60_000).toISOString(),
      sha256: 'b'.repeat(64),
    },
    ...override,
  }, { headers: { 'Cache-Control': 'no-store' } });
}

test('verifies fresh protocol-1 allowed responses for all six platforms', async () => {
  let index = 0;
  await verifyControlledDesktopUpdate({
    manifest,
    expectedRevision: 17,
    previousVersion: '0.10.100',
    fetcher: async (url, init) => {
      index += 1;
      return responseFor(url, init);
    },
    requestIdFactory: () => `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
  });
  assert.equal(index, 6);
});

test('rejects an allowed response with the wrong SHA-256', async () => {
  await assert.rejects(verifyControlledDesktopUpdate({
    manifest,
    expectedRevision: 17,
    previousVersion: '0.10.100',
    fetcher: async (url, init) => {
      const response = responseFor(url, init);
      const body = await response.json();
      body.control.sha256 = 'c'.repeat(64);
      return Response.json(body, { headers: { 'Cache-Control': 'no-store' } });
    },
    requestIdFactory: () => '00000000-0000-4000-8000-000000000001',
  }), /SHA-256 mismatch/);
});
