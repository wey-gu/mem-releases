import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyGADelivery, verifyVersionPolicy } from './verify-ga-delivery.mjs';

const version = '0.10.94';
const files = {
  mac: 'aarch64-apple-darwin.dmg',
  'mac-intel': 'x86_64-apple-darwin.dmg',
  win: 'x86_64-pc-windows-msvc.exe',
  linux: 'x86_64-unknown-linux-gnu.AppImage',
  'linux-deb': 'x86_64-unknown-linux-gnu.deb',
  'linux-rpm': 'x86_64-unknown-linux-gnu.rpm',
  'linux-appimage': 'x86_64-unknown-linux-gnu.AppImage',
};
const targets = {
  mac: 'aarch64-apple-darwin',
  'mac-intel': 'x86_64-apple-darwin',
  win: 'x86_64-pc-windows-msvc',
  linux: 'x86_64-unknown-linux-gnu',
  'linux-deb': 'x86_64-unknown-linux-gnu',
  'linux-rpm': 'x86_64-unknown-linux-gnu',
  'linux-appimage': 'x86_64-unknown-linux-gnu',
};
const artifact = (release, file) => `https://download.test/app/${release}/${file}`;
const reply = (status, body, url = '') => ({
  status,
  url,
  json: async () => body,
  text: async () => body,
});

function fixture({ latest = version, missing = '', updater = latest, apt = version, direct = version } = {}) {
  return async (value) => {
    const url = new URL(value);
    const platform = url.searchParams.get('platform');
    if (url.pathname === '/latest') {
      if (platform === missing) return reply(404, {});
      return reply(200, {
        platform,
        version: latest,
        download_url: artifact(latest, files[platform]),
      });
    }
    if (url.pathname.startsWith('/download/')) {
      const parts = url.pathname.split('/');
      const file = files[parts[3]];
      const release = parts[2] === 'latest' ? latest : direct;
      return reply(200, {}, artifact(release, file));
    }
    if (url.pathname === '/update.json') {
      const target = url.searchParams.get('target');
      const selected = Object.keys(targets).find((name) => targets[name] === target);
      return reply(200, {
        version: updater,
        platforms: { [target]: { url: artifact(updater, files[selected]) } },
      });
    }
    if (url.pathname.endsWith('/Packages')) {
      return reply(200, `Package: nowledge-mem\nVersion: ${apt}\nFilename: pool/nowledge-mem_${apt}_amd64.deb\n`);
    }
    throw new Error(`Unexpected request: ${url}`);
  };
}

function options(fetcher, extras = {}) {
  return {
    expectedVersion: version,
    platforms: ['mac', 'win'],
    scope: ['latest', 'latest-redirect', 'direct', 'update', 'apt'],
    baseUrl: 'https://backbone.test',
    downloadHost: 'download.test',
    fetcher,
    ...extras,
  };
}

test('accepts exact GA on every required surface', async () => {
  await verifyGADelivery(options(fixture()));
});

test('direct GA checks all seven platform aliases including RPM', async () => {
  await verifyGADelivery(options(fixture(), { platforms: Object.keys(files) }));
});

test('rejects a stale latest version despite healthy old artifacts', async () => {
  await assert.rejects(
    verifyGADelivery(options(fixture({ latest: '0.10.93', updater: '0.10.93' }))),
    /latest mac: expected 0\.10\.94, got 0\.10\.93/,
  );
});

test('rejects a missing required platform', async () => {
  await assert.rejects(verifyGADelivery(options(fixture({ missing: 'win' }))),
    /latest win: expected 200, got 404/);
});

test('rejects a stale direct GA route', async () => {
  await assert.rejects(verifyGADelivery(options(fixture({ direct: '0.10.93' }))),
    /download\/0\.10\.94\/mac: expected \/app\/0\.10\.94/);
});

test('rejects a stale updater feed', async () => {
  await assert.rejects(verifyGADelivery(options(fixture({ updater: '0.10.93' }))),
    /update\.json .*expected 0\.10\.94, got 0\.10\.93/);
});

test('rejects stale APT metadata', async () => {
  await assert.rejects(verifyGADelivery(options(fixture({ apt: '0.10.93' }))),
    /APT Packages: expected 0\.10\.94, got 0\.10\.93/);
});

test('older out-of-band GA requires explicit newer-latest mode', async () => {
  const newer = fixture({ latest: '0.10.95', updater: '0.10.95' });
  await assert.rejects(verifyGADelivery(options(newer)),
    /latest mac: expected 0\.10\.94, got 0\.10\.95/);
  await verifyGADelivery(options(newer, {
    allowNewerLatest: true,
    scope: ['latest', 'latest-redirect', 'direct', 'update'],
  }));
});

test('preflight rejects a lower GA before public mutation', async () => {
  await assert.rejects(verifyVersionPolicy({
    expectedVersion: version,
    fetcher: fixture({ latest: '0.10.95' }),
  }), /Preflight refuses 0\.10\.94 behind live latest 0\.10\.95/);
});

test('preflight permits an older patch only with a newer live latest', async () => {
  await verifyVersionPolicy({
    expectedVersion: version,
    allowNewerLatest: true,
    fetcher: fixture({ latest: '0.10.95' }),
  });
  await assert.rejects(verifyVersionPolicy({
    expectedVersion: version,
    allowNewerLatest: true,
    fetcher: fixture(),
  }), /requires live latest newer/);
});
