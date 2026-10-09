// RPM is a deliberately deferred manual-download asset. It may be added to a
// published GA, but a same-name asset is immutable after its first upload.
import { execFileSync } from 'node:child_process';
import { basename } from 'node:path';
import { pathToFileURL } from 'node:url';
import { sha256File } from './upload-draft-release-assets.mjs';

const runGh = (args) => execFileSync('gh', args, { encoding: 'utf8' });

function readAsset(gh, tag, repo, name) {
  const release = JSON.parse(gh(['release', 'view', tag, '-R', repo,
    '--json', 'assets,isDraft']));
  const rpms = release.assets.filter((asset) => asset.name.toLowerCase().endsWith('.rpm'));
  if (rpms.length > 1 || rpms.some((asset) => asset.name !== name)) {
    throw new Error(`${tag}: conflicting or duplicate GA RPM assets`);
  }
  return rpms[0];
}

export async function reconcileRpmReleaseAsset({ tag, repo, file,
  checkOnly = false, gh = runGh, digestFile = sha256File }) {
  if (!/^v\d+\.\d+\.\d+$/.test(tag || '') || !repo ||
      !file || !basename(file).toLowerCase().endsWith('.rpm')) {
    throw new Error('GA tag, repo and RPM file required');
  }
  const name = basename(file);
  const expected = await digestFile(file);
  const existing = readAsset(gh, tag, repo, name);
  if (existing) {
    if (existing.digest !== expected) {
      throw new Error(`${name}: existing GA asset SHA-256 differs (${existing.digest} != ${expected})`);
    }
    console.log(`${name}: identical GA RPM asset already present`);
    return;
  }
  if (checkOnly) return;
  try {
    gh(['release', 'upload', tag, '-R', repo, file]); // Never --clobber.
  } catch (error) {
    if (readAsset(gh, tag, repo, name)?.digest !== expected) throw error;
  }
  if (readAsset(gh, tag, repo, name)?.digest !== expected) {
    throw new Error(`${name}: GA RPM asset readback SHA-256 mismatch`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const checkOnly = args[0] === '--check-only';
  if (checkOnly) args.shift();
  const [tag, repo, file] = args;
  reconcileRpmReleaseAsset({ tag, repo, file, checkOnly }).catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
