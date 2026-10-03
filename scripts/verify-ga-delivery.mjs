// Release-owned exact-version gate. The Backbone verifier checks transport
// health but intentionally permits missing platforms and older releases.
import { pathToFileURL } from 'node:url';

const FILES = {
  mac: 'aarch64-apple-darwin.dmg',
  'mac-intel': 'x86_64-apple-darwin.dmg',
  win: 'x86_64-pc-windows-msvc.exe',
  linux: 'x86_64-unknown-linux-gnu.AppImage',
  'linux-deb': 'x86_64-unknown-linux-gnu.deb',
  'linux-rpm': 'x86_64-unknown-linux-gnu.rpm',
  'linux-appimage': 'x86_64-unknown-linux-gnu.AppImage',
};
const TARGETS = {
  mac: 'aarch64-apple-darwin',
  'mac-intel': 'x86_64-apple-darwin',
  win: 'x86_64-pc-windows-msvc',
  linux: 'x86_64-unknown-linux-gnu',
  'linux-deb': 'x86_64-unknown-linux-gnu',
  'linux-rpm': 'x86_64-unknown-linux-gnu',
  'linux-appimage': 'x86_64-unknown-linux-gnu',
};
const BROWSER_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36',
  Origin: 'https://mem.nowledge.co',
  Referer: 'https://mem.nowledge.co/download',
};

function requireCondition(ok, message) {
  if (!ok) throw new Error(message);
}

function compareVersions(a, b) {
  const parse = (value) => {
    requireCondition(/^\d+\.\d+\.\d+$/.test(value), `Invalid semver: ${value}`);
    return value.split('.').map(Number);
  };
  const left = parse(a);
  const right = parse(b);
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return Math.sign(left[index] - right[index]);
  }
  return 0;
}

function assertArtifactUrl(value, host, version, file, label) {
  const url = new URL(value);
  requireCondition(url.protocol === 'https:' && url.host === host,
    `${label}: wrong download host ${url.host}`);
  requireCondition(url.pathname === `/app/${version}/${file}`,
    `${label}: expected /app/${version}/${file}, got ${url.pathname}`);
}

export async function verifyVersionPolicy({
  expectedVersion,
  platforms,
  allowNewerLatest = false,
  baseUrl = 'https://backbone-mem.nowledge.co',
  fetcher = fetch,
}) {
  requireCondition(/^\d+\.\d+\.\d+$/.test(expectedVersion || ''),
    'VERIFY_RELEASE_VERSION must be a clean semver');
  requireCondition(platforms?.length > 0, 'VERIFY_PLATFORMS must list required platforms');
  let liveLatest = '';
  for (const platform of platforms) {
    requireCondition(FILES[platform], `Unknown required platform: ${platform}`);
    const response = await fetcher(`${baseUrl.replace(/\/+$/, '')}/latest?platform=${platform}`, {
      headers: BROWSER_HEADERS,
      redirect: 'manual',
    });
    requireCondition(response.status === 200,
      `Preflight latest ${platform}: expected 200, got ${response.status}`);
    const body = await response.json();
    requireCondition(body.platform === platform,
      `Preflight latest ${platform}: platform mismatch`);
    requireCondition(typeof body.version === 'string',
      `Preflight latest ${platform}: missing version`);
    const order = compareVersions(body.version, expectedVersion);
    if (allowNewerLatest) {
      requireCondition(order > 0,
        `Preflight older-patch mode requires ${platform} latest newer than ${expectedVersion}, got ${body.version}`);
      if (liveLatest) {
        requireCondition(body.version === liveLatest,
          `Preflight older-patch mode requires one live latest, got ${body.version} vs ${liveLatest}`);
      }
    } else {
      requireCondition(order <= 0,
        `Preflight refuses ${expectedVersion} behind live ${platform} latest ${body.version}; use an explicit older-patch plan`);
    }
    liveLatest = body.version;
    console.log(`Preflight version policy ${platform}: GA=${expectedVersion}, live latest=${body.version}, older-patch=${allowNewerLatest}`);
  }
}

export async function verifyGADelivery({
  expectedVersion,
  platforms,
  scope,
  allowNewerLatest = false,
  baseUrl = 'https://backbone-mem.nowledge.co',
  downloadHost = 'download-mem.nowledge.co',
  fetcher = fetch,
}) {
  requireCondition(/^\d+\.\d+\.\d+$/.test(expectedVersion || ''),
    'VERIFY_RELEASE_VERSION must be a clean semver');
  requireCondition(platforms.length > 0, 'VERIFY_PLATFORMS must list required platforms');
  for (const platform of platforms) {
    requireCondition(FILES[platform], `Unknown required platform: ${platform}`);
  }
  const base = baseUrl.replace(/\/+$/, '');
  const scopes = new Set(scope);
  for (const required of ['latest', 'latest-redirect', 'direct', 'update']) {
    requireCondition(scopes.has(required), `Missing required verification scope: ${required}`);
  }
  const request = (url, init = {}) => fetcher(url, {
    ...init,
    headers: BROWSER_HEADERS,
    redirect: init.redirect || 'manual',
  });
  let liveLatest = '';
  for (const platform of platforms) {
    const response = await request(`${base}/latest?platform=${platform}`);
    requireCondition(response.status === 200,
      `/latest ${platform}: expected 200, got ${response.status}`);
    const body = await response.json();
    requireCondition(body.platform === platform,
      `/latest ${platform}: platform mismatch`);
    requireCondition(typeof body.version === 'string',
      `/latest ${platform}: missing version`);
    if (allowNewerLatest) {
      requireCondition(compareVersions(body.version, expectedVersion) > 0,
        `/latest ${platform}: older-patch mode requires a version newer than ${expectedVersion}, got ${body.version}`);
    } else {
      requireCondition(body.version === expectedVersion,
        `/latest ${platform}: expected ${expectedVersion}, got ${body.version}`);
    }
    if (liveLatest) {
      requireCondition(body.version === liveLatest,
        `/latest ${platform}: inconsistent version ${body.version} vs ${liveLatest}`);
    } else {
      liveLatest = body.version;
    }
    assertArtifactUrl(body.download_url, downloadHost, liveLatest, FILES[platform],
      `/latest ${platform}`);

    for (const [route, version] of [
      [`download/latest/${platform}`, liveLatest],
      [`download/${expectedVersion}/${platform}`, expectedVersion],
    ]) {
      const head = await request(`${base}/${route}`, { method: 'HEAD', redirect: 'follow' });
      requireCondition(head.status === 200, `/${route}: expected 200, got ${head.status}`);
      assertArtifactUrl(head.url, downloadHost, version, FILES[platform], `/${route}`);
    }
    console.log(`OK exact ${platform}: direct=${expectedVersion}, latest=${liveLatest}`);
  }

  for (const target of new Set(platforms.map((platform) => TARGETS[platform]))) {
    const response = await request(`${base}/update.json?current_version=0.0.0&target=${target}`);
    requireCondition(response.status === 200,
      `/update.json ${target}: expected 200, got ${response.status}`);
    const body = await response.json();
    requireCondition(body.version === liveLatest,
      `/update.json ${target}: expected ${liveLatest}, got ${body.version}`);
    const url = body?.platforms?.[target]?.url;
    const file = target === 'x86_64-unknown-linux-gnu'
      ? FILES['linux-appimage']
      : FILES[platforms.find((platform) => TARGETS[platform] === target)];
    assertArtifactUrl(url, downloadHost, liveLatest, file, `/update.json ${target}`);
    console.log(`OK exact updater ${target}: ${liveLatest}`);
  }

  if (scopes.has('apt')) {
    const response = await request(`https://${downloadHost}/apt/dists/stable/main/binary-amd64/Packages`,
      { redirect: 'follow' });
    requireCondition(response.status === 200, `APT Packages: expected 200, got ${response.status}`);
    const stanzas = (await response.text()).split(/\n\s*\n/);
    const packageStanza = stanzas.find((stanza) => /^Package:\s+nowledge-mem\s*$/m.test(stanza));
    requireCondition(packageStanza, 'APT Packages: nowledge-mem entry missing');
    const version = packageStanza.match(/^Version:\s+(.+)\s*$/m)?.[1]?.trim();
    const file = packageStanza.match(/^Filename:\s+(.+)\s*$/m)?.[1]?.trim();
    requireCondition(version === expectedVersion,
      `APT Packages: expected ${expectedVersion}, got ${version || '<missing>'}`);
    requireCondition(file === `pool/nowledge-mem_${expectedVersion}_amd64.deb`,
      `APT Packages: wrong pool file ${file || '<missing>'}`);
    console.log(`OK exact APT: ${expectedVersion}`);
  }
  console.log(`Exact GA delivery verified: ${expectedVersion}; latest=${liveLatest}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const options = {
    expectedVersion: process.env.VERIFY_RELEASE_VERSION,
    platforms: (process.env.VERIFY_PLATFORMS || '').split(',').map((value) => value.trim()).filter(Boolean),
    scope: (process.env.VERIFY_SCOPE || '').split(',').map((value) => value.trim()).filter(Boolean),
    allowNewerLatest: process.env.VERIFY_ALLOW_NEWER_LATEST === 'true',
    baseUrl: process.env.VERIFY_BASE_URL,
    downloadHost: process.env.EXPECT_CUSTOM_DOMAIN,
  };
  const run = process.argv.includes('--preflight')
    ? verifyVersionPolicy(options)
    : verifyGADelivery(options);
  run.catch((error) => {
    console.error(`Exact GA delivery failed: ${error.message}`);
    process.exitCode = 1;
  });
}
