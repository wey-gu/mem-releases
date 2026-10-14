import { appendFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

export function selectRunner(mode, runners = []) {
  if (mode === 'hosted') return 'windows-2022';
  if (mode === 'self-hosted') return 'windows-book';
  if (mode !== 'auto') throw new Error('Invalid Windows runner selection');
  return runners.some(r => r.name === 'windows-book' && r.status === 'online' &&
    r.busy === false && r.labels?.some(l => l.name === 'windows-book'))
    ? 'windows-book' : 'windows-2022';
}

export async function resolveRunner(mode, token, repository, fetcher = fetch, { release = false } = {}) {
  // A release (any build that is not tag=nightly) always uses GitHub-hosted
  // Windows: the self-hosted machine is for qualification and nightly runs
  // only (nowledge-co/mem#7108). Promotion re-tags rc bits, so an rc built
  // there would ship as GA.
  if (release) return selectRunner('hosted');
  if (mode !== 'auto') return selectRunner(mode);
  if (!token) return 'windows-2022';
  try {
    const response = await fetcher(`https://api.github.com/repos/${repository}/actions/runners?per_page=100`, {
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28' },
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) return 'windows-2022';
    const inventory = await response.json();
    return selectRunner(mode, Array.isArray(inventory.runners) ? inventory.runners : []);
  } catch {
    // Availability/auth/network failure must never block the hosted build.
    return 'windows-2022';
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const mode = process.env.WINDOWS_RUNNER_MODE || 'auto';
  const release = process.env.WINDOWS_RUNNER_RELEASE === 'true';
  const label = await resolveRunner(mode, process.env.RUNNER_INVENTORY_TOKEN,
    process.env.GITHUB_REPOSITORY, fetch, { release });
  if (release && mode === 'self-hosted') {
    console.log('::warning::windows_runner=self-hosted ignored: releases always build Windows on GitHub-hosted runners.');
  }
  appendFileSync(process.env.GITHUB_OUTPUT, `label=${label}\n`);
  appendFileSync(process.env.GITHUB_STEP_SUMMARY,
    `### Windows runner selection\nSelected: \`${label}\`${release ? ' (release: hosted only)' : ''}\n`);
  console.log(`Selected Windows runner: ${label}`);
}
