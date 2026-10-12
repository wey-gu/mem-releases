import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { verifyControlledDesktopDelivery } from './verify-controlled-desktop-delivery.mjs';

const origin = 'https://backbone-mem.nowledge.co';
const actions = ['status', 'history', 'hold', 'stop', 'recover', 'prepare', 'publish'];
const semver = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
function requireCondition(ok, message) { if (!ok) throw new Error(message); }
const files = {
  mac: 'aarch64-apple-darwin.dmg', mac_intel: 'x86_64-apple-darwin.dmg',
  win: 'x86_64-pc-windows-msvc.exe', 'linux-appimage': 'x86_64-unknown-linux-gnu.AppImage',
  'linux-deb': 'x86_64-unknown-linux-gnu.deb', 'linux-rpm': 'x86_64-unknown-linux-gnu.rpm',
};
function validateManifest(m) {
  requireCondition(m && semver.test(m.version ?? '') && /^[a-f0-9]{40}$/.test(m.source_sha ?? ''), 'Qualified manifest needs version and source_sha');
  requireCondition(typeof m.release_notes === 'string' && m.release_notes.length <= 8000 &&
    typeof m.pub_date === 'string' && Number.isFinite(Date.parse(m.pub_date)), 'Qualified manifest needs release_notes and pub_date');
  requireCondition(Object.keys(m.artifacts ?? {}).sort().join() === Object.keys(files).sort().join(), 'Qualified manifest needs exactly six platforms');
  for (const [p, f] of Object.entries(files)) {
    const a = m.artifacts[p];
    requireCondition(a?.key === `app/${m.version}/${f}` && Number.isSafeInteger(a.size) && a.size > 0 &&
      typeof a.etag === 'string' && a.etag.length > 0 && a.etag.length <= 128 &&
      /^[a-f0-9]{64}$/.test(a.sha256 ?? ''), `${p}: qualified artifact key/size/ETag/SHA-256 invalid`);
  }
}
export function buildPlan(input) {
  requireCondition(actions.includes(input.action), 'Unsupported action; initialize/complete are intentionally unavailable');
  if (['status', 'history'].includes(input.action)) return { action: input.action };
  requireCondition(typeof input.expected_revision === 'string' && /^(0|[1-9]\d*)$/.test(input.expected_revision), 'Explicit observed revision required');
  const revision = Number(input.expected_revision);
  requireCondition(Number.isSafeInteger(revision) && revision < Number.MAX_SAFE_INTEGER, 'Invalid observed revision');
  requireCondition(uuid.test(input.operation_id ?? ''), 'Persisted UUID-v4 operation_id required');
  requireCondition(typeof input.reason === 'string' && input.reason.trim() && input.reason.length <= 1000, 'Explicit operation reason required');
  requireCondition(semver.test(input.expected_stable_version ?? ''), 'Explicit expected stable version required');
  requireCondition(input.expected_candidate_version === 'none' || semver.test(input.expected_candidate_version ?? ''), 'Expected candidate version required; use none for no candidate');
  const plan = {
    action: input.action,
    request: { action: input.action, expected_revision: revision, operation_id: input.operation_id, reason: input.reason.trim() },
    expected_stable_version: input.expected_stable_version,
    expected_candidate_version: input.expected_candidate_version === 'none' ? null : input.expected_candidate_version,
  };
  if (['prepare', 'publish'].includes(input.action)) {
    validateManifest(input.manifest);
    plan.manifest = input.manifest;
    if (input.action === 'prepare') plan.request.manifest = input.manifest;
    else {
      requireCondition(input.expected_candidate_version === input.manifest.version, 'Publish manifest must match expected candidate');
      requireCondition(semver.test(input.previous_version ?? ''), 'Previous native Desktop version required for publish');
      const next = input.manifest.version.split('.').map(Number), previous = input.previous_version.split('.').map(Number);
      const index = next.findIndex((value, i) => value !== previous[i]);
      requireCondition([...next, ...previous].every(Number.isSafeInteger) && index >= 0 && next[index] > previous[index],
        'Previous native Desktop version must be older than candidate');
      plan.previous_version = input.previous_version;
    }
  }
  requireCondition(Buffer.byteLength(JSON.stringify(plan.request)) <= 32_768, 'Operation exceeds authority request limit');
  return plan;
}

export async function executePlan(plan, { token, fetcher = fetch, record = async () => {}, verify = verifyControlledDesktopDelivery } = {}) {
  const normalized = buildPlan({ action: plan.action, expected_revision: plan.request && String(plan.request.expected_revision),
    operation_id: plan.request?.operation_id, reason: plan.request?.reason,
    expected_stable_version: plan.expected_stable_version, expected_candidate_version: plan.expected_candidate_version ?? 'none',
    previous_version: plan.previous_version, manifest: plan.manifest });
  requireCondition(JSON.stringify(plan) === JSON.stringify(normalized), 'Plan differs from validated operation; no request sent');
  requireCondition(typeof token === 'string' && token.length >= 32 && !/[\r\n]/.test(token), 'DESKTOP_UPDATE_OPERATOR_TOKEN is missing or invalid; no request sent');
  const call = async (path, body) => {
    const response = await fetcher(`${origin}${path}`, {
      method: body ? 'POST' : 'GET', redirect: 'error', signal: AbortSignal.timeout(30_000),
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'User-Agent': 'Nowledge-Mem-Desktop-Operator/1' },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    let value;
    try { value = await response.json(); } catch { throw new Error(`Authority returned non-JSON HTTP ${response.status}`); }
    requireCondition(response.status === 200, `Authority HTTP ${response.status}: ${/^desktop_update_[a-z_]+$/.test(value?.error_code ?? '') ? value.error_code : 'request failed'}`);
    return value;
  };
  const status = await call('/admin/desktop-updates');
  await record('status-before.json', status);
  requireCondition(Number.isSafeInteger(status.revision) && status.revision >= 0, 'Malformed authority status');
  if (plan.action === 'status') return status;
  const history = await call('/admin/desktop-updates/history?limit=100');
  await record('history-before.json', history);
  if (plan.action === 'history') return history;
  requireCondition(Array.isArray(history.entries), 'Malformed authority history');
  const replay = history.entries.find(r => r.operation_id === plan.request.operation_id);
  if (!replay) {
    requireCondition(status.revision === plan.request.expected_revision, 'Authority revision changed; inspect status/history, do not auto-increment or retry with a new UUID');
    requireCondition(status.stable?.version === plan.expected_stable_version &&
      (status.candidate?.version ?? null) === plan.expected_candidate_version, 'Stable/candidate identity changed; no mutation sent');
    const modes = { prepare: ['stable', 'recovered', 'completed'], publish: ['draft', 'held'],
      hold: ['draft', 'published'], recover: ['held', 'stopped'], stop: ['stable', 'recovered', 'completed', 'draft', 'published', 'held', 'stopped'] };
    requireCondition(modes[plan.action].includes(status.mode), 'Authority mode does not allow this operation');
    if (plan.action === 'publish') {
      requireCondition(status.candidate.source_sha === plan.manifest.source_sha, 'Candidate source SHA mismatch');
      for (const [p, a] of Object.entries(plan.manifest.artifacts)) {
        const actual = status.candidate.artifacts?.[p];
        requireCondition(actual && ['key', 'size', 'etag', 'sha256'].every(k => actual[k] === a[k]), `${p}: registered candidate differs from qualified manifest`);
      }
    }
  } else {
    requireCondition(replay.action === plan.action && replay.revision === plan.request.expected_revision + 1,
      'Persisted operation UUID receipt does not match the request');
    // Replay the EXACT request. The authority validates body and actor against
    // its saved fingerprint before returning the original receipt.
  }
  const receipt = await call('/admin/desktop-updates', plan.request);
  await record('operation-receipt.json', receipt);
  requireCondition(receipt.operation_id === plan.request.operation_id && receipt.action === plan.action &&
    receipt.revision === plan.request.expected_revision + 1, 'Operation response does not match request; inspect persisted receipt before any retry');
  const after = await call('/admin/desktop-updates');
  await record('status-after.json', after);
  const expectedMode = { prepare: 'draft', publish: 'published', hold: 'held', stop: 'stopped', recover: 'recovered' }[plan.action];
  const expectedCandidate = plan.action === 'recover' ? null : plan.action === 'prepare' ? plan.manifest.version : plan.expected_candidate_version;
  requireCondition(after.revision === receipt.revision && after.mode === expectedMode &&
    after.stable?.version === plan.expected_stable_version && (after.candidate?.version ?? null) === expectedCandidate,
    'Authority changed after operation; inspect receipts, no automatic recovery or rollback');
  if (plan.action === 'publish') {
    const decisions = await verify({ manifest: plan.manifest, previousVersion: plan.previous_version });
    await record('native-delivery-receipt.json', decisions);
    requireCondition(decisions.length === 6 && decisions.every(d => d.revision === receipt.revision),
      'Native decisions do not match the published revision; inspect status/history');
  }
  return receipt;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.includes('--plan')) {
      const manifest = process.env.QUALIFIED_MANIFEST ? JSON.parse(process.env.QUALIFIED_MANIFEST) : undefined;
      const plan = buildPlan({ action: process.env.OPERATION_ACTION, expected_revision: process.env.EXPECTED_REVISION,
        operation_id: process.env.OPERATION_ID, reason: process.env.OPERATION_REASON,
        expected_stable_version: process.env.EXPECTED_STABLE_VERSION, expected_candidate_version: process.env.EXPECTED_CANDIDATE_VERSION,
        previous_version: process.env.VERIFY_PREVIOUS_DESKTOP_VERSION, manifest });
      await writeFile('desktop-update-plan.json', JSON.stringify(plan, null, 2) + '\n');
      console.log('Validated operation plan saved. No token used or HTTP request sent.');
    } else {
      requireCondition(process.argv.includes('--execute'), 'Choose --plan or --execute');
      const plan = JSON.parse(await readFile('desktop-update-plan.json', 'utf8'));
      await mkdir('desktop-update-receipts', { recursive: true });
      const result = await executePlan(plan, { token: process.env.DESKTOP_UPDATE_OPERATOR_TOKEN,
        record: (name, value) => writeFile(`desktop-update-receipts/${name}`, JSON.stringify(value, null, 2) + '\n') });
      console.log(JSON.stringify(result, null, 2));
    }
  } catch (error) {
    console.error(`Desktop update operation failed: ${error.message}`);
    process.exitCode = 1;
  }
}
