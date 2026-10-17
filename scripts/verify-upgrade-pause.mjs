import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { ARTIFACTS } from './build-desktop-update-manifest.mjs';

const TARGETS = {
  mac: 'aarch64-apple-darwin',
  'mac-intel': 'x86_64-apple-darwin',
  win: 'x86_64-pc-windows-msvc',
  linux: 'x86_64-unknown-linux-gnu',
  'linux-deb': 'x86_64-unknown-linux-gnu',
  'linux-rpm': 'x86_64-unknown-linux-gnu',
  'linux-appimage': 'x86_64-unknown-linux-gnu',
};
const CONTROL_PLATFORMS = {
  mac: 'mac',
  'mac-intel': 'mac_intel',
  win: 'win',
  linux: 'linux-appimage',
  'linux-deb': 'linux-deb',
  'linux-rpm': 'linux-rpm',
  'linux-appimage': 'linux-appimage',
};
const FILES = Object.fromEntries(ARTIFACTS.map(([platform, , file]) => [platform, file]));
function artifactUrl(platform, version) {
  return `https://download-mem.nowledge.co/app/${version}/${FILES[CONTROL_PLATFORMS[platform]]}`;
}

function requireCondition(ok, message) {
  if (!ok) throw new Error(message);
}

function assertPauseHeaders(response, label) {
  requireCondition((response.headers.get('cache-control') || '').toLowerCase().includes('no-store'),
    `${label}: cache-control must include no-store`);
  requireCondition(/^\d+$/.test(response.headers.get('retry-after') || ''),
    `${label}: retry-after must be an integer`);
  requireCondition(Boolean(response.headers.get('x-mem-upgrade-pause')),
    `${label}: x-mem-upgrade-pause is missing`);
}

async function assertPaused(response, label) {
  requireCondition(response.status === 503, `${label}: expected 503, got ${response.status}`);
  assertPauseHeaders(response, label);
  const body = await response.text();
  requireCondition(/temporarily paused/i.test(body), `${label}: pause body is missing`);
}

function assertNoUpdate(response, label) {
  requireCondition(response.status === 204, `${label}: expected 204, got ${response.status}`);
  assertPauseHeaders(response, label);
}

function assertFallbackHeaders(response, version, label) {
  requireCondition((response.headers.get('cache-control') || '').toLowerCase().includes('no-store'),
    `${label}: cache-control must include no-store`);
  requireCondition(Boolean(response.headers.get('x-mem-upgrade-pause')),
    `${label}: x-mem-upgrade-pause is missing`);
  requireCondition(response.headers.get('x-mem-latest-fallback') === version,
    `${label}: expected fallback marker ${version}`);
}

async function assertFallbackLatest(response, platform, version, label) {
  requireCondition(response.status === 200, `${label}: expected 200, got ${response.status}`);
  assertFallbackHeaders(response, version, label);
  const body = await response.json();
  requireCondition(body?.platform === platform,
    `${label}: expected platform ${platform}, got ${body?.platform || '<missing>'}`);
  requireCondition(body?.version === version,
    `${label}: expected version ${version}, got ${body?.version || '<missing>'}`);
  requireCondition(body?.download_url === artifactUrl(platform, version),
    `${label}: fallback download does not target the exact platform artifact`);
}

function assertFallbackRedirect(response, platform, version, label) {
  requireCondition(response.status === 302, `${label}: expected 302, got ${response.status}`);
  assertFallbackHeaders(response, version, label);
  requireCondition(response.headers.get('location') === artifactUrl(platform, version),
    `${label}: fallback redirect does not target the exact platform artifact`);
}

async function verifyArtifactRange(artifact, headers, fetcher) {
  const response = await fetcher(artifact.url, {
    headers: { ...headers, Range: 'bytes=0-0' }, redirect: 'manual', signal: AbortSignal.timeout(15000),
  });
  try {
    requireCondition(response.status === 206 || response.status === 200,
      `Fallback range GET: expected 200/206, got ${response.status}`);
    if (response.status === 206) {
      requireCondition(response.headers.get('content-range') === `bytes 0-0/${artifact.size}`,
        'Fallback range size differs from the selected pause plan');
      requireCondition(response.headers.get('content-length') === '1', 'Fallback range length must be 1');
    } else {
      // HTTP permits a server to ignore Range. Read one chunk and cancel, never the full installer.
      requireCondition(Number(response.headers.get('content-length')) === artifact.size,
        'Fallback GET size differs from the selected pause plan');
    }
    requireCondition(response.body, 'Fallback range body is missing');
    const reader = response.body.getReader();
    try {
      const first = await reader.read();
      requireCondition(!first.done && first.value.byteLength > 0, 'Fallback range body is empty');
      if (response.status === 206) {
        requireCondition(first.value.byteLength === 1, 'Fallback range body must contain one byte');
        requireCondition((await reader.read()).done, 'Fallback range body exceeds one byte');
      }
    } finally { await reader.cancel(); }
  } finally { if (response.body && !response.bodyUsed) await response.body.cancel(); }
}

export async function verifyUpgradePause({
  platforms,
  previousVersion,
  baseUrl = 'https://backbone-mem.nowledge.co',
  aptBaseUrl = 'https://download-mem.nowledge.co/apt',
  includeApt = true,
  includeControlled = true,
  includeLegacy = true,
  fallbackVersion = '',
  fallbackArtifacts,
  fetcher = fetch,
  requestIdFactory = randomUUID,
}) {
  requireCondition(platforms?.length > 0, 'VERIFY_PLATFORMS must list required platforms');
  requireCondition(/^\d+\.\d+\.\d+$/.test(previousVersion || ''),
    'VERIFY_PREVIOUS_DESKTOP_VERSION must be a clean semver');
  for (const platform of platforms) {
    requireCondition(TARGETS[platform], `Unknown required platform: ${platform}`);
  }
  const base = baseUrl.replace(/\/+$/, '');
  const headers = {
      'User-Agent': `Nowledge-Mem/${previousVersion} (Tauri)`,
      Origin: 'https://mem.nowledge.co',
      Referer: 'https://mem.nowledge.co/download',
  };
  const request = (url) => fetcher(url, {
    headers,
    redirect: 'manual',
  });

  for (const platform of platforms) {
    if (includeLegacy) {
      if (fallbackVersion) {
        requireCondition(/^\d+\.\d+\.\d+$/.test(fallbackVersion),
          'VERIFY_FALLBACK_VERSION must be a clean semver');
        await assertFallbackLatest(await request(`${base}/latest?platform=${platform}`),
          platform, fallbackVersion, `/latest ${platform}`);
        assertFallbackRedirect(await request(`${base}/download/latest/${platform}`),
          platform, fallbackVersion, `/download/latest/${platform}`);
        if (fallbackArtifacts) {
          const matching = fallbackArtifacts.filter((a) => a.platform === CONTROL_PLATFORMS[platform]);
          requireCondition(matching.length === 1 && matching[0].url === artifactUrl(platform, fallbackVersion)
            && Number.isSafeInteger(matching[0].size) && matching[0].size > 0,
          `Missing or invalid pause plan artifact for ${platform}`);
          await verifyArtifactRange(matching[0], headers, fetcher);
        }
      } else {
        await assertPaused(await request(`${base}/latest?platform=${platform}`),
          `/latest ${platform}`);
        await assertPaused(await request(`${base}/download/latest/${platform}`),
          `/download/latest/${platform}`);
      }
    }

    if (includeControlled) {
      const requestId = requestIdFactory();
      const controlPlatform = CONTROL_PLATFORMS[platform];
      const controlled = await request(
        `${base}/latest?platform=${controlPlatform}&update_control=1&request_id=${requestId}`,
      );
      requireCondition(controlled.status === 200,
        `controlled /latest ${controlPlatform}: expected 200, got ${controlled.status}`);
      requireCondition((controlled.headers.get('cache-control') || '').toLowerCase().includes('no-store'),
        `controlled /latest ${controlPlatform}: cache-control must include no-store`);
      const body = await controlled.json();
      requireCondition(body?.control?.protocol === 1,
        `controlled /latest ${controlPlatform}: expected protocol 1`);
      requireCondition(body?.control?.status === 'stopped',
        `controlled /latest ${controlPlatform}: expected stopped, got ${body?.control?.status || '<missing>'}`);
      requireCondition(Number.isSafeInteger(body?.control?.revision) && body.control.revision >= 0,
        `controlled /latest ${controlPlatform}: invalid revision`);
      requireCondition(body?.control?.request_id === requestId,
        `controlled /latest ${controlPlatform}: request ID mismatch`);
      requireCondition(body.version === '' && body.download_url === '',
        `controlled /latest ${controlPlatform}: stopped response exposed a release`);
    }
  }

  if (includeLegacy) {
    for (const target of new Set(platforms.map((platform) => TARGETS[platform]))) {
      assertNoUpdate(await request(
        `${base}/update.json?current_version=${previousVersion}&target=${target}`,
      ), `/update.json ${target}`);
    }
  }

  if (includeApt) {
    const apt = aptBaseUrl.replace(/\/+$/, '');
    await assertPaused(await request(`${apt}/dists/stable/main/binary-amd64/Packages`),
      'APT Packages');
  }

  console.log(`Upgrade pause verified for ${platforms.length} platforms; apt=${includeApt}`);
}

async function main() {
  let plan;
  if (process.env.VERIFY_FALLBACK_PLAN) {
    plan = JSON.parse(await readFile(process.env.VERIFY_FALLBACK_PLAN, 'utf8'));
    requireCondition(plan.fallback_version === process.env.VERIFY_FALLBACK_VERSION
      && Array.isArray(plan.fallback_artifacts), 'Fallback pause plan does not match the selected version');
  }
  await verifyUpgradePause({
    platforms: (process.env.VERIFY_PLATFORMS || '')
      .split(',').map((value) => value.trim()).filter(Boolean),
    previousVersion: process.env.VERIFY_PREVIOUS_DESKTOP_VERSION,
    baseUrl: process.env.VERIFY_BASE_URL,
    aptBaseUrl: process.env.VERIFY_APT_BASE_URL,
    includeApt: !process.argv.includes('--desktop-only'),
    includeControlled: !process.argv.includes('--legacy-only'),
    includeLegacy: !process.argv.includes('--controlled-only'),
    fallbackVersion: process.env.VERIFY_FALLBACK_VERSION,
    fallbackArtifacts: plan?.fallback_artifacts,
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(`Upgrade pause verification failed: ${error.message}`);
    process.exitCode = 1;
  });
}
