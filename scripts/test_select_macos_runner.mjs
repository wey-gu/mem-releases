import test from 'node:test';
import assert from 'node:assert/strict';
import { selectRunner, resolveRunner, SELF_HOSTED, HOSTED } from './select-macos-runner.mjs';
const mini = { name: 'mac-mini-m4', status: 'online', busy: false, labels: [{ name: 'mac-mini-m4' }] };
const hosted = { ...HOSTED, selfHosted: false };
const selfHosted = { arm64: SELF_HOSTED, x86_64: SELF_HOSTED, selfHosted: true };

test('auto picks the online mini for both legs, busy or not', () => {
  assert.deepEqual(selectRunner('auto', [mini]), selfHosted);
  assert.deepEqual(selectRunner('auto', [{ ...mini, busy: true }]), selfHosted);
});
for (const [name, runners] of [['absent', []], ['offline', [{ ...mini, status: 'offline' }]],
  ['wrong name', [{ ...mini, name: 'macbook-intel' }]], ['wrong label', [{ ...mini, labels: [] }]]]) {
  test(`auto falls back to GitHub-hosted when the mini is ${name}`, () =>
    assert.deepEqual(selectRunner('auto', runners), hosted));
}
test('hosted keeps the original images, one per architecture', () => {
  assert.deepEqual(selectRunner('hosted', [mini]), hosted);
  assert.deepEqual(hosted.arm64, ['macos-15']);
  assert.deepEqual(hosted.x86_64, ['macos-15-intel']);
});
test('self-hosted forces the mini; anything else is rejected', () => {
  assert.deepEqual(selectRunner('self-hosted', []), selfHosted);
  assert.throws(() => selectRunner('invalid'), /Invalid/);
});
test('missing credentials, API errors, malformed data and network errors fall back', async () => {
  assert.deepEqual(await resolveRunner('auto', '', 'a/b', () => assert.fail()), hosted);
  for (const fetcher of [async () => ({ ok: false }), async () => { throw Error('network'); },
    async () => ({ ok: true, json: async () => ({}) })])
    assert.deepEqual(await resolveRunner('auto', 'secret', 'a/b', fetcher), hosted);
});
test('a successful inventory with the mini online resolves self-hosted', async () => {
  assert.deepEqual(await resolveRunner('auto', 'secret', 'a/b',
    async () => ({ ok: true, json: async () => ({ runners: [mini] }) })), selfHosted);
});
test('explicit modes never query the inventory', async () => {
  for (const mode of ['hosted', 'self-hosted'])
    await resolveRunner(mode, 'secret', 'a/b', () => assert.fail('queried'));
});
