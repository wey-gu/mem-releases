# Windows self-hosted release runner

`windows-book` is an optional runner for the standard Windows Desktop build.
Hosted `windows-2022` stays the default; GPU variants stay hosted. Select
`windows_self_hosted=true` on a manual `release-desktop` dispatch only when the
machine is available. A self-hosted selection does not automatically fall back
when the machine is offline.

The runner is registered to `wey-gu/mem-releases` with only `windows-book` and
`windows-book-feasibility` labels. `Nowledge-Mem-Release-Runner` starts it under
the owner's interactive user at logon. It remains online while that user is
logged in; it is not a Windows service. The startup script limits runner Cargo
parallelism to four jobs while local tests can continue.

## Directories and cache ownership

| Path under `%USERPROFILE%` | Purpose |
|---|---|
| `ci-runner` | Runner binaries, credentials and isolated job workspace |
| `ci-tools` | Pinned tools, helpers and pre-change user environment backup |
| `ci-cache/compiler` | Shared local sccache storage, capped at 8 GiB |
| `ci-cache/runner-target` | Runner-only Cargo artifacts retained across jobs |
| `.cargo` | Existing shared crate downloads and registry |

**Never clean** `dev/mem/nmem-rs/target`,
`dev/mem/nowledge-graph/src-tauri/target`, or `dev/mem/.context`.
Developer tests retain their existing targets and incremental settings. Their
new processes use the same sccache wrapper; existing shells can opt in with:

```powershell
. "$env:USERPROFILE\ci-tools\Enter-BuildEnvironment.ps1"
```

One local sccache server owns the disk cache. CI skips its hosted sccache setup,
R2 configuration and stop/restart diagnostics on this runner.
`Nowledge-Mem-Compiler-Cache` starts the shared daemon independently of CI, with
idle shutdown disabled, so job orphan cleanup cannot terminate other clients'
cache service. Toolchain, features, profile and source differences are separate cache entries. Incremental
Rust, metadata-only checks and linker-invoking crates may compile uncached;
Cargo's dependency artifacts and compatible Rust/C++ compilations still reuse.
Do not point developer tests at the runner's `target` or stop the shared daemon.

## Validation and disk maintenance

Manual `windows_runner_probe=true`, `publish=false` skips the entire release
graph. It validates tools and native compilation without source checkout or
release credentials. `windows_cache_benchmark=true` also measures `nmem-wire`
from the local developer checkout using fresh, separate target directories.
Detailed compiler output stays local; the job emits only the measurement.

`Maintain-Disk.ps1` defaults to a dry run. `-Apply` removes only files older than
30 days from pip's download cache and our tool-download directory. It refuses
protected-path overlap and skips reparse points, including ancestor junctions.
It does not clear application data, sources, installed packages, test outputs,
Cargo registries or other compiler artifacts. A daily user task runs this narrow
maintenance; sccache evicts within its own 8 GiB limit. Desktop jobs require at
least 40 GiB free before starting. If the guard fails, free space elsewhere or
choose the hosted runner; do not delete the protected tests to pass it.

Initial real cache measurement: `nmem-wire` release 197.866 → 175.122 seconds
between fresh local-test/runner targets, with 33 Rust hits observed. This is an
11.5% component improvement, not a measured full Desktop release speedup.
Retaining the runner target's dependency artifacts and cleaning only our
`nmem-wire` package before rebuilding took 30.728 seconds, with one Rust
cache hit observed. That is about 6.4x faster than the initial component build;
it does not establish the full backend's rebuild cost after a version change.
