import assert from 'node:assert/strict';
import test from 'node:test';
import { requireCpuDelivery } from './cpu-delivery-receipt.mjs';

import { cpuHarness } from './cpu-delivery-test-harness.mjs';
const version = '0.10.96';
const digest = n => `sha256:${String(n).repeat(64)}`;

test('exact successful CPU receipts accept direct GA and RC promotion without GPU/RPM', async () => {
  for (const sourceVersion of [version, `${version}-rc1`]) await requireCpuDelivery(version, cpuHarness({ sourceVersion }));
});
for (const state of ['failure', 'pending', 'cancelled', 'skipped']) {
  test(`CPU ${state} is rejected before any receipt is trusted`, async () => {
    await assert.rejects(requireCpuDelivery(version, cpuHarness({ state })), /CPU.*terminal success/);
  });
}
test('missing run id and unrelated source version cannot qualify delivery', async () => {
  await assert.rejects(requireCpuDelivery(version, { ...cpuHarness(), cpuRuns: {} }), /CPU.*run id/);
  await assert.rejects(requireCpuDelivery(version, cpuHarness({ sourceVersion: '0.10.95-rc1' })), /CPU.*source version/);
});
test('receipt identity, source SHA and target image digest are enforced', async () => {
  for (const field of ['version', 'source_sha', 'tooling_sha', 'workflow', 'run_attempt', 'role']) {
    const h = cpuHarness();
    h.receipts.get('11')[field] = 'wrong';
    await assert.rejects(requireCpuDelivery(version, h), /CPU/);
  }
  const h = cpuHarness();
  h.manifestReader = async () => ({ digest: digest(9), arch_digests: {} });
  await assert.rejects(requireCpuDelivery(version, h), /CPU.*digest/);
});


test('wrong run identity, expired/ambiguous artifacts and a changed GA source are rejected', async () => {
  for (const fault of ['run-id', 'repository', 'attempt', 'expired', 'ambiguous', 'ga-source', 'arch-digest']) {
    const h = cpuHarness({ sourceVersion: `${version}-rc1` });
    const original = h.api;
    h.api = async (...args) => {
      const result = await original(...args);
      const path = args[1];
      if (path.endsWith('/runs/11') && fault === 'run-id') result.id = 99;
      if (path.endsWith('/runs/11') && fault === 'repository') result.repository.full_name = 'other/repo';
      if (path.endsWith('/runs/11') && fault === 'attempt') result.run_attempt = 2;
      if (path.includes('/artifacts?') && fault === 'expired') result.artifacts[0].expired = true;
      if (path.includes('/artifacts?') && fault === 'ambiguous') { result.artifacts.push({ ...result.artifacts[0], id: 99 }); result.total_count += 1; }
      if (path.endsWith(`/git/ref/tags/v${version}`) && fault === 'ga-source') result.object.sha = 'c'.repeat(40);
      return result;
    };
    if (fault === 'arch-digest') h.manifestReader = async () => ({ digest: digest(1), arch_digests: { amd64: digest(9), arm64: digest(3) } });
    await assert.rejects(requireCpuDelivery(version, h), /CPU/);
  }
});
