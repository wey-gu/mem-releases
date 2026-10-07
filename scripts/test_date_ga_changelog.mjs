import test from 'node:test';
import assert from 'node:assert/strict';
import { dateChangelog, gaDate } from './date-ga-changelog.mjs';

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
