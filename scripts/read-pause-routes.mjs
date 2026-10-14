// Read-only view of the emergency upgrade-pause Worker routes. The one-click
// promotion uses the exact route set to decide which pause stages are done.
import { pathToFileURL } from 'node:url';

const SCRIPT_NAME = 'nowledge-mem-upgrade-pause';

export async function readPauseRoutes({ zoneId, apiToken, fetcher = fetch }) {
  if (!/^[0-9a-f]{32}$/.test(zoneId || '')) throw new Error('CLOUDFLARE_ZONE_ID is invalid');
  if (!apiToken) throw new Error('CLOUDFLARE_API_TOKEN is required');
  const response = await fetcher(
    `https://api.cloudflare.com/client/v4/zones/${zoneId}/workers/routes?per_page=100`,
    { headers: { Authorization: `Bearer ${apiToken}` } },
  );
  if (response.status !== 200) throw new Error(`List Worker routes: expected 200, got ${response.status}`);
  const body = await response.json();
  if (body.success !== true || !Array.isArray(body.result)) {
    throw new Error('List Worker routes: malformed response');
  }
  if ((body.result_info?.total_pages || 1) !== 1) {
    throw new Error('List Worker routes: pagination would hide routes');
  }
  const patterns = body.result.filter((route) => route.script === SCRIPT_NAME)
    .map((route) => route.pattern);
  if (new Set(patterns).size !== patterns.length) throw new Error('Duplicate pause routes');
  return patterns.sort();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  readPauseRoutes({
    zoneId: process.env.CLOUDFLARE_ZONE_ID,
    apiToken: process.env.CLOUDFLARE_API_TOKEN,
  }).then((patterns) => console.log(JSON.stringify(patterns))).catch((error) => {
    console.error(`Pause route read failed: ${error.message}`);
    process.exitCode = 1;
  });
}
