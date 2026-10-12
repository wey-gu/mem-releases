import test from 'node:test';
import assert from 'node:assert/strict';
import { openAptPauseRoute } from './open-apt-pause-route.mjs';

const previousVersion = '0.10.100';
const patterns = [
  'download-mem.nowledge.co/apt/*',
  'backbone-mem.nowledge.co/download/latest/*',
  'backbone-mem.nowledge.co/update.json*',
  `download-mem.nowledge.co/app/${previousVersion}/*`,
  `backbone-mem.nowledge.co/download/${previousVersion}/*`,
  'backbone-mem.nowledge.co/latest*',
];

function fixture(initialPatterns = patterns) {
  let routes = initialPatterns.map((pattern, index) => ({
    id: String(index + 1).padStart(32, '0'),
    pattern,
    script: 'nowledge-mem-upgrade-pause',
  }));
  const calls = [];
  const fetcher = async (value, init = {}) => {
    calls.push({ value, method: init.method || 'GET' });
    if (init.method === 'DELETE') {
      const id = value.split('/').at(-1);
      routes = routes.filter((route) => route.id !== id);
      return { status: 200, json: async () => ({ success: true, result: { id } }) };
    }
    return {
      status: 200,
      json: async () => ({ success: true, result: routes, result_info: { total_pages: 1 } }),
    };
  };
  return { fetcher, calls };
}

function options(fetcher) {
  return {
    previousVersion,
    zoneId: '0123456789abcdef0123456789abcdef',
    apiToken: 'test-token',
    fetcher,
  };
}

test('deletes only the APT route and verifies all Desktop routes remain', async () => {
  const { fetcher, calls } = fixture();
  await openAptPauseRoute(options(fetcher));
  assert.equal(calls.filter((call) => call.method === 'DELETE').length, 1);
  assert.match(calls.find((call) => call.method === 'DELETE').value, /\/00000000000000000000000000000001$/);
});

test('is idempotent after the APT route is already absent', async () => {
  const { fetcher, calls } = fixture(patterns.slice(1));
  await openAptPauseRoute(options(fetcher));
  assert.equal(calls.filter((call) => call.method === 'DELETE').length, 0);
});

test('refuses an unexpected route owned by the pause Worker', async () => {
  const { fetcher } = fixture([...patterns, 'example.com/*']);
  await assert.rejects(openAptPauseRoute(options(fetcher)), /Refusing unexpected/);
});

test('refuses to open APT if a Desktop pause route is missing', async () => {
  const { fetcher } = fixture(patterns.slice(0, -1));
  await assert.rejects(openAptPauseRoute(options(fetcher)), /Required Desktop pause route is missing/);
});
