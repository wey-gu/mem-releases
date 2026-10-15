import test from 'node:test';
import assert from 'node:assert/strict';
import {
  APT_LANDING_KEYS,
  APT_LANDING_MARKER,
  aptLandingDocument,
  publishAptLanding,
  verifyAptLanding,
} from './publish-apt-landing.mjs';

function response(status, body, contentType = 'text/html; charset=utf-8') {
  return {
    status,
    headers: new Headers({ 'Content-Type': contentType }),
    text: async () => body,
  };
}

test('publishes both R2 keys and purges both public URLs', async () => {
  const puts = [];
  const purges = [];
  const result = await publishAptLanding({
    host: 'https://download.test',
    put: async (object) => puts.push(object),
    purge: async (urls) => purges.push(...urls),
  });

  assert.deepEqual(puts.map(({ key }) => key), [...APT_LANDING_KEYS]);
  for (const object of puts) {
    assert.equal(object.contentType, 'text/html; charset=utf-8');
    assert.equal(object.cacheControl, 'public, max-age=300, no-transform');
    assert.match(object.body.toString(), /Nowledge Mem APT Repository/);
    assert.match(object.body.toString(), /apt-get install --only-upgrade nowledge-mem/);
  }
  assert.deepEqual(purges, ['https://download.test/apt', 'https://download.test/apt/']);
  assert.deepEqual(result.keys, ['apt', 'apt/']);
});

test('landing document carries the stable verification marker', () => {
  assert.match(aptLandingDocument(), new RegExp(APT_LANDING_MARKER));
});

test('verifies both public APT landing paths', async () => {
  const seen = [];
  await verifyAptLanding({
    host: 'download.test',
    fetcher: async (url) => {
      seen.push(url);
      return response(200, `<html ${APT_LANDING_MARKER}></html>`);
    },
  });
  assert.deepEqual(seen, ['https://download.test/apt', 'https://download.test/apt/']);
});

test('rejects a missing or stale APT landing object', async () => {
  await assert.rejects(verifyAptLanding({
    host: 'download.test',
    fetcher: async () => response(404, 'not found'),
  }), /expected 200, got 404/);

  await assert.rejects(verifyAptLanding({
    host: 'download.test',
    fetcher: async () => response(200, '<html></html>'),
  }), /repository marker missing/);
});
