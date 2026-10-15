import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { ARTIFACTS } from './build-desktop-update-manifest.mjs';
import { compareVersions } from './desktop-upgrade-pause-worker.mjs';

function requireCondition(ok, message) {
  if (!ok) throw new Error(message);
}

export async function prepareDesktopPause({ pausedVersion, releases, config, fetcher = fetch }) {
  compareVersions(pausedVersion, pausedVersion);
  requireCondition(Array.isArray(releases) && releases.every(Array.isArray),
    'Expected complete paginated GitHub release arrays');
  const all = releases.flat();
  const withdrawn = all.filter((release) => release.tag_name === `v${pausedVersion}`);
  requireCondition(withdrawn.length === 1 && withdrawn[0].draft === true
    && withdrawn[0].prerelease === false,
  'The paused GA must exist as one withdrawn draft release');

  const candidates = all.filter((release) => {
    if (release.draft !== false || release.prerelease !== false
        || !/^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(release.tag_name || '')
        || !/^\d{4}-\d{2}-\d{2}T/.test(release.published_at || '')
        || !Number.isFinite(Date.parse(release.published_at))) return false;
    return compareVersions(release.tag_name.slice(1), pausedVersion) < 0;
  }).sort((a, b) => compareVersions(b.tag_name.slice(1), a.tag_name.slice(1)));

  for (const release of candidates) {
    const version = release.tag_name.slice(1);
    if (!Array.isArray(release.assets)) continue;
    const assets = ARTIFACTS.map(([, assetName]) =>
      release.assets.filter((asset) => asset.name === assetName(version)));
    if (!assets.every((matches) => matches.length === 1 && matches[0].state === 'uploaded'
        && Number.isSafeInteger(matches[0].size) && matches[0].size > 0)) continue;
    let available = true;
    for (let i = 0; i < ARTIFACTS.length; i += 1) {
      const response = await fetcher(
        `https://download-mem.nowledge.co/app/${version}/${ARTIFACTS[i][2]}`,
        { method: 'HEAD', redirect: 'manual', signal: AbortSignal.timeout(15000) },
      );
      if (response.status === 404 || response.status === 410) { available = false; break; }
      requireCondition(response.status === 200,
        `Cannot verify fallback artifact: HTTP ${response.status}`);
      if (Number(response.headers.get('content-length')) !== assets[i][0].size) {
        available = false;
        break;
      }
    }
    if (!available) continue;
    const routes = config.routes.map((route) => ({
      ...route, pattern: route.pattern.replace('__PAUSED_VERSION__', pausedVersion),
    }));
    requireCondition(config.routes.filter((route) => route.pattern.includes('__PAUSED_VERSION__')).length === 2,
      'Pause template must contain two exact version route placeholders');
    return {
      paused_version: pausedVersion,
      fallback_version: version,
      fallback_published_at: new Date(release.published_at).toISOString(),
      fallback_release_id: release.id,
      fallback_artifacts: ARTIFACTS.map(([platform, , file], i) => ({
        platform, url: `https://download-mem.nowledge.co/app/${version}/${file}`,
        size: assets[i][0].size,
      })),
      config: { ...config, routes },
    };
  }
  throw new Error('No older published GA has all six available Desktop artifacts');
}

async function main() {
  const { values } = parseArgs({ options: {
    'paused-version': { type: 'string' }, 'releases-json': { type: 'string' },
    config: { type: 'string' }, output: { type: 'string' }, plan: { type: 'string' },
  } });
  for (const key of ['paused-version', 'releases-json', 'config', 'output', 'plan']) {
    requireCondition(values[key], `--${key} is required`);
  }
  const configPath = resolve(values.config);
  const config = JSON.parse(await readFile(configPath, 'utf8'));
  // Generated configs may live outside the repository; keep the source path exact.
  config.main = resolve(dirname(configPath), config.main);
  const prepared = await prepareDesktopPause({
    pausedVersion: values['paused-version'], config,
    releases: JSON.parse(await readFile(values['releases-json'], 'utf8')),
  });
  await writeFile(values.output, `${JSON.stringify(prepared.config, null, 2)}\n`);
  const { config: generated, ...plan } = prepared;
  await writeFile(values.plan, `${JSON.stringify(plan, null, 2)}\n`);
  console.log(`Desktop ${plan.paused_version} pause prepared; downloads fall back to ${plan.fallback_version}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(`Desktop pause preparation failed: ${error.message}`);
    process.exitCode = 1;
  });
}
