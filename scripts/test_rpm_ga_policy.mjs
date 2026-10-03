import test from 'node:test';
import assert from 'node:assert/strict';
import { gaRpmFilename } from './rpm-ga-policy.mjs';

test('accepts the actual RC2 RPM package filename without a suffix rewrite', () => {
  assert.equal(gaRpmFilename('0.10.94-rc2', '0.10.94',
    'Nowledge.Mem-0.10.94-1.x86_64.rpm'),
  'Nowledge.Mem-0.10.94-1.x86_64.rpm');
});

test('rewrites an RPM filename that does contain the RC suffix', () => {
  assert.equal(gaRpmFilename('0.10.94-rc2', '0.10.94',
    'Nowledge.Mem_0.10.94-rc2_x86_64.rpm'),
  'Nowledge.Mem_0.10.94_x86_64.rpm');
});

test('rejects a different version or non-RPM source', () => {
  assert.throws(() => gaRpmFilename('0.10.94-rc2', '0.10.95',
    'Nowledge.Mem-0.10.94-1.x86_64.rpm'), /do not match/);
  assert.throws(() => gaRpmFilename('0.10.94-rc2', '0.10.94',
    'Nowledge.Mem-0.10.93-1.x86_64.rpm'), /does not contain/);
  assert.throws(() => gaRpmFilename('0.10.94-rc2', '0.10.94',
    'Nowledge.Mem-0.10.941-1.x86_64.rpm'), /does not contain/);
  assert.throws(() => gaRpmFilename('0.10.94-rc2', '0.10.94',
    'Nowledge.Mem-0.10.94-1.x86_64.deb'), /Expected a single RPM/);
});
