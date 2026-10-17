import test from 'node:test';
import assert from 'node:assert/strict';
import { desktopPausePatterns, removeDesktopPauseRoutes } from './remove-desktop-pause-routes.mjs';

const previousVersion = '0.10.100';

function fixture(initialPatterns = desktopPausePatterns(previousVersion)) {
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
    return { status: 200, json: async () => ({
      success: true, result: routes, result_info: { total_pages: 1 },
    }) };
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

test('deletes the five exact Desktop pause routes and retains no route', async () => {
  const { fetcher, calls } = fixture();
  await removeDesktopPauseRoutes(options(fetcher));
  assert.equal(calls.filter((call) => call.method === 'DELETE').length, 5);
});

test('finishes a partial exact-route removal safely', async () => {
  const { fetcher, calls } = fixture(desktopPausePatterns(previousVersion).slice(2));
  await removeDesktopPauseRoutes(options(fetcher));
  assert.equal(calls.filter((call) => call.method === 'DELETE').length, 3);
});

test('refuses an unexpected route owned by the pause Worker', async () => {
  const { fetcher } = fixture([
    ...desktopPausePatterns(previousVersion),
    'backbone-mem.nowledge.co/admin/*',
  ]);
  await assert.rejects(removeDesktopPauseRoutes(options(fetcher)), /Refusing unexpected/);
});
