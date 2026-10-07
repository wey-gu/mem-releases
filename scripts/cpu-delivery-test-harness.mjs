const version = '0.10.96';
const digest = n => `sha256:${String(n).repeat(64)}`;
export function cpuHarness({ state = 'success', sourceVersion = version } = {}) {
  const receipts = new Map();
  for (const [role, runId, sourceRepo] of [['mem', '11', 'nowledge-co/mem'], ['mem-updater', '22', 'nowledge-co/community']]) {
    receipts.set(runId, { schema: 1, repository: 'wey-gu/mem-releases', workflow: `.github/workflows/release-docker${role === 'mem' ? '' : '-updater'}.yml`,
      run_id: runId, run_attempt: 1, tooling_sha: 'b'.repeat(40), version: sourceVersion, role,
      source_repository: sourceRepo, source_ref: `v${sourceVersion}`, source_sha: 'a'.repeat(40),
      digest: digest(1), arch_digests: { amd64: digest(2), arm64: digest(3) } });
  }
  const trusted = structuredClone(receipts);
  const api = async (method, path) => {
    const id = path.match(/runs\/(\d+)/)?.[1];
    const receipt = trusted.get(id);
    if (path.includes('/git/ref/')) return { object: { type: 'commit', sha: 'a'.repeat(40) } };
    if (path.endsWith('/jobs?per_page=100')) return { total_count: 5, jobs: ['meta', 'publish', 'verify-amd64', 'verify-arm64', 'cpu-receipt'].map(name => ({ name, status: state === 'pending' ? 'in_progress' : 'completed', conclusion: state })) };
    if (path.endsWith('/artifacts?per_page=100')) return { total_count: 1, artifacts: [{ id: Number(id), name: `cpu-delivery-${sourceVersion}-${receipt.role}-attempt-1`, expired: false }] };
    if (receipt) return { id: Number(id), repository: { full_name: receipt.repository }, path: receipt.workflow, run_attempt: 1, head_sha: receipt.tooling_sha, event: 'workflow_dispatch' };
    throw new Error(`Unexpected CPU API: ${method} ${path}`);
  };
  return { api, cpuRuns: { mem: '11', 'mem-updater': '22' }, sourceVersion,
    receiptReader: async id => receipts.get(String(id)),
    manifestReader: async () => ({ digest: digest(1), arch_digests: { amd64: digest(2), arm64: digest(3) } }), receipts };
}
