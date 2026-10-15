import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyUpgradePause } from './verify-upgrade-pause.mjs';

const pausedHeaders = {
  'cache-control': 'no-store',
  'retry-after': '3600',
  'x-mem-upgrade-pause': '2026-10-12',
};

function reply(status, body, headers = {}) {
  return {
    status,
    headers: new Headers(headers),
    json: async () => body,
    text: async () => String(body),
  };
}

function fixture({
  legacyStatus = 503,
  controlStatus = 'stopped',
  exposeRelease = false,
  fallbackVersion = '',
  fallbackMarker = fallbackVersion,
} = {}) {
  return async (value) => {
    const url = new URL(value);
    if (url.pathname === '/latest' && url.searchParams.get('update_control') === '1') {
      const requestId = url.searchParams.get('request_id');
      return reply(200, {
        version: exposeRelease ? '0.10.101' : '',
        download_url: exposeRelease ? 'https://download.test/release' : '',
        control: { protocol: 1, status: controlStatus, revision: 14, request_id: requestId },
      }, { 'cache-control': 'no-store' });
    }
    if (url.pathname === '/update.json') return reply(204, '', pausedHeaders);
    if (fallbackVersion && url.pathname === '/latest') {
      const platform = url.searchParams.get('platform');
      return reply(200, {
        platform,
        version: fallbackVersion,
        download_url: `https://download-mem.nowledge.co/app/${fallbackVersion}/artifact`,
      }, {
        ...pausedHeaders,
        'x-mem-latest-fallback': fallbackMarker,
      });
    }
    if (fallbackVersion && url.pathname.startsWith('/download/latest/')) {
      return reply(302, '', {
        ...pausedHeaders,
        location: `https://download-mem.nowledge.co/app/${fallbackVersion}/artifact`,
        'x-mem-latest-fallback': fallbackMarker,
      });
    }
    return reply(legacyStatus, 'Desktop upgrades are temporarily paused.', pausedHeaders);
  };
}

test('uses the protocol-1 platform name for Intel macOS', async () => {
  const controlledPlatforms = [];
  const fetcher = async (value) => {
    const url = new URL(value);
    if (url.pathname === '/latest' && url.searchParams.get('update_control') === '1') {
      controlledPlatforms.push(url.searchParams.get('platform'));
    }
    return fixture()(value);
  };
  await verifyUpgradePause(options(fetcher, { platforms: ['mac-intel'] }));
  assert.deepEqual(controlledPlatforms, ['mac_intel']);
});

function options(fetcher, extras = {}) {
  return {
    platforms: ['mac', 'linux-rpm'],
    previousVersion: '0.10.100',
    baseUrl: 'https://backbone.test',
    aptBaseUrl: 'https://download.test/apt',
    fetcher,
    requestIdFactory: () => '00000000-0000-4000-8000-000000000101',
    ...extras,
  };
}

test('accepts fail-closed legacy routes and stopped protocol-1 responses', async () => {
  await verifyUpgradePause(options(fixture()));
});

test('accepts previous stable metadata while the controlled authority remains stopped', async () => {
  const fallbackVersion = '0.10.99';
  await verifyUpgradePause(options(fixture({ fallbackVersion }), { fallbackVersion }));
});

test('rejects fallback metadata that exposes the paused release', async () => {
  const fallbackVersion = '0.10.99';
  await assert.rejects(verifyUpgradePause(options(
    fixture({ fallbackVersion: '0.10.100', fallbackMarker: fallbackVersion }),
    { fallbackVersion },
  )), /expected version 0\.10\.99/);
});

test('desktop-only mode does not require the APT route to remain paused', async () => {
  const fetcher = async (value) => {
    const url = new URL(value);
    if (url.host === 'download.test') return reply(200, 'live apt');
    return fixture()(value);
  };
  await verifyUpgradePause(options(fetcher, { includeApt: false }));
});

test('rejects an open legacy route', async () => {
  await assert.rejects(verifyUpgradePause(options(fixture({ legacyStatus: 200 }))),
    /expected 503/);
});

test('rejects a controlled authority that is not stopped', async () => {
  await assert.rejects(verifyUpgradePause(options(fixture({ controlStatus: 'allowed' }))),
    /expected stopped/);
});

test('rejects a stopped response that exposes release fields', async () => {
  await assert.rejects(verifyUpgradePause(options(fixture({ exposeRelease: true }))),
    /stopped response exposed a release/);
});

test('legacy-only boundary stays paused after a separately verified candidate is allowed', async () => {
  const fetcher = async (value) => {
    const url = new URL(value);
    assert.notEqual(url.searchParams.get('update_control'), '1');
    return fixture({ controlStatus: 'allowed', exposeRelease: true })(value);
  };
  await verifyUpgradePause(options(fetcher, { includeApt: false, includeControlled: false }));
  await assert.rejects(verifyUpgradePause(options(fixture({ legacyStatus: 200 }),
    { includeApt: false, includeControlled: false })), /expected 503/);
});
