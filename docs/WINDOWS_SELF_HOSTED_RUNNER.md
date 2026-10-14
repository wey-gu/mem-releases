# Windows self-hosted release runner

The standard Windows Desktop build defaults to `windows_runner=auto`: select
`windows-book` when GitHub's runner inventory reports this exact labelled machine
online and idle; otherwise use GitHub-hosted `windows-2022`. Offline, busy,
missing credentials, API/auth errors and the 10-second query timeout all select
GitHub. GPU variants remain hosted.

Only `tag=nightly` builds (including `windows_only` qualification) can use
`windows-book`. Every release (tag push, repository dispatch, or a dispatch
with any other tag, rc or GA) builds Windows on GitHub-hosted `windows-2022`,
whatever `windows_runner` says: promotion re-tags rc bits, so an rc built on
this machine would ship as GA (nowledge-co/mem#7108).

The selector uses the existing `RELEASE_APP_ID` / `RELEASE_APP_PRIVATE_KEY`
installation, requesting only repository Administration read permission. The
App must have that permission and be installed on `mem-releases`; if it does not,
auto safely uses GitHub and token creation reports the configuration error.
Use `windows_runner=hosted` to force GitHub, or `self-hosted` to force the local
machine. Legacy `windows_self_hosted=true` forces self-hosted only in auto mode.

This is availability selection before dispatch, not an automatic rebuild after
failure. A selected machine can go offline between the check and assignment,
or fail during a build; those cases still require rerunning with `hosted`.

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
| `ci-cache/pnpm-store` | Persistent pnpm package downloads shared across runner jobs |
| `.cargo` | Existing shared crate downloads and registry |

**Never clean** `dev/mem/nmem-rs/target`,
`dev/mem/nowledge-graph/src-tauri/target`, or `dev/mem/.context`.
The self-hosted job exports `NMEM_PNPM_STORE_DIR` before `pnpm/action-setup`
and installs with explicit `--store-dir` and `--prefer-offline`. Without this override, pnpm 11 uses the
store inside the action's `PNPM_HOME`, which the action deletes and recreates on
the next job. The persistent store is outside checkout and action installation
paths. Checkout still recreates `node_modules`, so linking and install scripts
remain; measure a warm run before promising a wall-time reduction.

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

## Full Windows qualification

Dispatch `release-desktop` with `tag=nightly`, `source_ref=<mem commit SHA>`,
`publish=false`, `windows_only=true`, and the desired `windows_runner` mode.
It runs the same standard Windows release build and NSIS packaging steps;
macOS/Linux, GPU variants, nightly all-platform reporting, artifact uploads and
all publishing jobs are skipped. `windows_only` is rejected for release tags.
Use a fixed SHA when comparing runner timing; hardware and cache state differ.
The self-hosted shared cache statistics can also include developer clients;
do not restart that shared daemon to isolate measurements.

## W8 diagnostics on the shared runner

Dead Windows caches are removed on both runner modes. Hosted jobs start their
own debug daemon and upload the sanitized W8 diagnostics. Self-hosted CI never
restarts the shared daemon. For a bounded qualification, an operator copies the
updated helpers into `ci-tools`, then runs `Set-CacheDiagnostics.ps1 -Enable`
only while compiler clients are idle. This restarts the independently scheduled
server with debug logging, retaining all cache and target contents. CI records
its log offset and shared statistics and hashes the persistent runner target.

The self-hosted raw log, unit events and hashes stay in the local job workspace;
only aggregate counts enter the job log, because the shared server may include
developer clients. These counts are not exact per-job cache attribution. After
qualification, run `Set-CacheDiagnostics.ps1` without `-Enable` while compilers
are idle to restore quiet logging. If the idle guard fails, defer the operation;
never stop developer compilers to collect diagnostics.

Self-hosted builds use the preinstalled Python 3.12 runtime and check its version
before checkout. They skip `actions/setup-python`, whose Windows installer
modifies system Python registry entries and requires administrator access.
Hosted builds retain the existing setup action; do not elevate the runner to
work around installer permissions.
