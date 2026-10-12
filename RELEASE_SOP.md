# Nowledge Mem Release SOP

This is the standard path for a desktop or multi-surface App release. Its
purpose is to make the release candidate a fixed, reviewable source range
instead of a moving view of `main`.

## Invariants

- A release has one immutable source boundary: `RELEASE_BASE_SHA`.
- `main` may continue to accept ordinary work after the boundary is cut.
- Release metadata is reviewed and merged on `main` before it is copied to the
  release branch with `git cherry-pick -x`.
- No ordinary product change is cherry-picked into the release branch.
- Every release note, binary, tag, and smoke result names the same release
  branch commit.
- The public Changelog is deployed and read back before package distribution.
- Core RC artifacts are promoted to GA without rebuilding. A delayed RPM is
  promoted separately from its validated RC asset and never changes the APT
  repository or auto-updater feed.
- Desktop delivery is complete only after the previous native client protocol
  admits the qualified release on all six platforms. Legacy download/feed
  checks alone do not prove that Check for Updates works.

### Native update delivery acceptance

After all six GA objects are ready and an authorized release operator has
published the candidate, run this independent, read-only qualification before
declaring desktop delivery complete. Keep the previous stable recovery baseline
until explicit candidate acceptance. Neither this verifier nor a GA upload
changes the protected update authority; the deployed authority may still
require separate prepare/publish operations. Do not deploy Backbone to advance
a version, or infer native delivery from ordinary `/latest` and `/update.json`.

Supply a reviewed manifest derived from the qualified release bytes, with
`version`, the immutable `source_sha`, and exactly six `artifacts` entries:
`mac`, `mac_intel`, `win`, `linux-appimage`, `linux-deb`, `linux-rpm`. Each entry
needs canonical `key` (`app/<version>/<target><extension>`), positive `size`,
and independently computed lowercase `sha256`. An operator manifest with
additional ETag/notes/date fields can be used directly. Do not copy expected
hashes from the controlled endpoint being tested.

```bash
VERIFY_ARTIFACT_MANIFEST=/secure/reviewed-release-manifest.json \
VERIFY_PREVIOUS_DESKTOP_VERSION=<previous-shipped-semver> \
  node scripts/verify-controlled-desktop-delivery.mjs
```

Alternatively dispatch `qualify-desktop-update-delivery.yml` from the reviewed
release-tooling ref, supplying `previous_version` and the reviewed JSON as
`qualified_manifest`. Record the exact tooling SHA, terminal successful run and
downloaded receipt. The workflow uses public read-only requests, no operator
credential, and does not publish, enable, stop, complete, or install anything.

Acceptance requires six fresh protocol-1 allowed decisions using the previous
Desktop's native User-Agent: exact version, unique echoed request IDs, valid
60-second lifetime, `no-store`, target, immutable URL, size and SHA-256, and one
consistent authority revision. A stale candidate, held/stopped state or checksum
disagreement fails qualification even when ordinary downloads work. Inspect
the protected authority rather than retrying a publication or calling it a
client cache issue. The existing promotion jobs still verify legacy delivery;
their success does not replace this final native acceptance. An older patch
that intentionally does not become latest must not claim this latest-update
acceptance; record its direct-download-only delivery boundary separately.

### Protected CI update operations

Use `desktop-update-control.yml` to call the existing production update
authority. This is a manual operation workflow, independent of App builds,
R2 uploads, GA promotion and Backbone deployment. It runs from reviewed `main`
only, with `contents: read` and serialized operations that are never cancelled
by a newer dispatch. Do not use the existing, unprotected `release-publish`
environment for this credential.

Before the first run, configure the dedicated `desktop-update-production`
environment to allow only the `main` branch (no tag policies). The CI job reuses
existing `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` release secrets; it
requires no additional operator password. Its Cloudflare token must already
have access to the selected account and Workers preview/external bindings.
First qualify that capability with the read-only status job; ordinary contract
CI does not prove the existing token's production permissions.

Cloudflare authenticates an isolated remote preview, which binds only the
existing `DesktopUpdateControl` class in `nowledge-mem-backbone-prod`. The
preview calls the authority's existing `/status`, `/history` and `/operation`
methods, preserving CAS, artifact readiness checks, atomic audit/replay receipts
and all transition rules. It uses the fixed trusted-tool actor `release-ci`,
not a caller-provided name or an impersonated human. This is an account-managed
CI capability, separate from the public HTTP endpoint's named-operator password.
Neither customer credentials nor the general admin token gain that capability.

The preview is temporary, loopback-proxied and Cloudflare-authenticated; it is
not a public Worker deployment and does not change the production Backbone
code, routes, namespace or password map. The generated preview allows only the
exact persisted operation JSON; status/history plans cannot write. The runtime
and preview logs remain private and are removed when the owned process exits.
Never deploy this helper as a public Worker or modify namespace storage directly.
A missing management token or inaccessible preview fails before any operation.
The old public-HTTP operator CLI remains available separately for operators who
already hold its credential; the CI workflow does not use or copy that password.

Start with the read-only status job:

```bash
gh workflow run desktop-update-control.yml -R wey-gu/mem-releases \
  --ref main -f action=status
```

Download its `desktop-update-receipts-*` artifact. Each mutation requires the
exact observed `expected_revision`, `expected_stable_version`, and
`expected_candidate_version` (use the literal `none` when absent), a UUID-v4
`operation_id`, and a reviewed `reason`. Generate and retain the UUID before
dispatch. The workflow persists the exact request plan as an artifact before
calling the authority. After an unknown outcome, inspect status/history and
rerun the **identical inputs and UUID**; the server validates the saved request
fingerprint and returns its original receipt. Never increment revisions or
create a new UUID just to make a failed run pass.

`prepare` and `publish` also require `qualified_manifest` containing the
independently reviewed six-platform version/source/ETag/size/SHA-256 snapshot.
`publish` additionally requires `previous_version` and automatically runs the
six-platform native protocol qualification against the returned revision.
Pass JSON via `--json` and a prepared dispatch payload file rather than
interpolating multiline JSON into shell code. A publish success requires both
the authority readback and native delivery checks; a later verification failure
does not roll back publication. Inspect the plan and receipts before retrying.

Each dispatch performs **one** explicitly selected operation. An unfinished,
unaccepted published candidate cannot be silently replaced: after reviewing
authenticated status/history, explicitly `hold` and `recover` to retain the
known-good stable baseline, then `prepare` and `publish` the next qualified
candidate, reading the actual state between operations. Recovery checks stable
artifact availability and must succeed before preparing another candidate.
Held intervals temporarily pause controlled updates; never leave a failed
operation uninspected. `complete` and `initialize` are intentionally absent
from this repair workflow because they cross separate acceptance/bootstrap
boundaries. It does not silently mark any candidate known-good.

## 0. Verify the release capability before the cut

Before creating a branch, verify that the release workflows can read the
private product source and every source-recorded Rust dependency, and that
signing, upload, and registry secrets are available. Run the standalone Rust
bundle workflow against a known-good source ref if the dependency contract or
workflow changed recently.

The private product source still requires `MEM_REPO_TOKEN`. HawDB is public
at `https://github.com/nowledge-co/hawdb.git` and must be fetched over HTTPS
at the exact gitlink recorded by the selected source commit. Its checkout
does not require a deploy key or a HawDB-specific token. Do not replace the
recorded commit with a moving branch or tag, and do not persist checkout
credentials in the dependency.

Do not cut an RC merely to discover a missing credential. An RC tag is
immutable release evidence, not a disposable CI retry handle.

### Windows release and Bazel validation

The Windows CPU release uses the Cargo PowerShell entry point,
`scripts/build-rust-bundle.ps1`, previously used by the successful
`v0.10.91-rc2` Windows job. Unix CPU release jobs continue to use Bazel.

Keep Windows Bazel validation separate while its native compiler and Ninja
inputs are being repaired (`nowledge-co/mem#5748`). Run only that platform on
demand with an exact source SHA:

```bash
gh workflow run test-windows-bazel.yml -R wey-gu/mem-releases \
  --ref main -f source_ref=<exact-mem-sha>
```

The workflow builds the Bazel backend and NSIS installer, records the resolved
source SHA, and retains the installer as the `windows-bazel-x86_64` Actions
artifact. It runs only on manual dispatch and has no release or distribution
job. Its result is tracked separately from the core Cargo release.

## 1. Cut the release branch first

Choose the last intended product commit on `main`, fetch it, and record it:

```bash
git fetch origin main
export RELEASE_BASE_SHA="$(git rev-parse origin/main)"
git switch -c "release/<version>" "$RELEASE_BASE_SHA"
git push -u origin "release/<version>"
```

The branch is the candidate's product boundary. Do not retarget it to a newer
`main`, merge ordinary PRs into it, or append later fixes because they happen
to merge while release work is in progress. Release metadata is the one
exception: it is reviewed on `main` first, then copied with
`git cherry-pick -x` as described below. A necessary release-only repair is
allowed, but it must first be reviewed on `main`, copied with `-x`, and result
in a new RC number.

## 2. Make the version-only change on `main`

Create a version-only commit or PR with base `main`. It contains
only version fields and generated manifests or lockfiles required by the
version change. It must not contain user-facing prose or a website gitlink
update.

After it merges, record its exact commit as `RELEASE_MAIN_VERSION_SHA`. Do not
copy it to the release branch yet; the Changelog and its parent metadata must
first be reviewed and merged on `main`.

## 3. Write, merge, deploy, and verify release notes on `main`

Build the changelog worksheet from the exact range:

```text
v<previous-version>..RELEASE_BASE_SHA
```

Classify only changes in that range. A merged PR outside the range belongs to
the next release, even if it has a lower PR number or is user-visible.

Prepare two ordered changes, both based on `main`:

1. a child PR in `nowledge-labs-website` with the `date: "unreleased"` entry;
2. a parent PR in `nowledge-co/mem` that advances the website gitlink and adds
   matching engineering history in `nowledge-graph/CHANGELOG.md`.

### Website ownership and Vercel deployment

`nowledge-co/nowledge-labs-website` is the canonical review repository. Every
Changelog change must be submitted as a PR there and merged there first. Do
not use a PR against `wey-gu/nowledge-labs-website` as a substitute for that
review or merge.

`wey-gu/nowledge-labs-website` is the Vercel deployment carrier. After the
canonical merge, synchronize its `main` branch from the current deployment
head by merging canonical `main`, then push that merge to `wey-gu/main` to
start the manual deployment. Record both SHAs:

```text
CANONICAL_WEBSITE_SHA=<nowledge-co/main>
DEPLOY_WEBSITE_SHA=<wey-gu/main after the sync merge>
```

Never force-push the deployment branch. If the two histories have diverged,
the synchronization commit must retain the current `wey-gu/main` as one parent
and canonical `main` as the other; its tree should match the canonical source
unless the deployment repository has an explicitly reviewed deployment-only
change. Verify the Vercel deployment and cache-bust the API readback. Confirm
the entry version, `unreleased` state, item count, and representative text
before creating any package tag. Once the parent PR merges, record its exact
merge commit as `RELEASE_MAIN_METADATA_SHA`.

## 4. Copy the reviewed release metadata to the release branch

Cherry-pick the two reviewed `main` commits onto the branch in dependency
order. Always use `-x`; the appended source-commit trailer is release
provenance and must not be removed.

```bash
git fetch origin main "release/<version>"
git switch "release/<version>"
git cherry-pick -x "$RELEASE_MAIN_VERSION_SHA"
git cherry-pick -x "$RELEASE_MAIN_METADATA_SHA"
git push origin "release/<version>"
export RELEASE_SHA="$(git rev-parse HEAD)"
```

Before compiling, verify that the copied commits contain only the intended
version and release metadata, and that each `-x` source SHA is reachable from
`origin/main`. Do not merge `main` into the branch to resolve a conflict. If a
cherry-pick conflicts, resolve only the release metadata conflict, preserve the
`-x` trailer, and record the resolution in the release record.

Run source preflights and build the native bundles from exactly `RELEASE_SHA`.
`ci/mem-native-link-arm64` is started by the normal merge path; release work
must only observe that merge-triggered job and must never dispatch or retry it
solely for a release. A completed failure blocks the release. A successful or
still-running job does not require a separate release-triggered run. The
native-link gate belongs in the pre-release artifact smoke if it requires a
built bundle.

If a check fails:

- fix only release infrastructure or a confirmed release blocker through a
  reviewed `main` commit, then cherry-pick it with `-x` onto the release
  branch;
- re-run the affected checks against the new release-branch head;
- do not import unrelated `main` changes;
- use `rc2`, `rc3`, and so on after any already-published RC tag.

## 5. Cut and smoke the RC

Create coordinated annotated `v<version>-rcN` tags from `RELEASE_SHA` in each
source repository required by the release workflow, then trigger the official
RC workflows. Verify tag-to-SHA readback before treating the workflow as a
candidate build.

The RC must prove the actual published artifacts: install the desktop package,
verify the embedded native binary and updater path, run the required
native-link/artifact smoke, and verify Docker and CLI artifacts where they are
in scope. RC publication must not move `latest` or the GA updater feed.

The desktop RC uploads its platform artifacts independently. A slow RPM build
may complete after the macOS, Windows, DEB, and AppImage artifacts; its result
must still pass the Linux payload and ABI-floor checks before it can be
promoted. Do not make a failed RPM invisible by treating the RC as complete.

## 6. Promote the tested RC to GA

### 6.1 Promote the core release

Once the core RC artifacts have passed their smoke gates, run
`promote-rc-to-ga.yml`. It promotes Docker, macOS, Windows, DEB, and AppImage
artifacts without requiring the RPM to exist. It is the only promotion that
updates APT. The production updater currently discovers the latest desktop
version from R2 objects; uploading a newer GA can expose it before this
workflow finishes. `push_latest` controls Docker `:latest` only. Finish the
core artifact smoke before dispatch, and treat any failure after R2 upload as
a partial public release. The release-owned exact-delivery gate must confirm
each core platform, direct GA download, updater target, and APT version before
the draft is created; the separate Backbone probe still checks headers and
Range support. Issue #67 owns the explicit all-artifact latest gate.

```bash
gh workflow run promote-rc-to-ga.yml -R wey-gu/mem-releases \
  -f rc_tag=<version>-rcN \
  -f ga_tag=<version> \
  -f push_latest=true
```

Record the workflow run, the resulting GA release draft, the R2/CDN readback,
and the updater readback. A core release is not evidence that a deferred RPM
has been delivered.

If Docker, R2, and APT succeeded but the GA draft is missing, use
`finish-ga-release.yml` only after checking the exact partial state. It
verifies the core platforms and APT before creating the draft; it does not
deploy the Backbone Worker or promote the separately staged RPM. Do not
repeat the full promote merely to create the missing draft.
If any core R2 object or the APT candidate is missing or wrong, withhold that
GA: `finish-ga-release` will reject it, and normal promote refuses the occupied
R2 keys. Record the exact keys, the updater response, and the failed run;
prepare a higher-version RC from the corrected source, smoke its published
artifacts, then promote that version. Do not reuse the incomplete GA tag or
publish its draft. A release operator must decide how to clean up the
incomplete version's R2 keys and CDN cache after the replacement is verified.

For an older out-of-band GA, desktop R2 discovery may correctly remain on a
newer version. Only in that case set `allow_newer_desktop_latest=true`; a
read-only preflight rejects this mode unless every selected platform's live
latest is the same newer version.
The release still verifies the promoted version's direct downloads, while
every selected desktop latest/update route must agree on one newer version.
This mode skips the APT update so it cannot downgrade the stable repository;
verify the existing APT candidate separately. `push_latest` controls Docker
only and must also be set to `false` if Docker latest should stay newer.

### 6.2 Promote a deferred RPM

After the RC draft contains its single validated RPM, run
`promote-rpm-to-ga.yml` with the same RC and GA versions:

```bash
gh workflow run promote-rpm-to-ga.yml -R wey-gu/mem-releases \
  -f rc_tag=<version>-rcN \
  -f ga_tag=<version>
```

The workflow refuses a mismatched version pair, a missing GA release, or an RC
release without exactly one RPM. It verifies the downloaded RPM against the RC
Release SHA-256. Package names that already contain the GA base version (for
example `Nowledge.Mem-0.10.94-1.x86_64.rpm` on an RC2 Release) are retained;
names with the RC suffix are renamed to GA. Before any public write it rejects
a different same-name GA asset. The release-owned R2 step uploads only a
missing key, skips identical bytes, and refuses different bytes; CDN delivery
is downloaded and checked by SHA-256. The final Release attachment likewise
skips an identical RPM and never clobbers an existing one. It may add a missing
RPM to an already published GA, as this channel is explicitly deferred. It
does not rebuild the RPM, update APT, or change the core auto-updater feed.

Publish the GitHub Release only after the intended asset set is present. If the
core release is announced while RPM remains pending, state that RPM availability
is pending and send a completion update after its promotion verifies.

## 7. Close the history loop

The version and unreleased-note metadata are already merged on `main`; do not
merge the release branch back. After GA, date the Changelog entry, deploy it,
and merge its parent gitlink update to `main`. Run the release-history check
to prove the tagged release branch is patch-equivalent to its recorded `main`
source commits and that every copied commit has a valid `-x` provenance trailer.
An ancestor-only check is invalid for this workflow because cherry-pick creates
new commit IDs by design.

## Fast-path rule

Skipping RC is reserved for an isolated, low-risk change with a previously
validated release pipeline. A versioned App release, a changed release
workflow, a renamed private dependency, or any native bundle change always
uses the RC path.

If an approved fast path uses `release-desktop.yml` directly with a clean
semver tag, its publish job requires and uploads all six desktop artifacts,
including RPM, then requires APT signing credentials, updates APT, and verifies the R2 download and updater
routes before creating a draft GitHub Release. The old `latest` input is kept
only so existing dispatch commands still parse; it no longer gates desktop
latest. Legacy update endpoints discover the newest GA objects in R2 as they
arrive. The release-owned exact-delivery gate checks legacy delivery on all six
platforms and APT. Native controlled update acceptance is a separate required
step described above. The explicit older-patch mode skips the APT update and
exact APT check so an older version cannot replace its stable candidate. A
failure after upload is a partial public release even if the draft is absent.
Do not deploy the Backbone Worker merely to set a release version. Normal GA
preflight fails before public mutation if `GPG_PRIVATE_KEY` is absent.
While RPM promotion is deferred, direct GA's all-platform older-patch preflight
also fails closed because the live RPM latest can differ from the six core
platforms. Use the RC promotion flow for an approved older out-of-band patch.
For clean GA tags, `publish=false, build_vulkan=true` is build-only: the
Vulkan bundles remain Actions artifacts and do not create a GH Release draft.
When `publish=true`, optional Vulkan assets attach only while the Release is
draft and reconcile partial attachments by SHA-256. Core GA attachment uses
the same rule:
identical existing bytes are skipped, missing files are uploaded, and
different bytes stop the run. Inspect a partial draft and its assets before
any retry. Normal RC promotion and direct GA require their target R2 keys and
GA Release tag to be unused before any parallel publication job starts. If a
direct GA partially wrote R2, withhold its draft and cut a higher-version RC
after inspection; `finish-ga-release` is for a validated RC promotion, not a
direct rebuild.
