import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

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

export async function verifyUpgradePause({
  platforms,
  previousVersion,
  baseUrl = 'https://backbone-mem.nowledge.co',
  aptBaseUrl = 'https://download-mem.nowledge.co/apt',
  includeApt = true,
  includeControlled = true,
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
  const request = (url) => fetcher(url, {
    headers: {
      'User-Agent': `Nowledge-Mem/${previousVersion} (Tauri)`,
      Origin: 'https://mem.nowledge.co',
      Referer: 'https://mem.nowledge.co/download',
    },
    redirect: 'manual',
  });

  for (const platform of platforms) {
    await assertPaused(await request(`${base}/latest?platform=${platform}`),
      `/latest ${platform}`);
    await assertPaused(await request(`${base}/download/latest/${platform}`),
      `/download/latest/${platform}`);

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

  for (const target of new Set(platforms.map((platform) => TARGETS[platform]))) {
    assertNoUpdate(await request(
      `${base}/update.json?current_version=${previousVersion}&target=${target}`,
    ), `/update.json ${target}`);
  }

  if (includeApt) {
    const apt = aptBaseUrl.replace(/\/+$/, '');
    await assertPaused(await request(`${apt}/dists/stable/main/binary-amd64/Packages`),
      'APT Packages');
  }

  console.log(`Upgrade pause verified for ${platforms.length} platforms; apt=${includeApt}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  verifyUpgradePause({
    platforms: (process.env.VERIFY_PLATFORMS || '')
      .split(',').map((value) => value.trim()).filter(Boolean),
    previousVersion: process.env.VERIFY_PREVIOUS_DESKTOP_VERSION,
    baseUrl: process.env.VERIFY_BASE_URL,
    aptBaseUrl: process.env.VERIFY_APT_BASE_URL,
    includeApt: !process.argv.includes('--desktop-only'),
    includeControlled: !process.argv.includes('--legacy-only'),
  }).catch((error) => {
    console.error(`Upgrade pause verification failed: ${error.message}`);
    process.exitCode = 1;
  });
}
