// Immutable CPU verification receipts. GPU, scan and deferred RPM are separate.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ghApi } from './finalize-ga-release.mjs';

const REPO = 'wey-gu/mem-releases';
const ROLES = { mem: 'nowledge-co/mem', 'mem-updater': 'nowledge-co/community' };
const workflow = role => `.github/workflows/release-docker${role === 'mem' ? '' : '-updater'}.yml`;
const digestPattern = /^sha256:[a-f0-9]{64}$/;
const requireCondition = (condition, detail) => { if (!condition) throw new Error(`CPU delivery ${detail}`); };

export function imageManifest(image) {
  const inspect = (reference, args) => {
    const result = spawnSync('docker', ['buildx', 'imagetools', 'inspect', reference, ...args], { encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024 });
    requireCondition(result.status === 0, 'manifest lookup failed');
    return JSON.parse(result.stdout);
  };
  const summary = inspect(image, ['--format', '{{json .Manifest}}']);
  requireCondition(digestPattern.test(summary.digest), 'manifest digest is invalid');
  const index = inspect(`${image.slice(0, image.lastIndexOf(':'))}@${summary.digest}`, ['--raw']);
  const arch_digests = {};
  for (const arch of ['amd64', 'arm64']) {
    const matches = index.manifests?.filter(item => item.platform?.os === 'linux' && item.platform?.architecture === arch) ?? [];
    requireCondition(matches.length === 1, `manifest must contain exactly one linux/${arch}`);
    arch_digests[arch] = matches[0].digest;
  }
  return { digest: summary.digest, arch_digests };
}

function readReceipt(artifactId) {
  const download = spawnSync('gh', ['api', `repos/${REPO}/actions/artifacts/${artifactId}/zip`], { timeout: 30000, maxBuffer: 1024 * 1024 });
  requireCondition(download.status === 0, 'receipt download failed');
  const directory = mkdtempSync(join(tmpdir(), 'nmem-cpu-receipt-'));
  try {
    const zip = join(directory, 'receipt.zip');
    writeFileSync(zip, download.stdout);
    // Read only this JSON entry; never extract or execute artifact contents.
    const decoded = spawnSync('unzip', ['-p', zip, 'receipt.json'], { encoding: 'utf8', timeout: 5000, maxBuffer: 65536 });
    requireCondition(decoded.status === 0, 'receipt JSON is unavailable');
    return JSON.parse(decoded.stdout);
  } finally { rmSync(directory, { recursive: true, force: true }); }
}

async function sourceSha(repo, ref, api, token) {
  let { object } = await api('GET', `repos/${repo}/git/ref/tags/${ref}`, undefined, token);
  for (let depth = 0; object.type === 'tag' && depth < 5; depth += 1) {
    ({ object } = await api('GET', `repos/${repo}/git/tags/${object.sha}`, undefined, token));
  }
  requireCondition(object.type === 'commit' && /^[a-f0-9]{40}$/.test(object.sha), 'source tag does not resolve to a commit');
  return object.sha;
}

export async function requireCpuDelivery(version, {
  api = ghApi, cpuRuns = { mem: process.env.CPU_MEM_RUN_ID, 'mem-updater': process.env.CPU_UPDATER_RUN_ID },
  sourceVersion = process.env.CPU_SOURCE_VERSION || version,
  deliveryVersion = process.env.CPU_DELIVERY_VERSION || version,
  receiptReader = readReceipt, manifestReader = imageManifest,
  readToken = process.env.MEM_REPO_TOKEN,
} = {}) {
  requireCondition(/^\d+\.\d+\.\d+$/.test(version) &&
    (sourceVersion === version || new RegExp(`^${version.replaceAll('.', '\\.')}-rc[1-9][0-9]*$`).test(sourceVersion)), 'source version must be this GA or its RC');
  requireCondition([version, sourceVersion].includes(deliveryVersion), 'delivery version must be this GA or its validated RC');
  for (const [role, repo] of Object.entries(ROLES)) {
    const id = String(cpuRuns[role] ?? '');
    requireCondition(/^[1-9][0-9]*$/.test(id), `${role} run id is required`);
    const run = await api('GET', `repos/${REPO}/actions/runs/${id}`);
    requireCondition(String(run.id) === id && run.repository?.full_name === REPO && run.path === workflow(role) &&
      ['push', 'workflow_dispatch'].includes(run.event) && Number.isInteger(run.run_attempt), 'run identity is invalid');
    const jobs = await api('GET', `repos/${REPO}/actions/runs/${id}/attempts/${run.run_attempt}/jobs?per_page=100`);
    requireCondition(jobs.total_count <= 100 && ['meta', 'publish', 'verify-amd64', 'verify-arm64', 'cpu-receipt'].every(name => {
      const matches = jobs.jobs.filter(job => job.name === name);
      return matches.length === 1 && matches[0].status === 'completed' && matches[0].conclusion === 'success';
    }), `${role} jobs must reach terminal success`);
    const artifacts = await api('GET', `repos/${REPO}/actions/runs/${id}/artifacts?per_page=100`);
    const matches = artifacts.artifacts.filter(artifact => !artifact.expired && artifact.name === `cpu-delivery-${sourceVersion}-${role}-attempt-${run.run_attempt}`);
    requireCondition(artifacts.total_count <= 100 && matches.length === 1, 'receipt is missing, expired or ambiguous');
    const receipt = await receiptReader(matches[0].id);
    const sha = await sourceSha(repo, `v${sourceVersion}`, api, repo === ROLES.mem ? readToken : undefined);
    const gaSha = await sourceSha(repo, `v${version}`, api, repo === ROLES.mem ? readToken : undefined);
    requireCondition(receipt.schema === 1 && receipt.repository === REPO && receipt.workflow === run.path && receipt.role === role &&
      receipt.run_id === id && receipt.run_attempt === run.run_attempt && receipt.tooling_sha === run.head_sha &&
      receipt.version === sourceVersion && receipt.source_repository === repo && receipt.source_ref === `v${sourceVersion}` &&
      receipt.source_sha === sha && gaSha === sha, 'receipt identity/version/source SHA does not match');
    const manifest = await manifestReader(`docker.io/nowledgelabs/${role}:${deliveryVersion}`);
    requireCondition(digestPattern.test(receipt.digest) && receipt.digest === manifest.digest && ['amd64', 'arm64'].every(arch =>
      digestPattern.test(receipt.arch_digests?.[arch]) && receipt.arch_digests[arch] === manifest.arch_digests?.[arch]), 'target digest differs from the verified CPU receipt');
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const [version, role, sha, digest, amd64, arm64] = process.argv.slice(2);
    requireCondition(process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_REPOSITORY === REPO &&
      process.env.GITHUB_WORKFLOW_REF?.startsWith(`${REPO}/${workflow(role)}@`), 'receipt writer must run in its official workflow');
    requireCondition(ROLES[role] && /^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9]+)*$/.test(version) && /^[a-f0-9]{40}$/.test(sha), 'receipt source is invalid');
    const manifest = imageManifest(`docker.io/nowledgelabs/${role}:${version}`);
    requireCondition([digest, amd64, arm64].every(item => digestPattern.test(item)) && manifest.digest === digest &&
      manifest.arch_digests.amd64 === amd64 && manifest.arch_digests.arm64 === arm64, 'published tag differs from verified digests');
    writeFileSync('receipt.json', JSON.stringify({ schema: 1, repository: REPO, workflow: workflow(role), role,
      run_id: process.env.GITHUB_RUN_ID, run_attempt: Number(process.env.GITHUB_RUN_ATTEMPT), tooling_sha: process.env.GITHUB_SHA,
      version, source_repository: ROLES[role], source_ref: `v${version}`, source_sha: sha,
      digest, arch_digests: { amd64, arm64 }, verified_at: new Date().toISOString() }, null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
