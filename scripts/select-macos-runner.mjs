import { appendFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

// The self-hosted Mac mini (M4, 10 cores, 16 GB) builds both macOS legs:
// arm64 natively and x86_64 cross-compiled from Apple Silicon. Measured on
// 0.10.104 (nowledge-co/mem#7108 P5): arm64 18 min vs 56 min on macos-15,
// x86_64 30 min vs about 92 min on macos-15-intel. The two legs share the one
// machine, so they run one after the other.
export const SELF_HOSTED = ['self-hosted', 'macOS', 'mac-mini-m4'];
export const HOSTED = { arm64: ['macos-15'], x86_64: ['macos-15-intel'] };

function labels(selfHosted) {
  return selfHosted
    ? { arm64: SELF_HOSTED, x86_64: SELF_HOSTED, selfHosted: true }
    : { ...HOSTED, selfHosted: false };
}

export function selectRunner(mode, runners = []) {
  if (mode === 'hosted') return labels(false);
  if (mode === 'self-hosted') return labels(true);
  if (mode !== 'auto') throw new Error('Invalid macOS runner selection');
  // Online is enough: a busy mini only means the other leg or a bench is
  // running, and the job waits for it. An offline mini must never strand a
  // release, so it falls back to GitHub-hosted.
  return labels(runners.some(r => r.name === 'mac-mini-m4' && r.status === 'online' &&
    r.labels?.some(l => l.name === 'mac-mini-m4')));
}

export async function resolveRunner(mode, token, repository, fetcher = fetch) {
  if (mode !== 'auto') return selectRunner(mode);
  if (!token) return labels(false);
  try {
    const response = await fetcher(`https://api.github.com/repos/${repository}/actions/runners?per_page=100`, {
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28' },
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) return labels(false);
    const inventory = await response.json();
    return selectRunner(mode, Array.isArray(inventory.runners) ? inventory.runners : []);
  } catch {
    // Availability/auth/network failure must never block the hosted build.
    return labels(false);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const mode = process.env.MACOS_RUNNER_MODE || 'auto';
  const picked = await resolveRunner(mode, process.env.RUNNER_INVENTORY_TOKEN,
    process.env.GITHUB_REPOSITORY, fetch);
  appendFileSync(process.env.GITHUB_OUTPUT,
    `arm64=${JSON.stringify(picked.arm64)}\nx86_64=${JSON.stringify(picked.x86_64)}\nself_hosted=${picked.selfHosted}\n`);
  appendFileSync(process.env.GITHUB_STEP_SUMMARY,
    `### macOS runner selection\nMode \`${mode}\`: arm64 \`${picked.arm64.join(',')}\`, x86_64 \`${picked.x86_64.join(',')}\`\n`);
  console.log(`Selected macOS runners (${mode}): arm64=${picked.arm64} x86_64=${picked.x86_64}`);
}
