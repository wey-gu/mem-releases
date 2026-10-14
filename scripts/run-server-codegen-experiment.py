#!/usr/bin/env python3
"""One frozen source/overlay/runner; only the server changes between samples."""
import argparse, hashlib, importlib.util, json, os, subprocess, sys, time, tomllib
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    results = args.results.resolve()
    results.mkdir(parents=True, exist_ok=True)
    builder_path = repo/'integrations/hawdb/build-embedded-server.py'
    spec = importlib.util.spec_from_file_location('desktop_builder', builder_path)
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    source_sha, hawdb_sha = builder.require_exact_hawdb_checkout(repo)
    stage = results/'overlay'
    subprocess.run([sys.executable, str(repo/'integrations/hawdb/prepare-nightly-server.py'),
                    '--repo-root', str(repo), '--output', str(stage/'Cargo.toml'),
                    '--lockfile', str(repo/'integrations/hawdb/nightly-server.Cargo.lock')], check=True)
    manifest = tomllib.loads((stage/'Cargo.toml').read_text())
    inventory = {'source_sha': source_sha, 'hawdb_sha': hawdb_sha,
                 'generated_release_profile': manifest.get('profile', {}).get('release', {}),
                 'workspace_release_profile': tomllib.loads((repo/'nmem-rs/Cargo.toml').read_text()).get('profile', {}).get('release', {}),
                 'lock_sha256': hashlib.sha256((stage/'Cargo.lock').read_bytes()).hexdigest(),
                 'rustc': subprocess.check_output(['rustc', '-vV'], text=True),
                 'cpu_count': os.cpu_count(), 'target': 'aarch64-apple-darwin'}
    (results/'inputs.json').write_text(json.dumps(inventory, indent=2))
    env = os.environ.copy()
    env['CARGO_TARGET_DIR'] = str(repo/'nmem-rs/target')
    env['NMEM_BUILD_SHA_FULL'] = source_sha
    env['RUSTC_WRAPPER'] = str(Path(__file__).with_name('server-codegen-wrapper.py').resolve())
    env['NMEM_EXPERIMENT_MEMORY_ROOT_PID'] = str(os.getpid())
    env['NMEM_EXPERIMENT_RESULTS'] = str(results/'samples.jsonl')
    statuses = []
    for units in (1, 4, 16):
        env['NMEM_EXPERIMENT_CODEGEN_UNITS'] = str(units)
        subprocess.run(['cargo', 'clean', '--manifest-path', str(stage/'Cargo.toml'), '-p', 'nmem-server',
                        '--release', '--target', inventory['target']], cwd=repo, env=env, check=True)
        cmd = builder.cargo_command(cargo='cargo', manifest_path=stage/'Cargo.toml', profile='release',
                                    target=inventory['target'], gpu='none', channel='production')
        started = time.monotonic()
        run = subprocess.run(cmd, cwd=repo, env=env)
        result = {'codegen_units': units, 'exit_code': run.returncode, 'build_seconds': round(time.monotonic()-started, 3)}
        if run.returncode == 0:
            binary = repo/'nmem-rs/target'/inventory['target']/'release/nmem-server'
            version = subprocess.check_output([str(binary), '--version'], text=True, timeout=15)
            if f'nmem-build-sha:{source_sha}' not in version or 'nmem-storage-build:hawdb' not in version:
                raise ValueError('built server provenance/storage identity mismatch')
            result.update(binary_bytes=binary.stat().st_size, version=version.strip())
        statuses.append(result)
        (results/'builds.json').write_text(json.dumps(statuses, indent=2))
    samples = [json.loads(line) for line in (results/'samples.jsonl').read_text().splitlines()]
    libs = {r['codegen_units']:r for r in samples if r['crate_type'] == 'lib' and r['exit_code'] == 0}
    current_units = next(r['original_codegen_units'] for r in samples if r['crate_type'] == 'lib')
    baseline = libs.get(current_units)
    candidates = []
    if baseline:
        for units in (4,16):
            row = libs.get(units)
            if units != current_units and row and row['peak_rss_mib'] * 1024 * 1024 <= 6_000_000_000:
                gain = 1-row['elapsed_seconds']/baseline['elapsed_seconds']
                if gain >= 0.2: candidates.append((row['elapsed_seconds'], units, round(gain,4)))
    recommendation = min(candidates) if candidates else None
    summary = {'library_samples': libs, 'current_release_codegen_units': current_units, 'recommendation': recommendation,
               'note': 'Compilation only. Dependencies unchanged; total first-build time includes cache warmup. Generated release baseline must be read from inputs.json.'}
    (results/'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if baseline else 1
if __name__ == '__main__': sys.exit(main())
