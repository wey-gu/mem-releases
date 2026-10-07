import assert from 'node:assert/strict';
import test from 'node:test';
import { finalize, preflight, dateEngineeringChangelog, gaDate } from './finalize-ga-release.mjs';

const version = '0.10.96';
const date = '2026-10-06';
const core = ['aarch64.dmg', 'x64.dmg', 'x64-setup.exe', 'amd64.deb', 'amd64.AppImage'];
function harness({ draft = true, stale = false, newer = false, sourceDate = date } = {}) {
  const changes = [];
  const release = { id: 1, tag_name: `v${version}`, draft, prerelease: false,
    published_at: draft ? null : '2026-10-06T15:14:21Z', html_url: 'https://github.com/wey-gu/mem-releases/releases/tag/v0.10.96',
    assets: core.map(suffix => ({ name: `Nowledge.Mem_${version}_${suffix}`, size: 1 })) };
  let latest = { id: 2, tag_name: newer ? 'v0.10.97' : 'v0.10.95', draft: false, prerelease: false, published_at: '2026-10-05T15:00:00Z' };
  const api = async (method, path, body) => {
    if (method !== 'GET') {
      changes.push({ method, path, body });
      if (body.draft === false) { release.draft = false; release.published_at = '2026-10-06T15:14:21Z'; }
      if (body.make_latest === 'true') latest = { ...release };
      return release;
    }
    if (path.endsWith('/environments/release-publish')) return { can_admins_bypass: false, protection_rules: [{ type: 'required_reviewers', prevent_self_review: true, reviewers: [{ type: 'User' }] }] };
    if (path.endsWith('/releases/latest')) return { ...latest };
    if (path.includes('/contents/')) return { sha: 'blob', content: Buffer.from(`## [${version}] - ${sourceDate}\n\nExisting items\n`).toString('base64') };
    return { ...release };
  };
  const fetcher = async url => {
    const explicit = new URL(url).searchParams.get('version');
    const selected = explicit ?? (newer ? '0.10.97' : stale || release.draft ? '0.10.95' : version);
    return new Response(JSON.stringify({ found: true, version: selected, date: selected === version ? stale || release.draft ? 'unreleased' : date : '2026-10-05', title: 'GA notes', release_notes: '- Changed' }),
      { headers: { 'x-changelog-publication-source': 'github-releases' } });
  };
  return { api, fetcher, changes, release, publish: true, writeToken: 'test-only', attempts: 1, delay: 0, sleep: async () => {} };
}

test('publishes once, derives UTC date, verifies both routes and engineering record', async () => {
  const h = harness();
  const result = await finalize(version, h);
  assert.equal(result.date, date);
  assert.equal(result.state, 'complete');
  assert.deepEqual(h.changes.map(x => x.body), [{ draft: false, make_latest: 'false' }, { make_latest: 'true' }]);
  await finalize(version, h);
  assert.equal(h.changes.length, 2);
});

test('a stale website cannot produce a complete finalization after publication', async () => {
  const h = harness({ stale: true });
  await assert.rejects(finalize(version, h), /website metadata is pending/);
  assert.equal(h.release.draft, false);
  assert.equal(h.changes.length, 1); // Publication can be partial; latest has not moved.
});

test('preflight requires the deployed publication resolver before artifact distribution', async () => {
  const h = harness();
  h.fetcher = async () => new Response(JSON.stringify({ found: true, version, title: 'Notes', release_notes: '- Changed' }));
  await assert.rejects(preflight(version, h), /resolver is not ready/);
  assert.equal(h.changes.length, 0);
});

test('a missing required asset prevents publication', async () => {
  const h = harness();
  h.release.assets.pop();
  await assert.rejects(finalize(version, h), /missing a required core artifact/);
  assert.equal(h.changes.length, 0);
});

test('an older task refuses to move a newer latest backward', async () => {
  const h = harness({ newer: true });
  await assert.rejects(finalize(version, h), /move latest backward/);
  assert.equal(h.changes.length, 0);
});

test('an explicit out-of-band release preserves the newer latest', async () => {
  const h = harness({ draft: false, newer: true });
  const result = await finalize(version, { ...h, allowNewer: true });
  assert.equal(result.state, 'complete');
  assert.equal(h.changes.length, 0);
});

test('publication with a lost response is read back without a second publish', async () => {
  const h = harness();
  const api = h.api;
  h.api = async (...args) => {
    const result = await api(...args);
    if (args[0] === 'PATCH' && args[2].draft === false) throw new Error('response lost');
    return result;
  };
  assert.equal((await finalize(version, h)).state, 'complete');
  assert.equal(h.changes.filter(x => x.body.draft === false).length, 1);
});

test('engineering metadata is required even when website publication is correct', async () => {
  const h = harness({ draft: false, sourceDate: 'unreleased' });
  await assert.rejects(finalize(version, { ...h, publish: false, writeToken: '' }), /Engineering date is pending/);
});

test('reuse an existing date-only engineering PR without changing it', async () => {
  const h = harness({ sourceDate: 'unreleased' });
  const api = h.api;
  h.api = async (...args) => {
    if (args[1].includes('/pulls?')) return [{ number: 6110, title: 'finalize 0.10.96 GA date', html_url: 'https://github.com/nowledge-co/mem/pull/6110' }];
    if (args[1].endsWith('/files?per_page=100')) return [{ filename: 'nowledge-graph/CHANGELOG.md', patch: '+## [0.10.96] - 2026-10-06' }];
    return api(...args);
  };
  await assert.rejects(finalize(version, { ...h, writeToken: 'test-only' }), /engineering metadata awaits review.*6110/);
  assert.equal(h.changes.filter(x => x.path.includes('repos/nowledge-co/mem')).length, 0);
});

test('exact heading replacement preserves history, wording and CRLF', () => {
  const before = '## [0.10.96] - unreleased\r\nText\r\n## [0.10.95] - 2026-10-05\r\n';
  assert.equal(dateEngineeringChangelog(before, version, date), before.replace('unreleased', date));
  assert.throws(() => dateEngineeringChangelog(before + before, version, date), /exactly one/);
});

test('draft, prerelease and invalid timestamps are not accepted as GA dates', () => {
  for (const release of [
    { tag_name: `v${version}`, draft: true, prerelease: false, published_at: '2026-10-06T15:14:21Z' },
    { tag_name: `v${version}-rc1`, draft: false, prerelease: true, published_at: '2026-10-06T15:14:21Z' },
    { tag_name: `v${version}`, draft: false, prerelease: false, published_at: 'invalid' },
  ]) assert.throws(() => gaDate(release, version), /not a published GA/);
});

test('metadata recovery cannot publish an unverified draft', async () => {
  const h = harness();
  await assert.rejects(finalize(version, { ...h, publish: false }), /still a draft/);
  assert.equal(h.changes.length, 0);
});

test('website cache convergence is retried without uploading or republishing', async () => {
  const h = harness({ draft: false });
  const fetcher = h.fetcher;
  let requests = 0;
  let waits = 0;
  h.fetcher = async url => {
    requests += 1;
    if (requests === 1) return new Response(JSON.stringify({ found: true, version, date: 'unreleased', title: 'Notes', release_notes: '- Changed' }), { headers: { 'x-changelog-publication-source': 'github-releases' } });
    return fetcher(url);
  };
  assert.equal((await finalize(version, { ...h, attempts: 2, sleep: async () => { waits += 1; } })).state, 'complete');
  assert.equal(waits, 1);
  assert.equal(h.changes.filter(x => x.body.draft === false).length, 0);
});

test('newer latest appearing after website readback prevents a downgrade', async () => {
  const h = harness({ draft: false });
  const api = h.api;
  let reads = 0;
  h.api = async (...args) => {
    if (args[1].endsWith('/releases/latest') && ++reads === 3) return { tag_name: 'v0.10.97' };
    return api(...args);
  };
  await assert.rejects(finalize(version, h), /newer GA appeared before latest update/);
  assert.equal(h.changes.length, 0);
});

test('new metadata PR uses compare-and-swap, both reviewers and a single dated heading', async () => {
  const h = harness({ draft: false, sourceDate: 'unreleased' });
  const api = h.api;
  const writes = [];
  h.api = async (method, path, body, token) => {
    if (!path.includes('repos/nowledge-co/mem')) return api(method, path, body, token);
    if (method !== 'GET') {
      writes.push({ method, path, body, token });
      if (path.endsWith('/pulls')) return { number: 6200, html_url: 'https://github.com/nowledge-co/mem/pull/6200', requested_reviewers: [] };
      return { object: { sha: 'main-head' } };
    }
    if (path.includes('/pulls?')) return [];
    if (path.includes('/git/ref/heads/release/')) throw Object.assign(new Error('absent'), { notFound: true });
    if (path.endsWith('/git/ref/heads/main')) return { object: { sha: 'main-head' } };
    if (path.includes('/compare/')) return { files: [] };
    return api(method, path, body, token);
  };
  await assert.rejects(finalize(version, { ...h, writeToken: 'test-only' }), /awaits review.*6200/);
  const update = writes.find(x => x.method === 'PUT');
  assert.equal(update.body.sha, 'blob');
  assert.equal(Buffer.from(update.body.content, 'base64').toString('utf8'), `## [${version}] - ${date}\n\nExisting items\n`);
  assert.deepEqual(writes.find(x => x.path.endsWith('/requested_reviewers')).body.reviewers, ['wey-gu', 'hawkingrei']);
  assert.ok(writes.every(x => x.token === 'test-only'));
});

test('an existing metadata branch with unrelated files is left untouched', async () => {
  const h = harness({ draft: false, sourceDate: 'unreleased' });
  const api = h.api;
  h.api = async (...args) => {
    if (args[1].includes('/pulls?')) return [];
    if (args[1].includes('/git/ref/heads/')) return { object: { sha: 'existing' } };
    if (args[1].includes('/compare/')) return { files: [{ filename: 'product.rs' }] };
    return api(...args);
  };
  await assert.rejects(finalize(version, { ...h, writeToken: 'test-only' }), /contains unrelated files/);
  assert.equal(h.changes.filter(x => x.path.includes('repos/nowledge-co/mem')).length, 0);
});

test('missing approval protection stops before any production mutation', async () => {
  const h = harness();
  const api = h.api;
  h.api = async (...args) => args[1].endsWith('/environments/release-publish')
    ? { protection_rules: [], can_admins_bypass: true } : api(...args);
  await assert.rejects(preflight(version, h), /must require reviewers/);
  await assert.rejects(finalize(version, h), /must require reviewers/);
  assert.equal(h.changes.length, 0);
});

test('missing metadata credential stops before distribution or draft publication', async () => {
  const h = harness();
  await assert.rejects(preflight(version, { ...h, writeToken: '' }), /required before distribution/);
  await assert.rejects(finalize(version, { ...h, writeToken: '' }), /required before publishing/);
  assert.equal(h.changes.length, 0);
});
