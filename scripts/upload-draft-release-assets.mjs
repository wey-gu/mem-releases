// Reconcile a draft GA Release after a partially successful asset upload.
// Existing bytes are immutable: skip only an identical SHA-256, never clobber.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { basename } from 'node:path';
import { pathToFileURL } from 'node:url';

export async function sha256File(path) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return `sha256:${hash.digest('hex')}`;
}

function runGh(args) {
  return execFileSync('gh', args, { encoding: 'utf8' });
}

function readDraft(gh, tag, repo) {
  const release = JSON.parse(gh(['release', 'view', tag, '-R', repo,
    '--json', 'isDraft,assets']));
  if (release.isDraft !== true) {
    throw new Error(`${tag} is published; refusing to change its assets`);
  }
  return release;
}

// GitHub stores an uploaded file under a sanitized name: "Nowledge Mem_x.dmg"
// becomes "Nowledge.Mem_x.dmg". Looking assets up by the local name never
// found the uploaded copy, so 0.10.108 (run 40249609661) reported "uploaded
// asset SHA-256 does not match" for an intact upload and every rerun then
// collided with "asset already exists".
export function githubAssetName(name) {
  return name.replace(/ /g, '.');
}

function existingAsset(release, name) {
  const stored = githubAssetName(name);
  const matches = release.assets.filter((asset) => asset.name === name || asset.name === stored);
  if (matches.length > 1) throw new Error(`${name}: duplicate assets on Release`);
  return matches[0];
}

// An asset read right after `gh release upload` may not carry its digest yet
// (GitHub fills it in asynchronously). Wait for it before comparing; a digest
// that never appears still fails.
const DIGEST_ATTEMPTS = 24;
const DIGEST_WAIT_MS = 5000;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function settledAsset(gh, tag, repo, name, sleep) {
  let asset = existingAsset(readDraft(gh, tag, repo), name);
  for (let attempt = 1; asset && !asset.digest && attempt < DIGEST_ATTEMPTS; attempt += 1) {
    await sleep(DIGEST_WAIT_MS);
    asset = existingAsset(readDraft(gh, tag, repo), name);
  }
  return asset;
}

export async function reconcileDraftAssets({ tag, repo, files, gh = runGh,
  digestFile = sha256File, sleep = pause }) {
  if (!tag || !repo || files.length === 0) throw new Error('tag, repo and files required');
  const names = files.map((file) => basename(file));
  if (new Set(names).size !== names.length) throw new Error('duplicate local asset names');

  for (const [index, file] of files.entries()) {
    const name = names[index];
    const expected = await digestFile(file);
    const found = await settledAsset(gh, tag, repo, name, sleep);
    if (found) {
      if (found.digest !== expected) {
        throw new Error(`${name}: existing asset SHA-256 differs (${found.digest} != ${expected})`);
      }
      console.log(`${name}: identical asset already present; skipping`);
      continue;
    }
    try {
      // No --clobber: a concurrent upload may win, but cannot be deleted here.
      gh(['release', 'upload', tag, '-R', repo, file]);
    } catch (error) {
      // A competing job may have uploaded the same bytes after our read.
      const raced = await settledAsset(gh, tag, repo, name, sleep);
      if (raced?.digest !== expected) throw error;
      console.log(`${name}: identical concurrent upload already present`);
    }
    const uploaded = await settledAsset(gh, tag, repo, name, sleep);
    if (uploaded?.digest !== expected) {
      throw new Error(`${name}: uploaded asset SHA-256 does not match ${expected}`);
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [tag, repo, ...files] = process.argv.slice(2);
  reconcileDraftAssets({ tag, repo, files }).catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
