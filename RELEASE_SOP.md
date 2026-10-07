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

## 0. Verify the release capability before the cut

Before creating a branch, verify that the release workflows can read every
private source dependency and that signing, upload, and registry secrets are
available. Run the standalone Rust bundle workflow against a known-good source
ref if the private dependency contract or workflow changed recently.

For renamed private dependencies, preserve the existing deploy-key identity.
For example, HawDB is the renamed Skein dependency and must be cloned with
`SKEIN_REPO_SSH_KEY` over SSH, not with a token scoped only to the parent
source repository.

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

Record the workflow run, the resulting GA Release and finalization state, the R2/CDN readback,
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
merge the release branch back. Direct GA, RC desktop promotion, and
`finish-ga-release` call `scripts/finalize-ga-release.mjs publish` only after
their existing delivery verification and draft-asset reconciliation succeed.
The common finalizer publishes the draft, reads its actual `published_at`, and
uses its UTC calendar date. It verifies both the explicit version and default
website APIs before a separate `make_latest` PATCH and GitHub latest readback.

The website resolves each clean, public, non-prerelease GA from its own GitHub
Release timestamp. Author new notes as `unreleased`; no per-GA website date PR,
deployment, or parent website gitlink update is needed. Keep the pre-distribution
notes deployment and production staging/GO gates. Before adopting this flow,
deploy the resolver through the approved canonical/carrier path and verify the
`X-Changelog-Publication-Source: github-releases` response header. Distribution
preflight rejects an old deployment or unavailable release lookup.

The finalizer also requires the exact engineering date on `nowledge-co/mem`
`main`. `MEM_REPO_TOKEN` remains read-only. An optional separately scoped
`MEM_METADATA_TOKEN` (Contents and Pull requests write; Issues write for labels)
can prepare a date-only PR, reuse an existing correction, and request `wey-gu`
and `hawkingrei`. It never merges. Without this token, merge the engineering
correction through the normal bot/review process. The run stays incomplete
until `main` has the correct date; it reports the pending PR or missing token.

If publication succeeded but website cache or engineering archival is pending,
rerun only this published-release recovery workflow:

```bash
gh workflow run finalize-ga-release.yml -R wey-gu/mem-releases \
  -f ga_tag=<version>
```

It refuses drafts and performs no build, upload, tag move, or website deployment.
All desktop publication/finalization jobs share a non-cancelling concurrency
group. A newer GA prevents an older task from moving latest backward; explicit
older-release mode preserves the newer latest. Website readback retries are
bounded to six minutes. A failed finalizer means a partial public release,
not permission to upload the same artifacts again. The `release-publish`
environment does not prove approval unless its protection rules enforce it.

Run the release-history check
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
latest. The production updater discovers the newest GA objects in R2 as they
arrive. The release-owned exact-delivery gate checks all six platforms and
the updater and APT. The explicit older-patch mode skips the APT update and
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
