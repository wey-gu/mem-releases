import test from 'node:test';
import assert from 'node:assert/strict';
import { dateChangelog, gaDate } from './date-ga-changelog.mjs';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const release = { tag_name: 'v0.10.96', draft: false, prerelease: false, published_at: '2026-10-07T00:14:21+09:00' };
const website = 'const changelog = [\n  { version: "0.10.97", date: "unreleased", title: "Next" },\n  { version: "0.10.96", date: "unreleased", title: "GA" },\n  { version: "0.10.95", date: "2026-10-05", title: "Previous" },\n];\n';
const engineering = '## [0.10.97] - unreleased\nNext\n\n## [0.10.96] - unreleased\nGA\n\n## [0.10.95] - 2026-10-05\nPrevious\n';

test('uses the GA UTC day and preserves every other website byte', () => {
  assert.equal(dateChangelog(website, release), website.replace('version: "0.10.96", date: "unreleased"', 'version: "0.10.96", date: "2026-10-06"'));
});
test('dates only the matching engineering header', () => {
  assert.equal(dateChangelog(engineering, release, 'engineering'), engineering.replace('## [0.10.96] - unreleased', '## [0.10.96] - 2026-10-06'));
});
test('retries leave an already correct date unchanged', () => {
  for (const [source, kind] of [[website, 'website'], [engineering, 'engineering']]) {
    const dated = dateChangelog(source, release, kind);
    assert.equal(dateChangelog(dated, release, kind), dated);
  }
});
test('RC, Draft and missing publication time cannot write dates', () => {
  for (const patch of [{ tag_name: 'v0.10.96-rc1' }, { draft: true }, { prerelease: true }, { published_at: null }]) {
    assert.throws(() => dateChangelog(website, { ...release, ...patch }));
  }
});
test('missing or duplicate target versions are rejected', () => {
  assert.throws(() => dateChangelog(website.replaceAll('0.10.96', '0.10.94'), release));
  assert.throws(() => dateChangelog(website + website, release));
  assert.throws(() => dateChangelog(engineering + engineering, release, 'engineering'));
});
test('an existing different date is not overwritten', () => {
  assert.throws(() => dateChangelog(website.replace('version: "0.10.96", date: "unreleased"', 'version: "0.10.96", date: "2026-10-05"'), release));
});
test('historical bracketed Unreleased headers are supported', () => {
  assert.equal(dateChangelog('## [0.10.96] - [Unreleased]\nNotes\n', release, 'engineering'), '## [0.10.96] - 2026-10-06\nNotes\n');
  assert.deepEqual(gaDate(release), { version: '0.10.96', date: '2026-10-06' });
});

function localFixture(t) {
  const temp = mkdtempSync(join(tmpdir(), 'ga-date-local-'));
  t.after(() => rmSync(temp, { recursive: true, force: true }));
  const root = join(temp, 'Mem checkout');
  const paths = [join(root, 'nowledge-labs-website/nowledge-mem/data/changelog.ts'), join(root, 'nowledge-graph/CHANGELOG.md')];
  paths.forEach(path => mkdirSync(dirname(path), { recursive: true }));
  writeFileSync(paths[0], website);
  writeFileSync(paths[1], engineering);
  const releaseFile = join(temp, 'release.json');
  writeFileSync(releaseFile, JSON.stringify(release));
  writeFileSync(join(temp, 'gh'), `#!/usr/bin/env node
const assert = require('node:assert/strict');
assert.deepEqual(process.argv.slice(2), ['api', '-X', 'GET', 'repos/wey-gu/mem-releases/releases/tags/v0.10.96']);
process.stdout.write(require('node:fs').readFileSync(process.env.GA_TEST_RELEASE));
`, { mode: 0o755 });
  const run = () => spawnSync(process.execPath, [fileURLToPath(new URL('./date-ga-changelog.mjs', import.meta.url)), '0.10.96', root], {
    env: { ...process.env, PATH: `${temp}:${process.env.PATH}`, GA_TEST_RELEASE: releaseFile }, encoding: 'utf8',
  });
  return { paths, run, releaseFile };
}

test('one local command dates both files and can be repeated', t => {
  const f = localFixture(t);
  const first = f.run();
  assert.equal(first.status, 0, first.stderr);
  const dated = f.paths.map(path => readFileSync(path, 'utf8'));
  assert.deepEqual(dated, [
    website.replace('version: "0.10.96", date: "unreleased"', 'version: "0.10.96", date: "2026-10-06"'),
    engineering.replace('## [0.10.96] - unreleased', '## [0.10.96] - 2026-10-06'),
  ]);
  assert.equal(f.run().status, 0);
  assert.deepEqual(f.paths.map(path => readFileSync(path, 'utf8')), dated);
});

test('local command validates both files and GA before writing either', t => {
  const f = localFixture(t);
  writeFileSync(f.paths[1], engineering.replace('## [0.10.96] - unreleased', '## [0.10.96] - 2026-10-05'));
  const before = f.paths.map(path => readFileSync(path, 'utf8'));
  assert.notEqual(f.run().status, 0);
  assert.deepEqual(f.paths.map(path => readFileSync(path, 'utf8')), before);
  writeFileSync(f.paths[1], engineering);
  writeFileSync(f.releaseFile, JSON.stringify({ ...release, draft: true }));
  assert.notEqual(f.run().status, 0);
  assert.deepEqual(f.paths.map(path => readFileSync(path, 'utf8')), [website, engineering]);
});
