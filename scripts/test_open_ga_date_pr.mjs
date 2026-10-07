import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const driver = fileURLToPath(new URL('./open-ga-date-pr.sh', import.meta.url));
const release = { tag_name: 'v0.10.96', draft: false, prerelease: false, published_at: '2026-10-07T00:14:21+09:00' };
const branch = 'release/ga-date-0.10.96';

function fixture(t, target, source) {
  const root = mkdtempSync(join(tmpdir(), 'ga-date-driver-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const env = { ...process.env, COMPONENT: 'web', GITHUB_STEP_SUMMARY: join(root, 'summary'), GH_STATE: join(root, 'state.json'), PATH: `${root}:${process.env.PATH}` };
  writeFileSync(env.GH_STATE, JSON.stringify({ pr: null, creates: 0, requests: [] }));
  writeFileSync(join(root, 'release.json'), JSON.stringify(release));
  writeFileSync(join(root, 'gh'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const state = JSON.parse(fs.readFileSync(process.env.GH_STATE));
if (args[0] !== 'pr') throw new Error('Unexpected gh command');
switch (args[1]) {
  case 'list': console.log(JSON.stringify(state.pr ? [state.pr] : [])); break;
  case 'create':
    const labels = args.flatMap((arg, i) => arg === '--label' ? args[i + 1].split(',') : []);
    const known = ['component/web', 'type/bug', 'severity/moderate', 'impact/wrong-result'];
    if (known.some(label => !labels.includes(label)) || labels.some(label => !known.includes(label))) throw new Error('Unknown or missing PR labels');
    state.creates++; state.pr = { state: 'OPEN', url: 'https://github.com/example/repo/pull/1' }; console.log(state.pr.url); break;
  case 'view':
    console.log(args.includes('author') ? 'github-actions[bot]' : JSON.stringify({ reviewRequests: state.requests.map(login => ({login})), reviews: [] })); break;
  case 'edit': state.requests.push(args[args.indexOf('--add-reviewer') + 1]); break;
  default: throw new Error('Unexpected gh command');
}
fs.writeFileSync(process.env.GH_STATE, JSON.stringify(state));
`, { mode: 0o755 });
  const run = (command, args, cwd = root) => {
    const result = spawnSync(command, args, { cwd, env, encoding: 'utf8' });
    assert.equal(result.status, 0, `${command} ${args.join(' ')}\n${result.stdout}\n${result.stderr}`);
    return result.stdout.trim();
  };
  run('git', ['init', '--bare', '--initial-branch=main', join(root, 'origin.git')]);
  const seed = join(root, 'seed');
  run('git', ['clone', join(root, 'origin.git'), seed]);
  run('git', ['config', 'user.name', 'Test'], seed);
  run('git', ['config', 'user.email', 'test@example.invalid'], seed);
  mkdirSync(join(seed, target, '..'), { recursive: true });
  writeFileSync(join(seed, target), source);
  writeFileSync(join(seed, 'unrelated'), 'preserve this\n');
  run('git', ['add', '.'], seed);
  run('git', ['commit', '-m', 'Initial changelog'], seed);
  run('git', ['push', 'origin', 'main'], seed);
  let counter = 0;
  const attempt = () => {
    const cwd = join(root, `attempt-${counter++}`);
    run('git', ['clone', '--branch', 'main', join(root, 'origin.git'), cwd]);
    return { cwd, result: spawnSync('bash', [driver, join(root, 'release.json'), 'example/repo', target], { cwd, env, encoding: 'utf8' }) };
  };
  return { root, env, run, attempt, state: () => JSON.parse(readFileSync(env.GH_STATE)), target, source };
}

for (const [target, source] of [
  ['data/changelog.ts', '{ version: "0.10.97", date: "unreleased" },\n{ version: "0.10.96", date: "unreleased" },\n'],
  ['docs/CHANGELOG.md', '## [0.10.97] - unreleased\nNext\n## [0.10.96] - unreleased\nGA\n'],
]) {
  test(`opens one date-only PR and reuses it on retry: ${target}`, t => {
    const f = fixture(t, target, source);
    const first = f.attempt();
    assert.equal(first.result.status, 0, first.result.stderr);
    const sha = f.run('git', ['rev-parse', 'HEAD'], first.cwd);
    assert.equal(f.run('git', ['diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'], first.cwd), target);
    assert.equal(readFileSync(join(first.cwd, target), 'utf8'), source.replace(/(0\.10\.96[^\n]*?)unreleased/, '$12026-10-06'));
    assert.deepEqual(f.state().requests, ['wey-gu', 'hawkingrei']);
    const retry = f.attempt();
    assert.equal(retry.result.status, 0, retry.result.stderr);
    assert.equal(f.run('git', ['rev-parse', 'HEAD'], retry.cwd), sha);
    assert.equal(f.state().creates, 1);
    assert.deepEqual(f.state().requests, ['wey-gu', 'hawkingrei']);
    const state = f.state();
    state.pr.state = 'CLOSED';
    writeFileSync(f.env.GH_STATE, JSON.stringify(state));
    const closed = f.attempt();
    assert.notEqual(closed.result.status, 0);
    assert.match(closed.result.stderr, /Date PR was closed/);
    assert.equal(f.state().creates, 1);
  });
}

test('an existing branch with unrelated edits is rejected', t => {
  const f = fixture(t, 'data/changelog.ts', '{ version: "0.10.96", date: "unreleased" },\n');
  const first = f.attempt();
  assert.equal(first.result.status, 0, first.result.stderr);
  writeFileSync(join(first.cwd, 'unrelated'), 'unexpected edit\n');
  f.run('git', ['add', 'unrelated'], first.cwd);
  f.run('git', ['commit', '-m', 'Unexpected change'], first.cwd);
  f.run('git', ['push', 'origin', `HEAD:refs/heads/${branch}`], first.cwd);
  const retry = f.attempt();
  assert.notEqual(retry.result.status, 0);
  assert.match(retry.result.stderr, /not a one-file date change/);
  assert.equal(f.state().creates, 1);
});
