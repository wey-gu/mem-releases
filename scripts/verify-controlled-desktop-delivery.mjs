import { randomBytes } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const files = {
  mac: 'aarch64-apple-darwin.dmg',
  mac_intel: 'x86_64-apple-darwin.dmg',
  win: 'x86_64-pc-windows-msvc.exe',
  'linux-appimage': 'x86_64-unknown-linux-gnu.AppImage',
  'linux-deb': 'x86_64-unknown-linux-gnu.deb',
  'linux-rpm': 'x86_64-unknown-linux-gnu.rpm',
};
function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}
const semver = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

// Read-only qualification of the protocol used by the shipped native client.
// The manifest must come from qualified release bytes, not the endpoint under test.
export async function verifyControlledDesktopDelivery({
  manifest, previousVersion, baseUrl = 'https://backbone-mem.nowledge.co',
  downloadHost = 'download-mem.nowledge.co', fetcher = fetch,
  clock = () => performance.now(), requestId = () => randomBytes(24).toString('hex'),
}) {
  requireCondition(semver.test(manifest?.version ?? ''), 'Manifest needs a clean release version');
  requireCondition(/^[a-f0-9]{40}$/.test(manifest.source_sha ?? ''), 'Manifest needs its qualified source SHA');
  requireCondition(semver.test(previousVersion ?? ''), 'Previous native desktop version is required');
  const compare = manifest.version.split('.').map(Number);
  const previous = previousVersion.split('.').map(Number);
  requireCondition([...compare, ...previous].every(Number.isSafeInteger), 'Version components must be safe integers');
  const differing = compare.findIndex((v, i) => v !== previous[i]);
  requireCondition(differing >= 0 && compare[differing] > previous[differing],
    'Previous native version must be older than the release');
  requireCondition(Object.keys(manifest.artifacts ?? {}).sort().join() === Object.keys(files).sort().join(),
    'Qualified manifest must contain exactly all six native platforms');
  const base = new URL(baseUrl);
  requireCondition(base.protocol === 'https:' && !base.username && !base.password &&
    base.pathname === '/' && !base.search && !base.hash, 'Base must be a bare HTTPS origin');
  const ids = new Set();
  const receipts = [];
  for (const [platform, file] of Object.entries(files)) {
    const expected = manifest.artifacts[platform];
    const key = `app/${manifest.version}/${file}`;
    requireCondition(expected?.key === key && Number.isSafeInteger(expected.size) && expected.size > 0 &&
      /^[a-f0-9]{64}$/.test(expected.sha256 ?? ''), `${platform}: invalid qualified artifact`);
    const id = requestId();
    requireCondition(/^[a-f0-9-]{1,80}$/.test(id) && !ids.has(id), 'Request IDs must be valid and unique');
    ids.add(id);
    const url = new URL('/latest', base);
    url.search = new URLSearchParams({ platform, update_control: '1', request_id: id }).toString();
    const start = clock();
    const response = await fetcher(url, {
      headers: { 'User-Agent': `Nowledge-Mem/${previousVersion} (Tauri)`, 'Cache-Control': 'no-cache' },
      redirect: 'error', signal: AbortSignal.timeout(15_000),
    });
    requireCondition(response.status === 200, `${platform}: controlled lookup HTTP ${response.status}`);
    requireCondition(response.headers.get('cache-control')?.split(',').some(v => v.trim().toLowerCase() === 'no-store'),
      `${platform}: controlled decision must be no-store`);
    const text = await response.text();
    requireCondition(Buffer.byteLength(text) <= 32_768, `${platform}: controlled response exceeds native body limit`);
    const body = JSON.parse(text);
    const control = body.control;
    requireCondition(control?.protocol === 1 && control.status === 'allowed' &&
      Number.isSafeInteger(control.revision) && control.revision >= 0,
      `${platform}: controlled decision is not an allowed protocol-1 response`);
    requireCondition(control.request_id === id, `${platform}: controlled request ID mismatch`);
    const rfc3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
    requireCondition(typeof control.issued_at === 'string' && typeof control.expires_at === 'string' &&
      rfc3339.test(control.issued_at) && rfc3339.test(control.expires_at), `${platform}: decision timestamps must be native RFC3339`);
    const issued = Date.parse(control.issued_at), expires = Date.parse(control.expires_at);
    const elapsed = clock() - start;
    requireCondition(Number.isFinite(issued) && Number.isFinite(expires) && expires - issued === 60_000 &&
      elapsed >= 0 && elapsed < 60_000, `${platform}: invalid or exhausted native decision lifetime`);
    requireCondition(body.version === manifest.version, `${platform}: controlled version expected ${manifest.version}, got ${body.version}`);
    requireCondition(body.platform === platform && body.target === file.slice(0, file.lastIndexOf('.')),
      `${platform}: native platform/target mismatch`);
    const download = new URL(body.download_url);
    requireCondition(download.protocol === 'https:' && download.host === downloadHost &&
      !download.username && !download.password && !download.search && !download.hash && download.pathname === `/${key}`,
      `${platform}: controlled artifact URL mismatch`);
    requireCondition(body.file_size === expected.size && control.sha256 === expected.sha256,
      `${platform}: controlled artifact size/SHA-256 mismatch`);
    receipts.push({ platform, version: body.version, revision: control.revision, request_id: id,
      target: body.target, size: body.file_size, sha256: control.sha256 });
  }
  requireCondition(new Set(receipts.map(r => r.revision)).size === 1,
    'Authority changed during qualification; rerun against a consistent revision');
  return receipts;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    requireCondition(process.env.VERIFY_ARTIFACT_MANIFEST, 'VERIFY_ARTIFACT_MANIFEST is required');
    const receipts = await verifyControlledDesktopDelivery({
      manifest: JSON.parse(await readFile(process.env.VERIFY_ARTIFACT_MANIFEST, 'utf8')),
      previousVersion: process.env.VERIFY_PREVIOUS_DESKTOP_VERSION,
      baseUrl: process.env.VERIFY_BASE_URL,
      downloadHost: process.env.EXPECT_CUSTOM_DOMAIN,
    });
    console.log(JSON.stringify({ status: 'verified', receipts }, null, 2));
  } catch (error) {
    console.error(`Controlled Desktop delivery failed: ${error.message}`);
    process.exitCode = 1;
  }
}
