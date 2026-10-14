// Read-only check that every promoted GA image is the exact RC digest, and
// that the CPU manifest lists really contain both linux/amd64 and linux/arm64.
// A tag that merely exists is not evidence: compare digests and platforms.
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const CPU_REPOS = ['docker.io/nowledgelabs/mem', 'docker.io/nowledgelabs/mem-updater'];
const GPU_REPO = 'docker.io/nowledgelabs/mem';

function dockerInspect(ref) {
  const output = execFileSync('docker', ['buildx', 'imagetools', 'inspect', ref,
    '--format', '{{json .Manifest}}'], { encoding: 'utf8' });
  return JSON.parse(output);
}

function platformsOf(manifest) {
  return new Set((manifest.manifests || [])
    .map((entry) => `${entry.platform?.os}/${entry.platform?.architecture}`));
}

export async function verifyDockerPromotion({ rc, ga, cpu = true, gpu = [], inspect = dockerInspect }) {
  const results = [];
  const pairs = [];
  if (cpu) for (const repo of CPU_REPOS) pairs.push([`${repo}:${rc}`, `${repo}:${ga}`, true]);
  for (const variant of gpu) pairs.push([`${GPU_REPO}:${rc}-${variant}`, `${GPU_REPO}:${ga}-${variant}`, false]);
  for (const [source, target, multiArch] of pairs) {
    const want = (await inspect(source))?.digest;
    const got = await inspect(target);
    if (!/^sha256:[0-9a-f]{64}$/.test(want || '')) throw new Error(`${source}: digest is unreadable`);
    if (got?.digest !== want) throw new Error(`${target}: digest ${got?.digest} != RC ${want}`);
    if (multiArch) {
      const platforms = platformsOf(got);
      for (const required of ['linux/amd64', 'linux/arm64']) {
        if (!platforms.has(required)) throw new Error(`${target}: manifest list lacks ${required}`);
      }
    }
    results.push(`${target} -> ${want}`);
    console.log(`OK ${target} = ${source} (${want})${multiArch ? ' [linux/amd64, linux/arm64]' : ''}`);
  }
  return results;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [rc, ga, cpu, ...gpu] = process.argv.slice(2);
  verifyDockerPromotion({ rc, ga, cpu: cpu === 'cpu', gpu }).catch((error) => {
    console.error(`::error::Docker promotion verification failed: ${error.message}`);
    process.exitCode = 1;
  });
}
