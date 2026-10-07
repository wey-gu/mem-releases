#!/usr/bin/env node
// Shared by direct GA, RC promotion, and recovery. Never upload assets or move tags.
import { spawnSync } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const SITE = 'https://mem.nowledge.co';
const REPO = 'wey-gu/mem-releases';
const MEM = 'nowledge-co/mem';
const ENGINEERING = 'nowledge-graph/CHANGELOG.md';
const CORE = ['aarch64.dmg', 'x64.dmg', 'x64-setup.exe', 'amd64.deb', 'amd64.AppImage'];

export function compareVersions(a, b) {
  if (![a, b].every(value => /^v?\d+\.\d+\.\d+$/.test(value))) throw new Error('Expected clean GA versions when comparing latest');
  const left = a.replace(/^v/, '').split('.').map(Number);
  const right = b.replace(/^v/, '').split('.').map(Number);
  for (let i = 0; i < 3; i += 1) if (left[i] !== right[i]) return left[i] > right[i] ? 1 : -1;
  return 0;
}

export function gaDate(release, version) {
  if (release.tag_name !== `v${version}` || release.draft !== false || release.prerelease !== false ||
      typeof release.published_at !== 'string' || !Number.isFinite(Date.parse(release.published_at))) {
    throw new Error('The target is not a published GA with a valid publication timestamp');
  }
  return new Date(release.published_at).toISOString().slice(0, 10);
}

export function dateEngineeringChangelog(content, version, date) {
  const pattern = new RegExp(`^(## \\[${version.replaceAll('.', '\\.')}\\] - )([^\\r\\n]+)$`, 'gm');
  const matches = [...content.matchAll(pattern)];
  if (matches.length !== 1) throw new Error('Expected exactly one engineering release heading');
  return content.replace(pattern, (_, prefix) => `${prefix}${date}`);
}

export function ghApi(method, path, body, token) {
  const args = ['api', '-X', method, path];
  if (body !== undefined) args.push('--input', '-');
  const result = spawnSync('gh', args, {
    encoding: 'utf8', input: body === undefined ? undefined : JSON.stringify(body),
    env: token ? { ...process.env, GH_TOKEN: token } : process.env,
    timeout: 30000, maxBuffer: 8 * 1024 * 1024,
  });
  if (result.status !== 0) {
    const error = new Error(`GitHub ${method} ${path.split('?')[0]} failed`);
    error.notFound = result.stderr?.includes('HTTP 404') ?? false;
    throw error;
  }
  return result.stdout.trim() ? JSON.parse(result.stdout) : null;
}

async function latestRelease(api) {
  try { return await api('GET', `repos/${REPO}/releases/latest`); }
  catch (error) { if (error.notFound) return null; throw error; }
}

async function notes(version, fetcher, query = `version=${version}`, expectedDate) {
  const response = await fetcher(`${SITE}/api/changelog/release-notes?${query}&ga_readback=${Date.now()}`, {
    headers: { 'Cache-Control': 'no-cache' }, signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error('Website release notes are unavailable');
  const result = await response.json();
  if (response.headers.get('x-changelog-publication-source') !== 'github-releases') {
    throw new Error('Website publication-date resolver is not ready');
  }
  if (result.found !== true || result.version !== version || !result.title || !result.release_notes ||
      (expectedDate !== undefined && result.date !== expectedDate)) {
    throw new Error('Website version or GA UTC date does not match the published release');
  }
  return result;
}

async function requireProtectedPublication(api) {
  const environment = await api('GET', `repos/${REPO}/environments/release-publish`);
  const approval = environment.protection_rules?.find(rule => rule.type === 'required_reviewers');
  if (!approval?.reviewers?.length || approval.prevent_self_review !== true || environment.can_admins_bypass !== false) {
    throw new Error('release-publish must require reviewers, prevent self-review, and disable admin bypass before production mutation');
  }
}

export async function preflight(version, { api = ghApi, fetcher = fetch, allowNewer = false, writeToken = process.env.MEM_METADATA_TOKEN } = {}) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('Expected a clean GA version');
  await requireProtectedPublication(api);
  if (!writeToken) throw new Error('MEM_METADATA_TOKEN is required before distribution so engineering archival can be prepared');
  const latest = await latestRelease(api);
  if (latest && compareVersions(latest.tag_name, version) > 0 && !allowNewer) {
    throw new Error('A newer GA is already latest; refusing an older publication');
  }
  await notes(version, fetcher);
}

async function engineeringMetadata(version, date, api, readToken, writeToken) {
  const source = await api('GET', `repos/${MEM}/contents/${ENGINEERING}?ref=main`, undefined, readToken);
  const content = Buffer.from(source.content, 'base64').toString('utf8');
  const updated = dateEngineeringChangelog(content, version, date);
  if (updated === content) return null;
  if (!writeToken) throw new Error('Engineering date is pending. Merge its date PR or configure MEM_METADATA_TOKEN, then rerun finalize-ga-release');
  // Reuse an existing date-only correction such as #6110; do not take over product PRs.
  const pulls = [];
  for (let page = 1; ; page += 1) {
    if (page > 10) throw new Error('Engineering PR lookup exceeded its bounded pagination; inspect manually');
    const batch = await api('GET', `repos/${MEM}/pulls?state=open&base=main&per_page=100&page=${page}`, undefined, writeToken);
    pulls.push(...batch);
    if (batch.length < 100) break;
  }
  for (const pr of pulls.filter(pr => pr.title.includes(version))) {
    const files = await api('GET', `repos/${MEM}/pulls/${pr.number}/files?per_page=100`, undefined, writeToken);
    if (files.length === 1 && files[0].filename === ENGINEERING &&
        files[0].patch?.includes(`+## [${version}] - ${date}`)) return pr.html_url;
  }
  const branch = `release/finalize-${version}`;
  let head;
  try { head = await api('GET', `repos/${MEM}/git/ref/heads/${branch}`, undefined, writeToken); }
  catch (error) {
    if (!error.notFound) throw error;
    const base = await api('GET', `repos/${MEM}/git/ref/heads/main`, undefined, writeToken);
    head = await api('POST', `repos/${MEM}/git/refs`, { ref: `refs/heads/${branch}`, sha: base.object.sha }, writeToken);
  }
  // Compare-and-swap prevents overwriting a concurrent edit or rewriting history.
  const existing = await api('GET', `repos/${MEM}/contents/${ENGINEERING}?ref=${branch}`, undefined, writeToken);
  const branchContent = Buffer.from(existing.content, 'base64').toString('utf8');
  if (branchContent !== content && branchContent !== updated) throw new Error('Existing metadata branch diverged from main; inspect it instead of overwriting');
  const comparison = await api('GET', `repos/${MEM}/compare/main...${branch}`, undefined, writeToken);
  if (comparison.files?.some(file => file.filename !== ENGINEERING)) throw new Error('Existing metadata branch contains unrelated files; inspect it instead of taking ownership');
  const branchUpdated = dateEngineeringChangelog(branchContent, version, date);
  if (branchContent !== branchUpdated) await api('PUT', `repos/${MEM}/contents/${ENGINEERING}`, {
    branch, sha: existing.sha, message: `docs(release): finalize ${version} GA UTC date`,
    content: Buffer.from(branchUpdated).toString('base64'),
  }, writeToken);
  const matching = pulls.find(pr => pr.head.ref === branch);
  const pr = matching ?? await api('POST', `repos/${MEM}/pulls`, {
    head: branch, base: 'main', title: `docs(release): finalize ${version} GA UTC date`,
    body: `### Issue\n\nIssue Number: None — 正式发布日期元数据归档；流程任务 https://github.com/wey-gu/mem-releases/issues/85 。\n\n### Background\n\nhttps://github.com/wey-gu/mem-releases/releases/tag/v${version} 已公开。\n\n### What problem does this PR solve?\n\n工程日期仍未完成 GA 收尾。\n\n### How does it work?\n\n只将 ${version} 日期改为对应 published_at 的 UTC 日期 ${date}，保留全部条目与历史。网站日期由正式 Release 解析，无每版网站改动或指针更新。\n\n### Tests\n\n- [x] Not a UI change\n- [x] No need to test (精确版本的日期元数据，收尾工具检查唯一标题及实际发布时间)\n\n#### UI evidence\n\nNot a UI change.\n\nEvidence links: https://github.com/wey-gu/mem-releases/releases/tag/v${version}\n\n### Side effects / risks\n\n仅工程发布记录；不移动源码标签或替换制品。`,
  }, writeToken);
  const requested = new Set(pr.requested_reviewers?.map(user => user.login));
  const reviewers = ['wey-gu', 'hawkingrei'].filter(user => !requested.has(user));
  if (reviewers.length) await api('POST', `repos/${MEM}/pulls/${pr.number}/requested_reviewers`, { reviewers }, writeToken);
  await api('POST', `repos/${MEM}/issues/${pr.number}/labels`, { labels: ['component/docs', 'type/chore', 'documentation'] }, writeToken);
  return pr.html_url;
}

export async function finalize(version, {
  api = ghApi, fetcher = fetch, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
  attempts = 13, delay = 30000, allowNewer = false, publish = false,
  readToken = process.env.MEM_REPO_TOKEN, writeToken = process.env.MEM_METADATA_TOKEN,
  checkEngineering = true,
} = {}) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('Expected a clean GA version');
  await requireProtectedPublication(api);
  if (publish && !writeToken) throw new Error('MEM_METADATA_TOKEN is required before publishing a draft');
  const path = `repos/${REPO}/releases/tags/v${version}`;
  let release = await api('GET', path);
  const latest = await latestRelease(api);
  const newer = latest && compareVersions(latest.tag_name, version) > 0;
  if (newer && !allowNewer) throw new Error('A newer GA is already latest; refusing to move latest backward');
  if (release.draft === true) {
    if (!publish) throw new Error('The GA Release is still a draft');
    if (release.prerelease !== false || release.tag_name !== `v${version}` ||
        !CORE.every(suffix => release.assets?.some(asset => asset.name === `Nowledge.Mem_${version}_${suffix}` && asset.size > 0))) {
      throw new Error('The draft GA is missing a required core artifact');
    }
    await notes(version, fetcher);
    try { await api('PATCH', `repos/${REPO}/releases/${release.id}`, { draft: false, make_latest: 'false' }); }
    catch (error) {
      // Publication may have succeeded before the response was lost. Read once; never resend blindly.
      const observed = await api('GET', path);
      if (observed.draft !== false) throw error;
    }
    release = await api('GET', path);
  }
  const date = gaDate(release, version);
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const current = await latestRelease(api);
      if (current && compareVersions(current.tag_name, version) > 0) {
        if (!allowNewer) throw new Error('A newer GA appeared during finalization');
      }
      await notes(version, fetcher, `version=${version}`, date);
      const expected = current && compareVersions(current.tag_name, version) > 0 ? current : release;
      await notes(expected.tag_name.slice(1), fetcher, '', gaDate(expected, expected.tag_name.slice(1)));
      lastError = undefined;
      break;
    } catch (error) {
      lastError = error;
      if (attempt + 1 < attempts) await sleep(delay);
    }
  }
  if (lastError) throw new Error(`GA is public; website metadata is pending: ${lastError.message}. Rerun finalization only; do not upload artifacts again`);
  // Set latest only after the website has observed this publication; recheck before the mutation.
  const beforeLatest = await latestRelease(api);
  if (beforeLatest && compareVersions(beforeLatest.tag_name, version) > 0 && !allowNewer) throw new Error('A newer GA appeared before latest update; rerun only after reconciling it');
  if (!beforeLatest || compareVersions(beforeLatest.tag_name, version) < 0) {
    await api('PATCH', `repos/${REPO}/releases/${release.id}`, { make_latest: 'true' });
  }
  const afterLatest = await latestRelease(api);
  if (!afterLatest || (afterLatest.tag_name !== `v${version}` && !allowNewer)) throw new Error('GitHub latest readback did not match');
  const pr = checkEngineering ? await engineeringMetadata(version, date, api, readToken, writeToken) : null;
  if (pr) throw new Error(`GA is public and website is verified; engineering metadata awaits review: ${pr}. Rerun after bot merge`);
  return { state: 'complete', version, date, published_at: release.published_at, release_url: release.html_url };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [mode, version, allowNewer = 'false'] = process.argv.slice(2);
  try {
    if (!['preflight', 'publish', 'finalize'].includes(mode)) throw new Error('Usage: finalize-ga-release.mjs preflight|publish|finalize VERSION [allow-newer-latest]');
    if (mode !== 'preflight' && (process.env.GITHUB_ACTIONS !== 'true' || process.env.GITHUB_REPOSITORY !== REPO)) throw new Error('Run the approved release-publish workflow for production mutations');
    if (!['true', 'false'].includes(allowNewer)) throw new Error('allow-newer-latest must be true or false');
    const result = mode === 'preflight'
      ? await preflight(version, { allowNewer: allowNewer === 'true' })
      : await finalize(version, { allowNewer: allowNewer === 'true', publish: mode === 'publish' });
    if (result) {
      console.log(JSON.stringify(result));
      if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `\n### GA finalization complete: ${version}\n\nUTC date: ${result.date}\n`);
    }
  } catch (error) {
    console.error(`GA finalization incomplete: ${error.message}`);
    if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `\n### GA finalization incomplete: ${version}\n\n${error.message}\n`);
    process.exitCode = 1;
  }
}
