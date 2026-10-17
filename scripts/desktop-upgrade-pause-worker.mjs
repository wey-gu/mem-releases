const PLATFORM_ARTIFACTS = Object.freeze({
  mac: {
    target: 'aarch64-apple-darwin',
    file: 'aarch64-apple-darwin.dmg',
  },
  'mac-intel': {
    target: 'x86_64-apple-darwin',
    file: 'x86_64-apple-darwin.dmg',
  },
  win: {
    target: 'x86_64-pc-windows-msvc',
    file: 'x86_64-pc-windows-msvc.exe',
  },
  linux: {
    target: 'x86_64-unknown-linux-gnu',
    file: 'x86_64-unknown-linux-gnu.AppImage',
  },
  'linux-appimage': {
    target: 'x86_64-unknown-linux-gnu',
    file: 'x86_64-unknown-linux-gnu.AppImage',
  },
  'linux-deb': {
    target: 'x86_64-unknown-linux-gnu',
    file: 'x86_64-unknown-linux-gnu.deb',
  },
  'linux-rpm': {
    target: 'x86_64-unknown-linux-gnu',
    file: 'x86_64-unknown-linux-gnu.rpm',
  },
});

export function compareVersions(left, right) {
  const clean = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
  if (!clean.test(left || '') || !clean.test(right || '')) {
    throw new Error('Versions must be clean semver');
  }
  const a = left.split('.').map(BigInt);
  const b = right.split('.').map(BigInt);
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
  }
  return 0;
}

function requireFallback(env) {
  if (compareVersions(env.FALLBACK_VERSION, env.PAUSED_VERSION) >= 0) {
    throw new Error('FALLBACK_VERSION must precede PAUSED_VERSION');
  }
  if (Number.isNaN(Date.parse(env.FALLBACK_PUBLISHED_AT || ''))) {
    throw new Error('FALLBACK_PUBLISHED_AT must be an ISO-8601 timestamp');
  }
  return env.FALLBACK_VERSION;
}

function baseHeaders(env) {
  return {
    'Access-Control-Allow-Origin': '*',
    'Cache-Control': 'no-store',
    'X-Mem-Upgrade-Pause': env.PAUSE_OPERATION_ID,
  };
}

function downloadUrl(version, artifact) {
  return `https://download-mem.nowledge.co/app/${version}/${artifact.file}`;
}

function fallbackLatest(url, env) {
  const version = requireFallback(env);
  const platform = url.searchParams.get('platform') || 'mac';
  const artifact = PLATFORM_ARTIFACTS[platform];
  if (!artifact) {
    return Response.json({ error: 'Invalid platform' }, {
      status: 400,
      headers: baseHeaders(env),
    });
  }
  return Response.json({
    version,
    platform,
    target: artifact.target,
    download_url: downloadUrl(version, artifact),
    release_notes: '',
    pub_date: env.FALLBACK_PUBLISHED_AT,
    file_size: null,
  }, {
    headers: {
      ...baseHeaders(env),
      'X-Mem-Latest-Fallback': version,
    },
  });
}

function fallbackDownload(url, env) {
  const version = requireFallback(env);
  const parts = url.pathname.split('/').filter(Boolean);
  const platform = parts.length === 3 ? parts[2] : '';
  const artifact = PLATFORM_ARTIFACTS[platform];
  if (!artifact) {
    return Response.json({ error: 'Invalid platform' }, {
      status: 400,
      headers: baseHeaders(env),
    });
  }
  return new Response(null, {
    status: 302,
    headers: {
      ...baseHeaders(env),
      Location: downloadUrl(version, artifact),
      'X-Mem-Latest-Fallback': version,
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/latest'
        && url.searchParams.has('update_control')) {
      return env.BACKBONE.fetch(request);
    }

    if (request.method === 'GET' && url.pathname === '/latest') {
      return fallbackLatest(url, env);
    }
    if ((request.method === 'GET' || request.method === 'HEAD')
        && url.pathname.startsWith('/download/latest/')) {
      return fallbackDownload(url, env);
    }

    const headers = {
      ...baseHeaders(env),
      'Retry-After': '3600',
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
