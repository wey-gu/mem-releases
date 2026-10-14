#!/usr/bin/env python3
"""Override only nmem-server; measure real rustc work, never a cache hit."""
import json, os, signal, subprocess, sys, time
from pathlib import Path

def descendants(rows, root):
    keep = {root}
    while True:
        more = {pid for pid, parent, rss in rows if parent in keep}
        if more <= keep: break
        keep |= more
    return sum(rss for pid, parent, rss in rows if pid in keep)

def command(compiler, args, package, units):
    if package != 'nmem-server': return ['sccache', compiler, *args]
    if units not in (1, 4, 16): raise ValueError('codegen units must be 1, 4 or 16')
    return [compiler, *args, '-C', f'codegen-units={units}']

def main():
    compiler, *args = sys.argv[1:]
    package = os.environ.get('CARGO_PKG_NAME', '')
    units = int(os.environ['NMEM_EXPERIMENT_CODEGEN_UNITS'])
    cmd = command(compiler, args, package, units)
    if package != 'nmem-server' or '--crate-name' not in args:
        return subprocess.call(cmd)
    started = time.monotonic()
    child = subprocess.Popen(cmd, start_new_session=True)
    peak = 0
    exceeded = False
    while child.poll() is None:
        rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss='], text=True)
        rows = [tuple(map(int, line.split())) for line in rows.splitlines() if len(line.split()) == 3]
        peak = max(peak, descendants(rows, int(os.environ.get('NMEM_EXPERIMENT_MEMORY_ROOT_PID', child.pid))))
        if peak * 1024 > 6_000_000_000:
            exceeded = True
            os.killpg(child.pid, signal.SIGKILL)
        time.sleep(1)
    kind = args[args.index('--crate-type') + 1] if '--crate-type' in args else 'unknown'
    original_units = 16
    for arg in args:
        value = arg.removeprefix('-C')
        if value.startswith('codegen-units='): original_units = int(value.split('=', 1)[1])
    record = {'original_codegen_units': original_units, 'codegen_units': units, 'crate_type': kind, 'elapsed_seconds': round(time.monotonic()-started, 3),
              'peak_rss_mib': round(peak/1024, 2), 'memory_limit_exceeded': exceeded, 'memory_limit_bytes': 6_000_000_000,
              'exit_code': child.returncode, 'sccache_bypassed': True, 'effective_codegen_override': cmd[-2:]}
    path = Path(os.environ['NMEM_EXPERIMENT_RESULTS'])
    with path.open('a') as handle: handle.write(json.dumps(record)+'\n')
    print('CODEGEN_SAMPLE '+json.dumps(record), flush=True)
    return child.returncode if child.returncode >= 0 else 137
if __name__ == '__main__': sys.exit(main())
