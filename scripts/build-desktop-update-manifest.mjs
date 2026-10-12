#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const ARTIFACTS = [
  ['mac', (version) => `Nowledge.Mem_${version}_aarch64.dmg`, 'aarch64-apple-darwin.dmg'],
  ['mac_intel', (version) => `Nowledge.Mem_${version}_x64.dmg`, 'x86_64-apple-darwin.dmg'],
  ['win', (version) => `Nowledge.Mem_${version}_x64-setup.exe`, 'x86_64-pc-windows-msvc.exe'],
  ['linux-appimage', (version) => `Nowledge.Mem_${version}_amd64.AppImage`, 'x86_64-unknown-linux-gnu.AppImage'],
  ['linux-deb', (version) => `Nowledge.Mem_${version}_amd64.deb`, 'x86_64-unknown-linux-gnu.deb'],
  ['linux-rpm', (version) => `Nowledge.Mem-${version}-1.x86_64.rpm`, 'x86_64-unknown-linux-gnu.rpm'],
];

function requireCondition(ok, message) {
  if (!ok) throw new Error(message);
}

function unquoteEtag(value) {
  return String(value || '').replace(/^"|"$/g, '');
}

export async function buildDesktopUpdateManifest({
  version,
  sourceSha,
  releaseNotes,
  pubDate,
  release,
  headObject,
}) {
  requireCondition(/^\d+\.\d+\.\d+$/.test(version || ''), 'Version must be clean semver');
  requireCondition(/^[a-f0-9]{40}$/.test(sourceSha || ''), 'Source SHA must be 40 lowercase hex characters');
  requireCondition(typeof releaseNotes === 'string' && releaseNotes.length <= 8000,
    'Release notes must be a bounded string');
  requireCondition(Number.isFinite(Date.parse(pubDate || '')), 'Publication date must be ISO-compatible');
  requireCondition(Array.isArray(release?.assets) && release.assets.length === ARTIFACTS.length,
    'GA Release must contain exactly six assets');

  const artifacts = {};
  for (const [platform, assetName, target] of ARTIFACTS) {
    const name = assetName(version);
    const matching = release.assets.filter((asset) => asset.name === name);
    requireCondition(matching.length === 1, `Expected one Release asset named ${name}`);
    const asset = matching[0];
    const digest = String(asset.digest || '');
    requireCondition(/^sha256:[a-f0-9]{64}$/.test(digest), `Missing SHA-256 digest for ${name}`);
    requireCondition(Number.isSafeInteger(asset.size) && asset.size > 0, `Invalid size for ${name}`);
    const key = `app/${version}/${target}`;
    const head = await headObject(key);
    const etag = unquoteEtag(head?.ETag ?? head?.etag);
    const size = head?.ContentLength ?? head?.size;
    requireCondition(size === asset.size, `R2 size differs from Release asset for ${name}`);
    requireCondition(etag.length > 0 && etag.length <= 128, `Invalid R2 ETag for ${name}`);
    artifacts[platform] = { key, size, etag, sha256: digest.slice('sha256:'.length) };
  }

  return {
    version,
    source_sha: sourceSha,
    release_notes: releaseNotes,
    pub_date: new Date(pubDate).toISOString(),
    artifacts,
  };
}

async function main() {
  const { values } = parseArgs({ options: {
    version: { type: 'string' },
    'source-sha': { type: 'string' },
    'release-notes': { type: 'string' },
    'pub-date': { type: 'string' },
    'release-json': { type: 'string' },
    output: { type: 'string' },
  } });
  for (const key of ['version', 'source-sha', 'release-notes', 'pub-date', 'release-json', 'output']) {
    requireCondition(values[key], `--${key} is required`);
  }
  const { S3Client, HeadObjectCommand } = await import('@aws-sdk/client-s3');
  const accountId = process.env.CLOUDFLARE_ACCOUNT_ID;
  const bucket = process.env.R2_BUCKET_NAME;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY;
  requireCondition(accountId && bucket && accessKeyId && secretAccessKey,
    'R2 credentials and account metadata are required');
  const client = new S3Client({
    region: 'auto',
    endpoint: `https://${accountId}.r2.cloudflarestorage.com`,
    credentials: { accessKeyId, secretAccessKey },
  });
  const release = JSON.parse(await readFile(values['release-json'], 'utf8'));
  const manifest = await buildDesktopUpdateManifest({
    version: values.version,
    sourceSha: values['source-sha'],
    releaseNotes: values['release-notes'],
    pubDate: values['pub-date'],
    release,
    headObject: (key) => client.send(new HeadObjectCommand({ Bucket: bucket, Key: key })),
  });
  await writeFile(values.output, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(`Desktop update manifest prepared for ${manifest.version} with six exact R2 objects`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(`Desktop update manifest failed: ${error.message}`);
    process.exitCode = 1;
  });
}
