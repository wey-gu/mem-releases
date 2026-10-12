import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const PLATFORMS = {
  mac: 'aarch64-apple-darwin',
  mac_intel: 'x86_64-apple-darwin',
  win: 'x86_64-pc-windows-msvc',
  'linux-appimage': 'x86_64-unknown-linux-gnu',
  'linux-deb': 'x86_64-unknown-linux-gnu',
  'linux-rpm': 'x86_64-unknown-linux-gnu',
};

function requireCondition(ok, message) {
  if (!ok) throw new Error(message);
}

export async function verifyControlledDesktopUpdate({
  manifest,
  expectedRevision,
  previousVersion,
  baseUrl = 'https://backbone-mem.nowledge.co',
  downloadHost = 'download-mem.nowledge.co',
  fetcher = fetch,
  requestIdFactory = () => crypto.randomUUID(),
}) {
  requireCondition(/^\d+\.\d+\.\d+$/.test(previousVersion || ''),
    'Previous version must be clean semver');
  requireCondition(Number.isSafeInteger(expectedRevision) && expectedRevision > 0,
    'Expected revision must be a positive integer');
  requireCondition(manifest?.version && manifest?.artifacts,
    'Reviewed Desktop update manifest is required');
  const base = baseUrl.replace(/\/+$/, '');
  const userAgent = `Nowledge-Mem/${previousVersion} (Tauri)`;

  for (const [platform, target] of Object.entries(PLATFORMS)) {
    const requestId = requestIdFactory();
    requireCondition(/^[a-f0-9-]{1,80}$/.test(requestId), 'Request ID is invalid');
    const response = await fetcher(
      `${base}/latest?platform=${platform}&update_control=1&request_id=${requestId}`,
      { headers: { 'User-Agent': userAgent }, redirect: 'error' },
    );
    requireCondition(response.status === 200,
      `Controlled latest ${platform}: expected 200, got ${response.status}`);
    requireCondition((response.headers.get('Cache-Control') || '').toLowerCase().includes('no-store'),
      `Controlled latest ${platform}: no-store is missing`);
    requireCondition(!response.headers.has('X-Mem-Upgrade-Pause'),
      `Controlled latest ${platform}: emergency pause header remains`);
    const body = await response.json();
    const artifact = manifest.artifacts[platform];
    requireCondition(artifact, `Controlled latest ${platform}: manifest artifact is missing`);
    requireCondition(body.version === manifest.version,
      `Controlled latest ${platform}: expected ${manifest.version}, got ${body.version}`);
    requireCondition(body.platform === platform, `Controlled latest ${platform}: platform mismatch`);
    requireCondition(body.target === target, `Controlled latest ${platform}: target mismatch`);
    requireCondition(body.file_size === artifact.size, `Controlled latest ${platform}: size mismatch`);
    requireCondition(body.release_notes === manifest.release_notes,
      `Controlled latest ${platform}: release notes mismatch`);
    requireCondition(body.pub_date === manifest.pub_date,
      `Controlled latest ${platform}: publication date mismatch`);
    const download = new URL(body.download_url);
    requireCondition(download.protocol === 'https:' && download.host === downloadHost,
      `Controlled latest ${platform}: download host mismatch`);
    requireCondition(download.pathname === `/${artifact.key}`,
      `Controlled latest ${platform}: download path mismatch`);
    const control = body.control;
    requireCondition(control?.protocol === 1 && control.status === 'allowed',
      `Controlled latest ${platform}: expected protocol-1 allowed`);
    requireCondition(control.revision === expectedRevision,
      `Controlled latest ${platform}: revision mismatch`);
    requireCondition(control.request_id === requestId,
      `Controlled latest ${platform}: request ID mismatch`);
    requireCondition(control.sha256 === artifact.sha256,
      `Controlled latest ${platform}: SHA-256 mismatch`);
    const issuedAt = Date.parse(control.issued_at);
    const expiresAt = Date.parse(control.expires_at);
    requireCondition(Number.isFinite(issuedAt) && expiresAt - issuedAt === 60_000,
      `Controlled latest ${platform}: invalid validity window`);
    requireCondition(expiresAt > Date.now(),
      `Controlled latest ${platform}: response is already expired`);
    console.log(`OK controlled ${platform}: ${manifest.version}, revision ${expectedRevision}`);
  }
  console.log(`Protocol-1 Desktop delivery verified for six platforms: ${manifest.version}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const manifestPath = process.env.VERIFY_DESKTOP_MANIFEST;
  const manifest = manifestPath ? JSON.parse(await readFile(manifestPath, 'utf8')) : null;
  verifyControlledDesktopUpdate({
    manifest,
    expectedRevision: Number(process.env.VERIFY_AUTHORITY_REVISION),
    previousVersion: process.env.VERIFY_PREVIOUS_DESKTOP_VERSION,
    baseUrl: process.env.VERIFY_BASE_URL,
    downloadHost: process.env.EXPECT_CUSTOM_DOMAIN,
  }).catch((error) => {
    console.error(`Controlled Desktop update verification failed: ${error.message}`);
    process.exitCode = 1;
  });
}
