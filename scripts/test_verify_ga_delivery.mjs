import test from 'node:test';
import assert from 'node:assert/strict';
import {
  verifyAptDelivery,
  verifyAptPromotionPolicy,
  verifyGADelivery,
  verifyStagedGADelivery,
  verifyVersionPolicy,
} from './verify-ga-delivery.mjs';
import { APT_LANDING_MARKER } from './publish-apt-landing.mjs';
import pauseWorker from './desktop-upgrade-pause-worker.mjs';

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
const reply = (status, body, url = '', contentType = 'application/json') => ({
  status,
  url,
  headers: new Headers({ 'Content-Type': contentType }),
  json: async () => body,
  text: async () => body,
});

function fixture({ latest = version, missing = '', updater = latest, apt = version, direct = version } = {}) {
  return async (value) => {
    const url = new URL(value);
    const platform = url.searchParams.get('platform');
    if (url.pathname === '/apt' || url.pathname === '/apt/') {
      return reply(200, `<html ${APT_LANDING_MARKER}></html>`, '', 'text/html; charset=utf-8');
    }
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
  await assert.rejects(verifyGADelivery(options(fixture(), {
    allowNewerLatest: true,
    scope: ['latest', 'latest-redirect', 'direct', 'update'],
  })), /older-patch mode requires a version newer/);
});

test('preflight rejects a lower GA before public mutation', async () => {
  await assert.rejects(verifyVersionPolicy({
    expectedVersion: version,
    platforms: ['mac'],
    fetcher: fixture({ latest: '0.10.95' }),
  }), /Preflight refuses 0\.10\.94 behind live mac latest 0\.10\.95/);
});

test('preflight permits an older patch only with a newer live latest', async () => {
  await verifyVersionPolicy({
    expectedVersion: version,
    platforms: ['mac'],
    allowNewerLatest: true,
    fetcher: fixture({ latest: '0.10.95' }),
  });
  await assert.rejects(verifyVersionPolicy({
    expectedVersion: version,
    platforms: ['mac'],
    allowNewerLatest: true,
    fetcher: fixture(),
  }), /requires mac latest newer/);
});

test('preflight checks every platform and fails closed on missing latest', async () => {
  await assert.rejects(verifyVersionPolicy({
    expectedVersion: version,
    platforms: ['mac', 'win'],
    fetcher: fixture({ missing: 'win' }),
  }), /Preflight latest win: expected 200, got 404/);
  const mixed = async (url, init) => {
    const platform = new URL(url).searchParams.get('platform');
    return fixture({ latest: platform === 'win' ? '0.10.95' : version })(url, init);
  };
  await assert.rejects(verifyVersionPolicy({
    expectedVersion: version,
    platforms: ['mac', 'win'],
    fetcher: mixed,
  }), /Preflight refuses 0\.10\.94 behind live win latest 0\.10\.95/);
});

test('staged GA verifies exact direct downloads without consulting mutable latest', async () => {
  const seen = [];
  const fetcher = async (value) => {
    const url = new URL(value);
    seen.push(url.pathname);
    assert.match(url.pathname, /^\/download\/0\.10\.94\//);
    const platform = url.pathname.split('/').at(-1);
    return reply(200, {}, artifact(version, files[platform]));
  };
  await verifyStagedGADelivery(options(fetcher, { platforms: ['mac', 'win'] }));
  assert.deepEqual(seen, ['/download/0.10.94/mac', '/download/0.10.94/win']);
});

test('staged GA rejects a redirect to another version', async () => {
  const fetcher = async (value) => {
    const platform = new URL(value).pathname.split('/').at(-1);
    return reply(200, {}, artifact('0.10.93', files[platform]));
  };
  await assert.rejects(
    verifyStagedGADelivery(options(fetcher, { platforms: ['mac'] })),
    /expected \/app\/0\.10\.94/,
  );
});

test('same-version unpause confirms all exact routes remain blocked before removal', async () => {
  const pausedVersion = '1.4.0';
  const seen = [];
  const env = {
    PAUSED_VERSION: pausedVersion, FALLBACK_VERSION: '1.3.100',
    FALLBACK_PUBLISHED_AT: '2026-10-15T00:00:00Z', PAUSE_OPERATION_ID: 'test-pause',
  };
  const platforms = ['mac', 'mac-intel', 'win', 'linux-appimage', 'linux-deb', 'linux-rpm'];
  await verifyStagedGADelivery({ expectedVersion: pausedVersion, pausedVersion, platforms,
    fetcher: (url, init) => {
      seen.push(url);
      assert.equal(init.method, 'HEAD');
      return pauseWorker.fetch(new Request(url, init), env);
    },
  });
  assert.equal(seen.length, 12);
  for (const platform of platforms) {
    assert.ok(seen.includes(`https://backbone-mem.nowledge.co/download/${pausedVersion}/${platform}`));
    assert.ok(seen.includes(`https://download-mem.nowledge.co/app/${pausedVersion}/${files[platform]}`));
  }
});

test('same-version unpause rejects an exact route that is already open or lacks pause identity', async () => {
  for (const [status, headers] of [
    [200, { 'cache-control': 'no-store', 'x-mem-upgrade-pause': 'test-pause' }],
    [503, { 'cache-control': 'no-store' }],
    [503, { 'x-mem-upgrade-pause': 'test-pause' }],
  ]) {
    await assert.rejects(verifyStagedGADelivery({ expectedVersion: version, pausedVersion: version,
      platforms: ['mac'], fetcher: async () => new Response(null, { status, headers }),
    }), /paused|503|no-store|pause/);
  }
});

test('a different staged candidate still requires its exact public download to be available', async () => {
  await verifyStagedGADelivery(options(fixture(), { pausedVersion: '0.10.93' }));
  await assert.rejects(verifyStagedGADelivery(options(async () => new Response(null, {
    status: 503, headers: { 'cache-control': 'no-store', 'x-mem-upgrade-pause': 'test-pause' },
  }), { pausedVersion: '0.10.93' })), /expected 200, got 503/);
});

test('APT-only verification accepts the exact staged GA', async () => {
  await verifyAptDelivery(options(fixture()));
});

test('APT-only verification rejects a missing repository landing', async () => {
  const fetcher = async (value, init) => {
    if (new URL(value).pathname === '/apt') return reply(404, 'not found');
    return fixture()(value, init);
  };
  await assert.rejects(verifyAptDelivery(options(fetcher)),
    /APT landing \/apt: expected 200, got 404/);
});

test('APT preflight accepts an older live release and rejects a newer one', async () => {
  await verifyAptPromotionPolicy(options(fixture({ apt: '0.10.93' })));
  await assert.rejects(
    verifyAptPromotionPolicy(options(fixture({ apt: '0.10.95' }))),
    /refuses 0\.10\.94 behind live 0\.10\.95/,
  );
});
