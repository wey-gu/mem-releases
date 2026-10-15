export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/latest'
        && url.searchParams.has('update_control')) {
      return env.BACKBONE.fetch(request);
    }

    const headers = {
      'Cache-Control': 'no-store',
      'Retry-After': '3600',
      'X-Mem-Upgrade-Pause': env.PAUSE_OPERATION_ID,
    };
    if (url.pathname === '/update.json') {
      return new Response(null, { status: 204, headers });
    }
    return new Response('Desktop upgrades are temporarily paused.', {
      status: 503,
      headers,
    });
  },
};
