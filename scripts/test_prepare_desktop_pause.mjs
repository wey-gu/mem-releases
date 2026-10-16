import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { ARTIFACTS } from './build-desktop-update-manifest.mjs';
import { prepareDesktopPause } from './prepare-desktop-pause.mjs';
import worker from './desktop-upgrade-pause-worker.mjs';

const config = JSON.parse(await readFile(new URL('../wrangler.desktop-upgrade-pause.jsonc', import.meta.url)));
const pausedVersion = '1.4.0';
const bytes = (version, file) => Buffer.from(`${version}:${file}`);
function release(version, overrides = {}) {
  return {
    id: 139, tag_name: `v${version}`, draft: false, prerelease: false,
    published_at: '2026-10-15T05:00:00Z',
    assets: ARTIFACTS.map(([, assetName, file]) => ({
      name: assetName(version), state: 'uploaded', size: bytes(version, file).length,
    })),
    ...overrides,
  };
}
const withdrawn = () => release(pausedVersion, { draft: true });
function available(value, init) {
  assert.equal(init.method, 'HEAD');
  assert.equal(init.redirect, 'manual');
  const [, , version, file] = new URL(value).pathname.split('/');
  return new Response(null, { headers: { 'content-length': bytes(version, file).length } });
}
const prepare = (releases, fetcher = available) =>
  prepareDesktopPause({ pausedVersion, releases, config, fetcher });

test('selects the highest older numeric GA across all pages and generates exact routes', async () => {
  const plan = await prepare([
    [withdrawn(), release('1.3.9'), release('1.5.0')],
    [release('1.3.100'), release('1.3.10'), release('1.3.101', { draft: true }),
      release('1.3.102', { prerelease: true }), release('1.3.103-rc1')],
  ]);
  assert.equal(plan.fallback_version, '1.3.100');
  assert.equal(plan.paused_version, pausedVersion);
  assert.equal(plan.config.routes.length, 6);
  assert.equal(plan.config.routes.filter((r) => r.pattern.includes(`/${pausedVersion}/`)).length, 2);
  assert.ok(plan.config.routes.every((r) => !r.pattern.includes('__PAUSED_VERSION__')));
  assert.ok(config.routes.some((r) => r.pattern.includes('__PAUSED_VERSION__')), 'template is not mutated');
});

test('supports another major version without changing source or template', async () => {
  const plan = await prepareDesktopPause({
    pausedVersion: '20.0.0', config, fetcher: available,
    releases: [[release('20.0.0', { draft: true }), release('19.10.0'), release('19.9.100')]],
  });
  assert.equal(plan.fallback_version, '19.10.0');
  assert.ok(plan.config.routes.some((r) => r.pattern === 'backbone-mem.nowledge.co/download/20.0.0/*'));
});

test('skips incomplete and duplicate package sets and missing publication metadata', async () => {
  const incomplete = release('1.3.99');
  incomplete.assets.pop();
  const duplicate = release('1.3.98');
  duplicate.assets.push(duplicate.assets[0]);
  const plan = await prepare([[withdrawn(), incomplete, duplicate,
    release('1.3.97', { published_at: null }), release('1.3.96')]]);
  assert.equal(plan.fallback_version, '1.3.96');
});

test('checks all six CDN files and skips a release with a missing or mismatched package', async () => {
  for (const failedResponse of [new Response(null, { status: 404 }),
    new Response(null, { status: 410 }),
    new Response(null, { headers: { 'content-length': 1 } })]) {
    const urls = [];
    const plan = await prepare([[withdrawn(), release('1.3.100'), release('1.3.99')]], (url, init) => {
      urls.push(url);
      if (url.includes('/1.3.100/') && url.endsWith('.rpm')) return failedResponse.clone();
      return available(url, init);
    });
    assert.equal(plan.fallback_version, '1.3.99');
    assert.equal(urls.filter((url) => url.includes('/1.3.99/')).length, 6);
  }
});

test('stops on CDN authentication, service, redirect or transport failure', async () => {
  for (const status of [301, 403, 429, 500, 503]) {
    await assert.rejects(prepare([[withdrawn(), release('1.3.99')]],
      () => new Response(null, { status })), new RegExp(`HTTP ${status}`));
  }
  await assert.rejects(prepare([[withdrawn(), release('1.3.99')]],
    () => { throw new Error('network unavailable'); }), /network unavailable/);
});

test('refuses no older usable GA, unwithdrawn targets and incomplete release-list input', async () => {
  await assert.rejects(prepare([[withdrawn(), release('1.5.0')]]), /No older published GA/);
  await assert.rejects(prepare([[release(pausedVersion), release('1.3.99')]]), /withdrawn draft/);
  await assert.rejects(prepare([release('1.3.99')]), /paginated/);
  await assert.rejects(prepare([[withdrawn(), release('1.3.99', { assets: [] })]]), /No older/);
});

test('a website download completes with the selected previous bytes on all six package paths', async (t) => {
  const server = createServer((request, response) => {
    const [, , version, file] = request.url.split('/');
    const body = bytes(version, file);
    response.writeHead(200, { 'Content-Length': body.length });
    response.end(request.method === 'HEAD' ? undefined : body);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const localFetch = (url, init) => fetch(`${origin}${new URL(url).pathname}`, init);
  const plan = await prepare([[withdrawn(), release('1.3.100')]], localFetch);
  const env = {
    PAUSED_VERSION: plan.paused_version, FALLBACK_VERSION: plan.fallback_version,
    FALLBACK_PUBLISHED_AT: plan.fallback_published_at, PAUSE_OPERATION_ID: 'test-operation',
  };
  for (const [controlPlatform, , file] of ARTIFACTS) {
    const platform = controlPlatform === 'mac_intel' ? 'mac-intel' : controlPlatform;
    const redirect = await worker.fetch(new Request(
      `https://backbone-mem.nowledge.co/download/latest/${platform}`,
    ), env);
    assert.equal(redirect.status, 302);
    assert.equal(redirect.headers.get('location'),
      `https://download-mem.nowledge.co/app/1.3.100/${file}`);
    assert.match(redirect.headers.get('cache-control'), /no-store/);
    const downloaded = await localFetch(redirect.headers.get('location'));
    assert.equal(downloaded.status, 200);
    assert.deepEqual(Buffer.from(await downloaded.arrayBuffer()), bytes('1.3.100', file));
  }
  assert.equal((await worker.fetch(new Request(
    `https://download-mem.nowledge.co/app/${pausedVersion}/aarch64-apple-darwin.dmg`,
  ), env)).status, 503);
});
