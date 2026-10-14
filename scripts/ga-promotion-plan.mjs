// Read-only reconciliation plan for the one-click GA promotion.
//
// The workflow observes live state first (RC/GA Releases, R2 objects, APT,
// pause routes, the Desktop update authority, and source tags) and this module
// turns that observation into one decision per stage:
//
//   todo  - the stage has not happened; run it
//   done  - the exact expected bytes/SHA/state are already live; verify and skip
//   n/a   - the stage does not apply to this promotion mode
//
// Anything that is neither absent nor exactly equal is a conflict. Conflicts
// stop the run before any public mutation: a resumed promotion may fill gaps,
// but it never overwrites bytes, tags, or authority state it did not expect.
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { gaRpmFilename } from './rpm-ga-policy.mjs';

const SEMVER = /^\d+\.\d+\.\d+$/;
const SHA1 = /^[0-9a-f]{40}$/;
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const VARIANT = /vulkan|cuda/i;

export const CORE_TARGETS = [
  ['aarch64-apple-darwin.dmg', /aarch64.*\.dmg$/i],
  ['x86_64-apple-darwin.dmg', /(x86_64|x64).*\.dmg$/i],
  ['x86_64-pc-windows-msvc.exe', /\.exe$/i],
  ['x86_64-unknown-linux-gnu.deb', /\.deb$/i],
  ['x86_64-unknown-linux-gnu.AppImage', /\.AppImage$/i],
];
export const RPM_TARGET = ['x86_64-unknown-linux-gnu.rpm', /\.rpm$/i];

export const PAUSE_APT_PATTERN = 'download-mem.nowledge.co/apt/*';
export function desktopPausePatterns(version) {
  return [
    'backbone-mem.nowledge.co/download/latest/*',
    'backbone-mem.nowledge.co/update.json*',
    `download-mem.nowledge.co/app/${version}/*`,
    `backbone-mem.nowledge.co/download/${version}/*`,
    'backbone-mem.nowledge.co/latest*',
  ];
}

function compareVersions(a, b) {
  const left = a.split('.').map(Number);
  const right = b.split('.').map(Number);
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return Math.sign(left[index] - right[index]);
  }
  return 0;
}

export function validateVersions(rc, ga) {
  if (!SEMVER.test(ga || '')) throw new Error(`GA tag '${ga}' must be clean semver`);
  if (rc === ga) throw new Error(`RC and GA tags are identical: '${rc}'`);
  if ((rc || '').split('-')[0] !== ga) {
    throw new Error(`RC base '${(rc || '').split('-')[0]}' does not match GA '${ga}'`);
  }
}

// Pick the six Desktop packages from the RC Release by platform class. GPU
// variants are manual downloads and never part of the six-asset GA contract.
export function selectRcAssets({ rc, ga, assets }) {
  validateVersions(rc, ga);
  const candidates = (assets || []).filter((asset) => !VARIANT.test(asset.name));
  const selected = [];
  for (const [target, pattern] of [...CORE_TARGETS, RPM_TARGET]) {
    const matches = candidates.filter((asset) => pattern.test(asset.name)
      && !(target === 'x86_64-apple-darwin.dmg' && /aarch64/i.test(asset.name)));
    if (matches.length !== 1) {
      throw new Error(`RC v${rc}: expected exactly one ${target} package, found ${matches.length}`
        + (target.endsWith('.rpm') ? ' (re-run the RC Linux RPM lane before promotion)' : ''));
    }
    const [asset] = matches;
    if (!SHA256.test(asset.digest || '')) throw new Error(`${asset.name}: RC asset has no SHA-256 digest`);
    const gaName = target.endsWith('.rpm')
      ? gaRpmFilename(rc, ga, asset.name)
      : asset.name.replaceAll(rc, ga);
    if (gaName.includes(rc)) throw new Error(`${asset.name}: GA name still contains ${rc}`);
    selected.push({ target, rcName: asset.name, gaName, digest: asset.digest, size: asset.size,
      core: !target.endsWith('.rpm') });
  }
  return selected;
}

export function planDesktop({ selected, r2, gaRelease }) {
  const errors = [];
  const uploads = [];
  const attach = [];
  const expectedNames = new Set(selected.map((item) => item.gaName));
  const published = gaRelease && gaRelease.isDraft === false;
  for (const item of selected) {
    const state = r2?.[item.target];
    if (state === 'absent') {
      uploads.push(item.target);
    } else if (state !== item.digest) {
      errors.push(`R2 app/<ga>/${item.target}: live object ${state || '<unknown>'} differs from RC ${item.digest}`);
    }
    const found = (gaRelease?.assets || []).filter((asset) => asset.name === item.gaName);
    if (found.length > 1) errors.push(`${item.gaName}: duplicate GA Release assets`);
    if (found.length === 0) {
      attach.push(item.gaName);
      if (published && item.core) errors.push(`${item.gaName}: missing from the published GA Release`);
    } else if (found[0].digest !== item.digest) {
      errors.push(`${item.gaName}: GA Release asset ${found[0].digest} differs from RC ${item.digest}`);
    }
  }
  for (const asset of gaRelease?.assets || []) {
    if (!expectedNames.has(asset.name)) errors.push(`${asset.name}: unexpected asset on the GA Release`);
  }
  const pending = (core) => selected.some((item) => item.core === core
    && (uploads.includes(item.target) || attach.includes(item.gaName)));
  return {
    errors,
    uploads,
    attach,
    core: pending(true) ? 'todo' : 'done',
    rpm: pending(false) ? 'todo' : 'done',
  };
}

export function planPause({ pause, ga, routes, pausedInput }) {
  const errors = [];
  if (!Array.isArray(routes)) return { errors: ['Pause Worker routes could not be read'] };
  if (!pause) {
    if (routes.length > 0) {
      errors.push(`The upgrade pause Worker owns ${routes.length} routes; dispatch with desktop_upgrade_pause_active=true`);
    }
    return { errors, openApt: 'n/a', unpause: 'n/a', pausedVersion: '' };
  }
  const derived = [...new Set(routes
    .map((pattern) => pattern.match(/^download-mem\.nowledge\.co\/app\/(\d+\.\d+\.\d+)\/\*$/)?.[1])
    .filter(Boolean))];
  if (derived.length > 1) errors.push(`Pause routes block several versions: ${derived.join(', ')}`);
  if (pausedInput && derived[0] && pausedInput !== derived[0]) {
    errors.push(`paused_stable_version ${pausedInput} differs from the live pause routes (${derived[0]})`);
  }
  const pausedVersion = pausedInput || derived[0] || '';
  if (routes.length > 0 && !SEMVER.test(pausedVersion)) {
    errors.push('Cannot determine the paused stable version from the live pause routes');
  }
  if (pausedVersion && (!SEMVER.test(pausedVersion) || pausedVersion === ga)) {
    errors.push('paused_stable_version must be a clean semver different from GA');
  }
  const desktop = new Set(desktopPausePatterns(pausedVersion));
  const actual = [...routes].sort();
  const sameSet = (expected) => expected.length === actual.length
    && [...expected].sort().every((pattern, index) => pattern === actual[index]);
  let openApt;
  let unpause;
  if (sameSet([...desktop, PAUSE_APT_PATTERN])) {
    openApt = 'todo';
    unpause = 'todo';
  } else if (sameSet([...desktop])) {
    openApt = 'done';
    unpause = 'todo';
  } else if (routes.length === 0) {
    openApt = 'done';
    unpause = 'done';
  } else {
    errors.push(`Unexpected pause route set: ${actual.join(', ')}`);
  }
  return { errors, openApt, unpause, pausedVersion };
}

export function planApt({ olderPatch, live, ga, aptRouteOpen }) {
  if (olderPatch) return { errors: [], apt: 'n/a' };
  if (live === 'paused') {
    return aptRouteOpen
      ? { errors: ['APT is paused although its pause route is open'] }
      : { errors: [], apt: 'todo' };
  }
  if (!SEMVER.test(live || '')) return { errors: [`APT live version is unknown (${live || '<missing>'})`] };
  const order = compareVersions(live, ga);
  if (order > 0) return { errors: [`APT already serves newer ${live}; use the older-patch mode`] };
  return { errors: [], apt: order === 0 ? 'done' : 'todo' };
}

// Desktop update authority transitions (mem-backbone desktop-update-control):
// stopped -> recover -> recovered -> prepare -> draft -> publish -> published.
// `complete` (published -> completed) accepts a candidate and is deliberately
// not exposed to release automation.
export function planUpdater({ authority, ga }) {
  const errors = [];
  if (!authority || typeof authority !== 'object') {
    return { errors: ['Desktop update authority status is missing'] };
  }
  const { mode, revision } = authority;
  const stable = authority.stable?.version || '';
  const candidate = authority.candidate?.version || '';
  if (!Number.isSafeInteger(revision) || revision < 0) errors.push('Authority revision is invalid');
  if (!SEMVER.test(stable)) errors.push('Authority stable version is invalid');
  const plan = { recover: false, prepare: false, publish: false };
  if (stable === ga) {
    // The candidate was already accepted (published -> complete).
    if (!['completed', 'stable'].includes(mode) || candidate) {
      errors.push(`Authority stable is already ${ga} in unexpected mode ${mode}`);
    }
    return { errors, ...plan, updater: 'done', mode, revision, stable, candidate };
  }
  switch (mode) {
    case 'stopped':
      Object.assign(plan, { recover: true, prepare: true, publish: true });
      break;
    case 'stable':
    case 'recovered':
    case 'completed':
      if (candidate) errors.push(`Authority mode ${mode} unexpectedly retains candidate ${candidate}`);
      Object.assign(plan, { prepare: true, publish: true });
      break;
    case 'draft':
      if (candidate !== ga) errors.push(`Authority holds draft candidate ${candidate || '<none>'}, not ${ga}`);
      plan.publish = true;
      break;
    case 'published':
      if (candidate !== ga) {
        errors.push(`Authority still serves published candidate ${candidate || '<none>'}; `
          + 'it must be accepted (complete) before another candidate can be prepared');
      }
      break;
    case 'held':
      errors.push('Authority is held; an operator must decide between publish and recover');
      break;
    default:
      errors.push(`Unsupported authority mode ${mode || '<missing>'}`);
  }
  let updater = plan.recover || plan.prepare || plan.publish ? 'todo' : 'done';
  if (errors.length) updater = 'blocked';
  return { errors, ...plan, updater, mode, revision, stable, candidate };
}

export function planRelease({ gaRelease, latestTag, ga, olderPatch }) {
  if (!gaRelease) return { release: 'todo' };
  if (gaRelease.isDraft) return { release: 'todo' };
  if (olderPatch || latestTag === `v${ga}`) return { release: 'done' };
  return { release: 'todo' };
}

function planTag(name, live, expected) {
  if (!live) return { state: 'todo' };
  if (live === expected) return { state: 'done' };
  return { error: `${name} points at ${live}, not the RC-recorded ${expected}` };
}

export function planTags({ ga, tags }) {
  const errors = [];
  const memRc = tags?.mem?.rc;
  const communityRc = tags?.community?.rc;
  if (!SHA1.test(memRc || '')) errors.push('nowledge-co/mem RC tag is missing or unreadable');
  if (!SHA1.test(communityRc || '')) errors.push('nowledge-co/community RC tag is missing or unreadable');
  if (tags?.communityGitlink && tags.communityGitlink !== communityRc) {
    errors.push(`community RC tag ${communityRc} differs from the gitlink recorded by mem (${tags.communityGitlink})`);
  }
  if (errors.length) return { errors };
  const mem = planTag(`nowledge-co/mem v${ga}`, tags.mem.ga, memRc);
  const community = planTag(`nowledge-co/community v${ga}`, tags.community.ga, communityRc);
  const cli = planTag(`nowledge-co/mem cli-publish-v${ga}`, tags.mem.cli, memRc);
  for (const item of [mem, community, cli]) if (item.error) errors.push(item.error);
  return {
    errors,
    memSha: memRc,
    communitySha: communityRc,
    tagMem: mem.state,
    tagCommunity: community.state,
    tagCli: cli.state,
  };
}

export function planPromotion(observed) {
  const { rc, ga } = observed;
  const options = observed.options || {};
  const errors = [];
  const add = (result) => {
    errors.push(...(result.errors || []));
    return result;
  };
  validateVersions(rc, ga);
  if (options.olderPatch && options.pause) {
    errors.push('The older-patch and emergency-pause promotion modes are mutually exclusive');
  }
  if (options.olderPatch && options.pushLatest) {
    errors.push('Older desktop GA cannot move Docker :latest; set push_latest=false');
  }
  const plan = { rc, ga, options, stages: {} };
  if (!options.promoteDesktop) {
    Object.assign(plan.stages, {
      desktop: 'n/a', rpm: 'n/a', openApt: 'n/a', apt: 'n/a', updater: 'n/a',
      release: 'n/a', unpause: 'n/a', tags: 'n/a', mobile: 'n/a',
    });
    if (errors.length) throw new AggregateError(errors.map((e) => new Error(e)), errors.join('\n'));
    return plan;
  }

  const selected = selectRcAssets({ rc, ga, assets: observed.rcRelease?.assets });
  const desktop = add(planDesktop({ selected, r2: observed.r2, gaRelease: observed.gaRelease }));
  const pause = add(planPause({ pause: options.pause, ga, routes: observed.pauseRoutes,
    pausedInput: options.pausedStableVersion }));
  const apt = add(planApt({ olderPatch: options.olderPatch, live: observed.apt, ga,
    aptRouteOpen: pause.openApt === 'done' || !options.pause }));
  const updater = options.olderPatch
    ? { updater: 'n/a', recover: false, prepare: false, publish: false }
    : add(planUpdater({ authority: observed.authority, ga }));
  const release = planRelease({ gaRelease: observed.gaRelease, latestTag: observed.latestTag,
    ga, olderPatch: options.olderPatch });
  const tags = add(planTags({ ga, tags: observed.tags }));

  // Cross-stage ordering: each later stage presumes every earlier one.
  const published = observed.gaRelease?.isDraft === false;
  if (options.pause && (desktop.core === 'todo' || pause.openApt === 'todo' || apt.apt === 'todo')
      && observed.authority?.mode !== 'stopped') {
    errors.push('Paused staging and APT publication require a stopped authority; '
      + `it is ${observed.authority?.mode || '<unknown>'}`);
  }
  if (updater.updater === 'done' && (desktop.core === 'todo' || desktop.rpm === 'todo' || apt.apt === 'todo')) {
    errors.push('The authority already published this GA, but its packages or APT are incomplete');
  }
  if (published && (updater.prepare || (options.pause && apt.apt === 'todo'))) {
    errors.push('The GA Release is already public, but authority preparation or paused APT publication '
      + 'still needs the draft; recover by hand');
  }
  if (pause.unpause === 'done' && (updater.updater === 'todo' || release.release === 'todo')) {
    errors.push('Desktop pause routes are gone before the authority and GA Release were published');
  }
  if (errors.length) throw new AggregateError(errors.map((e) => new Error(e)), errors.join('\n'));

  Object.assign(plan, {
    selected,
    uploads: desktop.uploads,
    pausedVersion: pause.pausedVersion,
    authority: options.olderPatch ? null : {
      mode: updater.mode, revision: updater.revision, stable: updater.stable,
    },
    memSha: tags.memSha,
    communitySha: tags.communitySha,
  });
  Object.assign(plan.stages, {
    desktop: desktop.core,
    rpm: desktop.rpm,
    openApt: pause.openApt,
    apt: apt.apt,
    updater: updater.updater,
    release: release.release,
    unpause: pause.unpause,
    tagMem: tags.tagMem,
    tagCommunity: tags.tagCommunity,
    tagCli: tags.tagCli,
  });
  return plan;
}

const MANIFEST_TARGETS = {
  mac: 'aarch64-apple-darwin.dmg',
  mac_intel: 'x86_64-apple-darwin.dmg',
  win: 'x86_64-pc-windows-msvc.exe',
  'linux-appimage': 'x86_64-unknown-linux-gnu.AppImage',
  'linux-deb': 'x86_64-unknown-linux-gnu.deb',
  'linux-rpm': 'x86_64-unknown-linux-gnu.rpm',
};

// Verify-before-expose: the candidate the authority is about to publish must
// name exactly the RC-authenticated bytes, the RC-recorded source commit, and
// the public Changelog. Any difference stops the publish.
export function verifyCandidateManifest({ manifest, selected, ga, sourceSha, releaseNotes }) {
  const errors = [];
  if (!manifest || typeof manifest !== 'object') return ['The authority holds no candidate manifest'];
  if (manifest.version !== ga) errors.push(`Candidate version ${manifest.version} is not ${ga}`);
  if (manifest.source_sha !== sourceSha) {
    errors.push(`Candidate source ${manifest.source_sha} is not the RC-recorded ${sourceSha}`);
  }
  if (manifest.release_notes !== releaseNotes) {
    errors.push(`Candidate release notes ${manifest.release_notes} are not ${releaseNotes}`);
  }
  const artifacts = manifest.artifacts || {};
  if (Object.keys(artifacts).length !== Object.keys(MANIFEST_TARGETS).length) {
    errors.push(`Candidate lists ${Object.keys(artifacts).length} platforms, expected six`);
  }
  for (const [platform, target] of Object.entries(MANIFEST_TARGETS)) {
    const artifact = artifacts[platform];
    const rc = selected.find((item) => item.target === target);
    if (!artifact || !rc) {
      errors.push(`Candidate ${platform}: missing artifact`);
      continue;
    }
    if (artifact.key !== `app/${ga}/${target}`) errors.push(`Candidate ${platform}: key ${artifact.key}`);
    if (`sha256:${artifact.sha256}` !== rc.digest) {
      errors.push(`Candidate ${platform}: SHA-256 ${artifact.sha256} is not the RC ${rc.digest}`);
    }
    if (artifact.size !== rc.size) errors.push(`Candidate ${platform}: size ${artifact.size} is not ${rc.size}`);
  }
  return errors;
}

function writeOutputs(values) {
  const lines = Object.entries(values).map(([key, value]) => `${key}=${value ?? ''}`).join('\n');
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `${lines}\n`);
  console.log(lines);
}

function fail(error) {
  const messages = error instanceof AggregateError ? error.errors.map((e) => e.message) : [error.message];
  for (const message of messages) console.error(`::error::${message}`);
  process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [command, ...args] = process.argv.slice(2);
  try {
    if (command === 'select') {
      const [releaseJson, rc, ga] = args;
      const release = JSON.parse(readFileSync(releaseJson, 'utf8'));
      console.log(JSON.stringify(selectRcAssets({ rc, ga, assets: release.assets }), null, 2));
    } else if (command === 'plan') {
      const [observedJson, planJson] = args;
      const plan = planPromotion(JSON.parse(readFileSync(observedJson, 'utf8')));
      writeFileSync(planJson, `${JSON.stringify(plan, null, 2)}\n`);
      writeOutputs({
        ...Object.fromEntries(Object.entries(plan.stages)
          .map(([k, v]) => [`stage_${k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`)}`, v])),
        paused_version: plan.pausedVersion,
        authority_stable: plan.authority?.stable,
        authority_revision: plan.authority?.revision,
        mem_sha: plan.memSha,
        community_sha: plan.communitySha,
      });
    } else if (command === 'updater') {
      const [stateJson, ga] = args;
      const result = planUpdater({ authority: JSON.parse(readFileSync(stateJson, 'utf8')), ga });
      if (result.errors.length) throw new AggregateError(result.errors.map((e) => new Error(e)), 'updater');
      writeOutputs({
        recover: result.recover,
        prepare: result.prepare,
        publish: result.publish,
        mode: result.mode,
        revision: result.revision,
        stable: result.stable,
      });
    } else if (command === 'candidate') {
      const [stateJson, planJson, sourceSha, releaseNotes] = args;
      const state = JSON.parse(readFileSync(stateJson, 'utf8'));
      const plan = JSON.parse(readFileSync(planJson, 'utf8'));
      const errors = verifyCandidateManifest({ manifest: state.candidate, selected: plan.selected,
        ga: plan.ga, sourceSha, releaseNotes });
      if (errors.length) throw new AggregateError(errors.map((e) => new Error(e)), 'candidate');
      console.log(`Candidate ${plan.ga} names the six RC-authenticated packages and source ${sourceSha}`);
    } else {
      throw new Error('Usage: ga-promotion-plan.mjs select|plan|updater|candidate ...');
    }
  } catch (error) {
    fail(error);
  }
}
