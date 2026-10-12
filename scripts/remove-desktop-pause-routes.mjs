import { pathToFileURL } from 'node:url';

const SCRIPT_NAME = 'nowledge-mem-upgrade-pause';

function requireCondition(ok, message) {
  if (!ok) throw new Error(message);
}

export function desktopPausePatterns(previousVersion) {
  return [
    'backbone-mem.nowledge.co/download/latest/*',
    'backbone-mem.nowledge.co/update.json*',
    `download-mem.nowledge.co/app/${previousVersion}/*`,
    `backbone-mem.nowledge.co/download/${previousVersion}/*`,
    'backbone-mem.nowledge.co/latest*',
  ];
}

export async function removeDesktopPauseRoutes({
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

  const expected = desktopPausePatterns(previousVersion);
  const expectedSet = new Set(expected);
  const routes = await listPauseRoutes();
  const unexpected = routes.filter((route) => !expectedSet.has(route.pattern));
  requireCondition(unexpected.length === 0,
    `Refusing unexpected ${SCRIPT_NAME} routes: ${unexpected.map((route) => route.pattern).join(', ')}`);
  for (const pattern of expected) {
    requireCondition(routes.filter((route) => route.pattern === pattern).length <= 1,
      `Duplicate Desktop pause route: ${pattern}`);
  }

  let deleted = 0;
  for (const pattern of expected) {
    const route = routes.find((candidate) => candidate.pattern === pattern);
    if (!route) continue;
    const response = await fetcher(`${api}/${route.id}`, { method: 'DELETE', headers });
    requireCondition(response.status === 200,
      `Delete Desktop pause route ${pattern}: expected 200, got ${response.status}`);
    const body = await response.json();
    requireCondition(body.success === true && body.result?.id === route.id,
      `Delete Desktop pause route ${pattern}: malformed response`);
    deleted += 1;
  }

  const readback = await listPauseRoutes();
  requireCondition(readback.length === 0,
    `Pause Worker still owns routes: ${readback.map((route) => route.pattern).join(', ')}`);
  console.log(`Desktop upgrade pause routes removed: ${deleted}; pause Worker deployment retained`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  removeDesktopPauseRoutes({
    previousVersion: process.env.PREVIOUS_VERSION,
    zoneId: process.env.CLOUDFLARE_ZONE_ID,
    apiToken: process.env.CLOUDFLARE_API_TOKEN,
  }).catch((error) => {
    console.error(`Desktop pause route operation failed: ${error.message}`);
    process.exitCode = 1;
  });
}
