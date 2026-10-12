import { pathToFileURL } from 'node:url';

const SCRIPT_NAME = 'nowledge-mem-upgrade-pause';

function requireCondition(ok, message) {
  if (!ok) throw new Error(message);
}

function expectedPatterns(previousVersion) {
  return new Set([
    'download-mem.nowledge.co/apt/*',
    'backbone-mem.nowledge.co/download/latest/*',
    'backbone-mem.nowledge.co/update.json*',
    `download-mem.nowledge.co/app/${previousVersion}/*`,
    `backbone-mem.nowledge.co/download/${previousVersion}/*`,
    'backbone-mem.nowledge.co/latest*',
  ]);
}

export async function openAptPauseRoute({
  previousVersion,
  zoneId,
  apiToken,
  fetcher = fetch,
}) {
  requireCondition(/^\d+\.\d+\.\d+$/.test(previousVersion || ''),
    'PREVIOUS_VERSION must be a clean semver');
  requireCondition(/^[0-9a-f]{32}$/.test(zoneId || ''), 'CLOUDFLARE_ZONE_ID is invalid');
  requireCondition(Boolean(apiToken), 'CLOUDFLARE_API_TOKEN is required');
  const api = `https://api.cloudflare.com/client/v4/zones/${zoneId}/workers/routes`;
  const headers = { Authorization: `Bearer ${apiToken}` };

  async function listPauseRoutes() {
    const response = await fetcher(`${api}?per_page=100`, { headers });
    requireCondition(response.status === 200,
      `List Worker routes: expected 200, got ${response.status}`);
    const body = await response.json();
    requireCondition(body.success === true && Array.isArray(body.result),
      'List Worker routes: malformed response');
    requireCondition((body.result_info?.total_pages || 1) === 1,
      'List Worker routes: pagination would hide routes');
    return body.result.filter((route) => route.script === SCRIPT_NAME);
  }

  const expected = expectedPatterns(previousVersion);
  const routes = await listPauseRoutes();
  const unexpected = routes.filter((route) => !expected.has(route.pattern));
  requireCondition(unexpected.length === 0,
    `Refusing unexpected ${SCRIPT_NAME} routes: ${unexpected.map((route) => route.pattern).join(', ')}`);

  const aptPattern = 'download-mem.nowledge.co/apt/*';
  const retained = [...expected].filter((pattern) => pattern !== aptPattern);
  for (const pattern of retained) {
    requireCondition(routes.some((route) => route.pattern === pattern),
      `Required Desktop pause route is missing: ${pattern}`);
  }
  const aptRoutes = routes.filter((route) => route.pattern === aptPattern);
  requireCondition(aptRoutes.length <= 1, `Expected at most one APT pause route, got ${aptRoutes.length}`);

  if (aptRoutes.length === 1) {
    const response = await fetcher(`${api}/${aptRoutes[0].id}`, { method: 'DELETE', headers });
    requireCondition(response.status === 200,
      `Delete APT pause route: expected 200, got ${response.status}`);
    const body = await response.json();
    requireCondition(body.success === true && body.result?.id === aptRoutes[0].id,
      'Delete APT pause route: malformed response');
  }

  const readback = await listPauseRoutes();
  requireCondition(!readback.some((route) => route.pattern === aptPattern),
    'APT pause route still exists after deletion');
  requireCondition(readback.length === retained.length,
    `Expected ${retained.length} retained Desktop pause routes, got ${readback.length}`);
  for (const pattern of retained) {
    requireCondition(readback.some((route) => route.pattern === pattern),
      `Retained Desktop pause route is missing: ${pattern}`);
  }
  console.log(`APT pause route open; retained ${retained.length} Desktop pause routes`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  openAptPauseRoute({
    previousVersion: process.env.PREVIOUS_VERSION,
    zoneId: process.env.CLOUDFLARE_ZONE_ID,
    apiToken: process.env.CLOUDFLARE_API_TOKEN,
  }).catch((error) => {
    console.error(`APT pause route operation failed: ${error.message}`);
    process.exitCode = 1;
  });
}
