import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  desktopPausePatterns, PAUSE_APT_PATTERN, planPromotion, planUpdater, selectRcAssets,
  verifyCandidateManifest,
} from './ga-promotion-plan.mjs';

const RC = '0.10.102-rc1';
const GA = '0.10.102';
const MEM = 'a'.repeat(40);
const COMMUNITY = 'b'.repeat(40);
const digest = (n) => `sha256:${String(n).repeat(64).slice(0, 64)}`;

const RC_ASSETS = [
  { name: `Nowledge.Mem-${GA}-1.x86_64.rpm`, digest: digest(1), size: 10 },
  { name: `Nowledge.Mem_${RC}_aarch64.dmg`, digest: digest(2), size: 10 },
  { name: `Nowledge.Mem_${RC}_amd64.AppImage`, digest: digest(3), size: 10 },
  { name: `Nowledge.Mem_${RC}_amd64.deb`, digest: digest(4), size: 10 },
  { name: `Nowledge.Mem_${RC}_x64-setup.exe`, digest: digest(5), size: 10 },
  { name: `Nowledge.Mem_${RC}_x64.dmg`, digest: digest(6), size: 10 },
];
const GA_ASSETS = RC_ASSETS.map((asset) => ({ ...asset, name: asset.name.replace(RC, GA) }));
const TARGET_DIGESTS = {
  'x86_64-unknown-linux-gnu.rpm': digest(1),
  'aarch64-apple-darwin.dmg': digest(2),
  'x86_64-unknown-linux-gnu.AppImage': digest(3),
  'x86_64-unknown-linux-gnu.deb': digest(4),
  'x86_64-pc-windows-msvc.exe': digest(5),
  'x86_64-apple-darwin.dmg': digest(6),
};
const absentR2 = () => Object.fromEntries(Object.keys(TARGET_DIGESTS).map((key) => [key, 'absent']));

function fresh(overrides = {}) {
  const base = {
    rc: RC,
    ga: GA,
    options: { promoteDesktop: true, pause: false, olderPatch: false, pushLatest: true,
      pausedStableVersion: '' },
    rcRelease: { assets: RC_ASSETS },
    gaRelease: null,
    latestTag: 'v0.10.101',
    r2: absentR2(),
    apt: '0.10.101',
    pauseRoutes: [],
    authority: { mode: 'completed', revision: 18, stable: { version: '0.10.101' }, candidate: null },
    tags: {
      mem: { rc: MEM, ga: null, cli: null },
      community: { rc: COMMUNITY, ga: null },
      communityGitlink: COMMUNITY,
    },
  };
  return { ...base, ...overrides, options: { ...base.options, ...(overrides.options || {}) } };
}

function done(overrides = {}) {
  return fresh({
    gaRelease: { isDraft: false, assets: GA_ASSETS },
    latestTag: `v${GA}`,
    r2: { ...TARGET_DIGESTS },
    apt: GA,
    authority: { mode: 'published', revision: 21, stable: { version: '0.10.101' },
      candidate: { version: GA } },
    tags: {
      mem: { rc: MEM, ga: MEM, cli: MEM },
      community: { rc: COMMUNITY, ga: COMMUNITY },
      communityGitlink: COMMUNITY,
    },
    ...overrides,
  });
}

const PAUSE_ROUTES = [...desktopPausePatterns('0.10.100'), PAUSE_APT_PATTERN];
const stoppedAuthority = { mode: 'stopped', revision: 14, stable: { version: '0.10.98' },
  candidate: { version: '0.10.100' } };

test('selects exactly six packages and excludes GPU variants', () => {
  const selected = selectRcAssets({ rc: RC, ga: GA, assets: [
    ...RC_ASSETS,
    { name: `Nowledge Mem_${RC}_x64-vulkan-setup.exe`, digest: digest(7), size: 1 },
    { name: `Nowledge Mem_${RC}_amd64-vulkan.AppImage`, digest: digest(8), size: 1 },
  ] });
  assert.equal(selected.length, 6);
  assert.deepEqual(selected.map((item) => item.gaName).sort(), GA_ASSETS.map((a) => a.name).sort());
  assert.ok(selected.every((item) => !item.gaName.includes(RC)));
});

test('an RC without its RPM cannot start a one-click promotion', () => {
  assert.throws(() => selectRcAssets({ rc: RC, ga: GA, assets: RC_ASSETS.slice(1) }),
    /exactly one x86_64-unknown-linux-gnu.rpm.*RPM lane/);
});

test('a fresh normal promotion plans every stage', () => {
  const plan = planPromotion(fresh());
  assert.deepEqual(plan.stages, {
    desktop: 'todo', rpm: 'todo', openApt: 'n/a', apt: 'todo', updater: 'todo', release: 'todo',
    unpause: 'n/a', tagMem: 'todo', tagCommunity: 'todo', tagCli: 'todo',
  });
  assert.equal(plan.uploads.length, 6);
  assert.equal(plan.memSha, MEM);
  assert.deepEqual(plan.authority, { mode: 'completed', revision: 18, stable: '0.10.101' });
});

test('a completed promotion reconciles to all done', () => {
  const plan = planPromotion(done());
  for (const [stage, state] of Object.entries(plan.stages)) {
    assert.ok(['done', 'n/a'].includes(state), `${stage}=${state}`);
  }
  assert.deepEqual(plan.uploads, []);
});

test('a partial desktop stage resumes only the missing objects', () => {
  const r2 = { ...absentR2(), 'aarch64-apple-darwin.dmg': digest(2), 'x86_64-apple-darwin.dmg': digest(6) };
  const plan = planPromotion(fresh({ r2, gaRelease: { isDraft: true, assets: GA_ASSETS.slice(1, 3) } }));
  assert.equal(plan.stages.desktop, 'todo');
  assert.deepEqual(plan.uploads.sort(), ['x86_64-pc-windows-msvc.exe', 'x86_64-unknown-linux-gnu.AppImage',
    'x86_64-unknown-linux-gnu.deb', 'x86_64-unknown-linux-gnu.rpm']);
});

test('different R2 bytes or Release assets are conflicts, never overwrites', () => {
  assert.throws(() => planPromotion(fresh({ r2: { ...absentR2(), 'x86_64-unknown-linux-gnu.deb': digest(9) } })),
    /x86_64-unknown-linux-gnu.deb: live object .* differs from RC/);
  const assets = GA_ASSETS.map((asset) => (asset.name.endsWith('.exe') ? { ...asset, digest: digest(9) } : asset));
  assert.throws(() => planPromotion(fresh({ gaRelease: { isDraft: true, assets } })),
    /x64-setup.exe: GA Release asset .* differs from RC/);
  assert.throws(() => planPromotion(fresh({ gaRelease: { isDraft: true,
    assets: [{ name: 'notes.txt', digest: digest(9) }] } })), /notes.txt: unexpected asset/);
  assert.throws(() => planPromotion(fresh({ r2: { ...absentR2(), 'x86_64-apple-darwin.dmg': 'unknown' } })),
    /differs from RC/);
});

test('a published GA missing a core package needs manual recovery', () => {
  assert.throws(() => planPromotion(done({ gaRelease: { isDraft: false, assets: GA_ASSETS.slice(0, 5) } })),
    /missing from the published GA Release/);
});

test('source tags are reconciled by exact RC-recorded SHA', () => {
  const moved = fresh({ tags: { ...fresh().tags, mem: { rc: MEM, ga: 'c'.repeat(40), cli: null } } });
  assert.throws(() => planPromotion(moved), /nowledge-co\/mem v0.10.102 points at c+, not the RC-recorded a+/);
  const cli = fresh({ tags: { ...fresh().tags, mem: { rc: MEM, ga: MEM, cli: 'c'.repeat(40) } } });
  assert.throws(() => planPromotion(cli), /cli-publish-v0.10.102 points at/);
  const drift = fresh({ tags: { ...fresh().tags, communityGitlink: 'd'.repeat(40) } });
  assert.throws(() => planPromotion(drift), /differs from the gitlink recorded by mem/);
  const missing = fresh({ tags: { ...fresh().tags, mem: { rc: null, ga: null, cli: null } } });
  assert.throws(() => planPromotion(missing), /mem RC tag is missing/);
  const partial = planPromotion(fresh({ tags: { ...fresh().tags, mem: { rc: MEM, ga: MEM, cli: null } } }));
  assert.equal(partial.stages.tagMem, 'done');
  assert.equal(partial.stages.tagCli, 'todo');
});

test('the authority state machine decides recover, prepare and publish', () => {
  const stopped = planUpdater({ authority: stoppedAuthority, ga: GA });
  assert.deepEqual([stopped.recover, stopped.prepare, stopped.publish, stopped.errors], [true, true, true, []]);
  const completed = planUpdater({ authority: fresh().authority, ga: GA });
  assert.deepEqual([completed.recover, completed.prepare, completed.publish], [false, true, true]);
  const draft = planUpdater({ authority: { mode: 'draft', revision: 19, stable: { version: '0.10.101' },
    candidate: { version: GA } }, ga: GA });
  assert.deepEqual([draft.recover, draft.prepare, draft.publish, draft.errors], [false, false, true, []]);
  const published = planUpdater({ authority: done().authority, ga: GA });
  assert.equal(published.updater, 'done');
  assert.match(planUpdater({ authority: { ...done().authority, candidate: { version: '0.10.101' } }, ga: GA })
    .errors.join(), /must be accepted \(complete\)/);
  assert.match(planUpdater({ authority: { ...stoppedAuthority, mode: 'held' }, ga: GA }).errors.join(), /held/);
  const accepted = planUpdater({ authority: { mode: 'completed', revision: 22, stable: { version: GA },
    candidate: null }, ga: GA });
  assert.deepEqual([accepted.updater, accepted.errors], ['done', []]);
  assert.match(planUpdater({ authority: { mode: 'stopped', revision: 22, stable: { version: GA },
    candidate: null }, ga: GA }).errors.join(), /already 0.10.102 in unexpected mode stopped/);
  assert.match(planUpdater({ authority: { mode: 'draft', revision: 2, stable: { version: '0.10.101' },
    candidate: { version: '0.10.103' } }, ga: GA }).errors.join(), /draft candidate 0.10.103/);
  assert.match(planUpdater({ authority: { ...fresh().authority, revision: -1 }, ga: GA }).errors.join(),
    /revision is invalid/);
});

test('pause stages follow the exact live route set', () => {
  const paused = (routes, extra = {}) => fresh({ options: { pause: true }, pauseRoutes: routes, apt: 'paused',
    authority: stoppedAuthority, ...extra });
  const full = planPromotion(paused(PAUSE_ROUTES));
  assert.equal(full.pausedVersion, '0.10.100');
  assert.deepEqual([full.stages.openApt, full.stages.apt, full.stages.unpause], ['todo', 'todo', 'todo']);
  const aptOpen = planPromotion(paused(desktopPausePatterns('0.10.100'), {
    apt: '0.10.101', r2: { ...TARGET_DIGESTS }, gaRelease: { isDraft: true, assets: GA_ASSETS } }));
  assert.deepEqual([aptOpen.stages.desktop, aptOpen.stages.openApt, aptOpen.stages.apt], ['done', 'done', 'todo']);
  assert.throws(() => planPromotion(paused(PAUSE_ROUTES, { options: { pause: true,
    pausedStableVersion: '0.10.99' } })), /differs from the live pause routes/);
  assert.throws(() => planPromotion(paused(PAUSE_ROUTES.slice(1))), /Unexpected pause route set/);
  assert.throws(() => planPromotion(fresh({ pauseRoutes: PAUSE_ROUTES })), /desktop_upgrade_pause_active=true/);
  assert.throws(() => planPromotion(paused(desktopPausePatterns('0.10.100'), { apt: 'paused' })),
    /APT is paused although its pause route is open/);
});

test('paused APT publication cannot run after the authority left stopped', () => {
  const recovered = { mode: 'recovered', revision: 15, stable: { version: '0.10.98' }, candidate: null };
  assert.throws(() => planPromotion(fresh({ options: { pause: true }, pauseRoutes: desktopPausePatterns('0.10.100'),
    apt: '0.10.101', authority: recovered, r2: { ...TARGET_DIGESTS },
    gaRelease: { isDraft: true, assets: GA_ASSETS } })), /require a stopped authority/);
});

test('later stages may not run ahead of earlier ones', () => {
  assert.throws(() => planPromotion(done({ apt: '0.10.101' })),
    /authority already published this GA, but its packages or APT are incomplete/);
  assert.throws(() => planPromotion(done({ authority: fresh().authority })),
    /GA Release is already public, but authority preparation/);
  assert.throws(() => planPromotion(fresh({ options: { pause: true, pausedStableVersion: '0.10.100' },
    pauseRoutes: [], apt: GA, r2: { ...TARGET_DIGESTS }, gaRelease: { isDraft: true, assets: GA_ASSETS },
    authority: { mode: 'draft', revision: 16, stable: { version: '0.10.98' }, candidate: { version: GA } } })),
  /Desktop pause routes are gone before the authority and GA Release were published/);
});

test('an older out-of-band patch skips APT, the authority and latest', () => {
  const plan = planPromotion(done({ options: { olderPatch: true, pushLatest: false }, latestTag: 'v0.10.110',
    apt: '0.10.110', authority: null }));
  assert.deepEqual([plan.stages.apt, plan.stages.updater, plan.stages.release], ['n/a', 'n/a', 'done']);
  assert.throws(() => planPromotion(fresh({ options: { olderPatch: true, pushLatest: true } })),
    /cannot move Docker :latest/);
  assert.throws(() => planPromotion(fresh({ options: { olderPatch: true, pause: true, pushLatest: false } })),
    /mutually exclusive/);
});

test('a newer live APT is refused', () => {
  assert.throws(() => planPromotion(fresh({ apt: '0.10.103' })), /APT already serves newer 0.10.103/);
});

test('the CLI writes stage outputs and fails with every conflict listed', () => {
  const dir = mkdtempSync(join(tmpdir(), 'ga-plan-'));
  const observed = join(dir, 'observed.json');
  const output = join(dir, 'github-output');
  writeFileSync(observed, JSON.stringify(fresh()));
  writeFileSync(output, '');
  execFileSync('node', ['scripts/ga-promotion-plan.mjs', 'plan', observed, join(dir, 'plan.json')],
    { env: { ...process.env, GITHUB_OUTPUT: output } });
  const lines = readFileSync(output, 'utf8');
  assert.match(lines, /^stage_desktop=todo$/m);
  assert.match(lines, /^stage_tag_cli=todo$/m);
  assert.match(lines, /^stage_open_apt=n\/a$/m);
  assert.match(lines, /^mem_sha=a{40}$/m);
  assert.match(lines, /^authority_revision=18$/m);
  writeFileSync(observed, JSON.stringify(fresh({ apt: '0.10.103',
    tags: { ...fresh().tags, community: { rc: COMMUNITY, ga: 'e'.repeat(40) } } })));
  const failed = spawnSync('node', ['scripts/ga-promotion-plan.mjs', 'plan', observed, join(dir, 'plan.json')],
    { encoding: 'utf8' });
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /APT already serves newer/);
  assert.match(failed.stderr, /community v0.10.102 points at e+/);

  const state = join(dir, 'state.json');
  writeFileSync(state, JSON.stringify(stoppedAuthority));
  writeFileSync(output, '');
  execFileSync('node', ['scripts/ga-promotion-plan.mjs', 'updater', state, GA],
    { env: { ...process.env, GITHUB_OUTPUT: output } });
  assert.match(readFileSync(output, 'utf8'), /^recover=true\nprepare=true\npublish=true\nmode=stopped\nrevision=14\nstable=0.10.98$/m);
});

test('the updater publishes only a candidate naming the exact RC bytes and source', () => {
  const selected = selectRcAssets({ rc: RC, ga: GA, assets: RC_ASSETS });
  const notes = `https://mem.nowledge.co/changelog#${GA}`;
  const platforms = { mac: 'aarch64-apple-darwin.dmg', mac_intel: 'x86_64-apple-darwin.dmg',
    win: 'x86_64-pc-windows-msvc.exe', 'linux-appimage': 'x86_64-unknown-linux-gnu.AppImage',
    'linux-deb': 'x86_64-unknown-linux-gnu.deb', 'linux-rpm': 'x86_64-unknown-linux-gnu.rpm' };
  const manifest = { version: GA, source_sha: MEM, release_notes: notes, pub_date: '2026-10-14T00:00:00.000Z',
    artifacts: Object.fromEntries(Object.entries(platforms).map(([platform, target]) => [platform,
      { key: `app/${GA}/${target}`, size: 10, etag: 'e', sha256: TARGET_DIGESTS[target].slice(7) }])) };
  const check = (overrides) => verifyCandidateManifest({ manifest: { ...manifest, ...overrides }, selected, ga: GA,
    sourceSha: MEM, releaseNotes: notes });
  assert.deepEqual(check({}), []);
  assert.match(check({ source_sha: 'c'.repeat(40) }).join(), /not the RC-recorded a+/);
  assert.match(check({ version: '0.10.103' }).join(), /Candidate version 0.10.103/);
  assert.match(check({ release_notes: 'https://example.invalid' }).join(), /release notes/);
  const swapped = { ...manifest.artifacts, win: { ...manifest.artifacts.win, sha256: '9'.repeat(64) } };
  assert.match(check({ artifacts: swapped }).join(), /Candidate win: SHA-256 9+ is not the RC/);
  const { 'linux-rpm': _rpm, ...five } = manifest.artifacts;
  assert.match(check({ artifacts: five }).join(), /lists 5 platforms/);
  assert.deepEqual(verifyCandidateManifest({ manifest: null, selected, ga: GA }), ['The authority holds no candidate manifest']);
});
