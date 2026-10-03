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

function existingAsset(release, name) {
  const matches = release.assets.filter((asset) => asset.name === name);
  if (matches.length > 1) throw new Error(`${name}: duplicate assets on Release`);
  return matches[0];
}

export async function reconcileDraftAssets({ tag, repo, files, gh = runGh,
  digestFile = sha256File, checkOnly = false }) {
  if (!tag || !repo || files.length === 0) throw new Error('tag, repo and files required');
  const names = files.map((file) => basename(file));
  if (new Set(names).size !== names.length) throw new Error('duplicate local asset names');

  for (const [index, file] of files.entries()) {
    const name = names[index];
    const expected = await digestFile(file);
    const found = existingAsset(readDraft(gh, tag, repo), name);
    if (found) {
      if (found.digest !== expected) {
        throw new Error(`${name}: existing asset SHA-256 differs (${found.digest} != ${expected})`);
      }
      console.log(`${name}: identical asset already present; skipping`);
      continue;
    }
    if (checkOnly) continue;
    try {
      // No --clobber: a concurrent upload may win, but cannot be deleted here.
      gh(['release', 'upload', tag, '-R', repo, file]);
    } catch (error) {
      // A competing job may have uploaded the same bytes after our read.
      const raced = existingAsset(readDraft(gh, tag, repo), name);
      if (raced?.digest !== expected) throw error;
      console.log(`${name}: identical concurrent upload already present`);
    }
    const uploaded = existingAsset(readDraft(gh, tag, repo), name);
    if (uploaded?.digest !== expected) {
      throw new Error(`${name}: uploaded asset SHA-256 does not match ${expected}`);
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const checkOnly = args[0] === '--check-only';
  if (checkOnly) args.shift();
  const [tag, repo, ...files] = args;
  reconcileDraftAssets({ tag, repo, files, checkOnly }).catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
