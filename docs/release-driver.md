# Resumable local release driver

Use `scripts/release_driver.py` on macOS or Linux to replace per-release
observer-launch dictionaries. Python 3.11+ is sufficient; no new service,
credential, shell wrapper or release workflow is required. Commands can be
issued from any directory using the absolute driver and plan paths.

This is a local execution journal, not a replacement for release qualification.
The existing helpers still own exact-SHA CI admission, reviewed website
publication/readback, native artifact execution, public byte verification and
Desktop authority prepare/publish/complete. An exit code or driver plan alone
never proves that a release was delivered. The driver does not approve PRs,
synchronize a mirror without its owner, or trigger ARM64 native-link CI.

## Prepare the GA observer plan once

For a **new** release record directory containing the reviewed GA helper set:

```bash
python3 /path/to/mem-releases/scripts/release_driver.py prepare-ga \
  --plan /path/to/release-records/0.10.119/ga/driver.json \
  --version 0.10.119 \
  --source-sha <full-selected-mem-sha> \
  --tools-sha <full-reviewed-tools-sha>
```

Preparation executes no helper and triggers no remote operation. It verifies
all expected helpers exist, reads `SOURCE`, `TOOLS` and `VERSION` as literal
constants from `release_ops.py` without importing it, and rejects mismatches.
It pins every direct `.py`, `.mjs` and `.sh` helper in that directory by SHA-256.
Review the plan, imported helper closure, receipts and the helpers' existing
admission checks before executing it. Add pins for helper inputs outside that
directory when applicable. The tools SHA is release context; script hashing is
not proof of Git ancestry, review or workflow identity. The existing admission
helpers must establish those facts.

The generated graph starts the original qualification/dispatch helper once,
then joins promotion and distribution, CLI/mobile acceptance, Desktop authority,
website date/review/deployment, public readbacks and the final seal. Every stage
in this profile is an `action`: even a helper named `watch` can dispatch a
promotion or write an acceptance receipt. None is assumed safe to retry.

Preparation refuses an existing plan, state directory, dispatch receipt or
completion seal. It cannot adopt an already running or completed release.
Do not use it retroactively on the 118 record. Preserve its sealed evidence and
continue existing operations through their original recovery path.

## Run, inspect and resume from one directory

```bash
python3 /path/to/mem-releases/scripts/release_driver.py run \
  --plan /path/to/release-records/0.10.119/ga/driver.json --jobs 4

python3 /path/to/mem-releases/scripts/release_driver.py status \
  --plan /path/to/release-records/0.10.119/ga/driver.json
```

Run the same command to continue a plan whose earlier steps finished. Successful
actions are skipped, pending actions wait for all their dependencies, and no
new actions start after a helper fails. `status` never executes a helper or
creates/updates state; it remains usable while a driver is running.

`--jobs` defaults to one and is bounded at eight. Use a small observer-only
concurrency such as four for the GA profile. Keep local compilation and test
plans serial; this option is not permission to run heavy local suites in
parallel. Existing helpers must not wait for undeclared sibling tasks: model
such readiness as explicit `needs` dependencies.

The driver stores `.release-driver/state.json`, a release-directory-wide single-owner file lock and
per-operation logs alongside the plan. It fsyncs each operation UUID and intent
before launching the corresponding argument vector, without a shell, in the
plan-relative `cwd`. It supplies `RELEASE_DRIVER_OPERATION_ID`,
`RELEASE_DRIVER_SOURCE_SHA`, `RELEASE_DRIVER_TOOLS_SHA` and
`RELEASE_DRIVER_VERSION` to the helper. These do not replace the helper's own
persisted remote operation UUID or workflow-run receipt.

Each release record directory admits one plan, even if another driver uses a different plan filename. State binds the absolute plan path and the entire plan fingerprint. Changing commands, dependencies,
source/tools identity or a pinned helper stops continuation. Logs and prior
attempts are retained. Do not put secrets in a plan or command arguments; use
the existing named operator/authentication mechanisms.

## Custom RC or read-only plans

A reviewed plan can describe other local helpers without another launcher:

```json
{
  "schema": 1,
  "release": {
    "version": "0.10.119-rc1",
    "source_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "tools_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "files": {
    "check_source.py": "<actual-sha256>",
    "dispatch_private.py": "<actual-sha256>"
  },
  "steps": [
    {
      "id": "source",
      "kind": "check",
      "argv": ["{python}", "check_source.py"]
    },
    {
      "id": "private-build",
      "kind": "action",
      "needs": ["source"],
      "argv": ["{python}", "dispatch_private.py"]
    }
  ]
}
```

Replace all example identities and digests with real frozen values. `cwd`
defaults to the plan's directory; `{python}` selects the driver interpreter.
Hash helpers with `shasum -a 256`. Missing dependencies, cycles, duplicate IDs,
moving source refs and changed file pins fail before helper execution.

Use `check` **only** for a truly repeatable, read-only verifier. Every invocation
re-executes checks to refresh their evidence, including checks that failed or
were interrupted. A check crash with a recorded still-live PID blocks another
copy. Dispatch, upload, publication, authority changes, receipt collection with
exclusive outputs, and mixed observer/dispatcher scripts are `action` steps.
A completed prerequisite's old file existence never substitutes for running its
check successfully in this invocation.

## Failure and interruption

A nonzero child exit fails the driver. It stops its other local child process
groups and marks their results uncertain. It does **not** cancel remote Actions
runs or undo a partial publication. Failed/interrupted actions and an intent
left `running` by a crash block automatic continuation. Inspect their retained
logs and remote operation receipts; use the original workflow's controlled
recovery and independent review. There is intentionally no generic retry,
force-success or acknowledgement flag that could repeat an uncertain write.
Do not delete the state directory or make a second plan to circumvent this stop.

After a classified repair, explicitly reconcile the release through its existing
owner-controlled recovery procedure. The driver does not currently import
external success receipts into a failed action. This limitation keeps remote
identity and artifact acceptance checks in their authoritative helpers rather
than trusting a manually supplied boolean.
