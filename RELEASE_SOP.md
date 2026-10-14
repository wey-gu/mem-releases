# Nowledge Mem Release SOP

This is the standard path for a desktop or multi-surface App release. Its
purpose is to make the release candidate a fixed, reviewable source range
instead of a moving view of `main`.

## Invariants

- A release has one immutable source boundary: `RELEASE_BASE_SHA`.
- `main` may continue to accept ordinary work after the boundary is cut.
- Version fields and parent release metadata are reviewed together in one
  release-scoped PR to `main`, then copied to the release branch with one
  `git cherry-pick -x`.
- No ordinary product change is cherry-picked into the release branch.
- Every release note, binary, tag, and smoke result names the same release
  branch commit.
- Both canonical E2E gates have terminal success against `RELEASE_SHA` before
  any RC or GA tag is created or package distribution begins.
- The public Changelog is deployed and read back before package distribution.
- Core RC artifacts are promoted to GA without rebuilding. A delayed RPM is
  promoted separately from its validated RC asset and never changes the APT
  repository or auto-updater feed.

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

## 2. Start one release metadata PR with a version-only commit

Create one release-scoped PR with base `main`. Its first commit contains only
version fields and generated manifests or lockfiles required by the version
change. It must not contain user-facing prose or a website gitlink update.
Keep the PR open until the website child change is reviewed and merged, then
complete the same PR as described below.

Label the PR `severity/critical` while the release is active so Tide gives the
fixed release path higher merge priority. The label does not relax review, CI,
or bot-only merge gates, and it must not be used for unrelated product work.

### Version ownership and cache boundary

The App release version is a distribution identity, not automatically the
version of every internal library. Maintain one source-owned canonical release
version and generate the Desktop, CLI, updater, mobile, lockfile, and build
metadata that must carry it. Keep internal Rust libraries on independent
versions unless their public compatibility or distribution contract changes.

This boundary matters for build reuse. Changing one authored version file does
not preserve caches when that value still fans out into `CARGO_PKG_VERSION`,
Cargo lock entries, or generated Bazel target versions for every crate. The
long-term product change must limit the release version to shipped leaf
binaries and package metadata, while keeping generated consistency checks for
all runtime-visible versions.

Do not partially apply that target architecture during an active release.
Until the product repository owns a canonical generator and separates internal
crate versions, update every field required by the current release preflight.
Make the ownership refactor in a separate reviewed product PR. Its acceptance
evidence must show both that internal-only package versions remain stable
across an App release and that every shipped runtime surface reports the new
App version.

## 3. Write, merge, deploy, and verify release notes on `main`

Build the changelog worksheet from the exact range:

```text
v<previous-version>..RELEASE_BASE_SHA
```

Classify only changes in that range. A merged PR outside the range belongs to
the next release, even if it has a lower PR number or is user-visible.

Prepare two ordered changes:

1. a child PR in `nowledge-labs-website` with the `date: "unreleased"` entry;
2. after the child PR merges, a second commit in the existing
   `nowledge-co/mem` release PR that advances the website gitlink and adds
   matching engineering history in `nowledge-graph/CHANGELOG.md`.

Keep the two commits separate while the PR is under review so version-only and
parent-metadata scope remain easy to audit. The final PR head must contain no
ordinary product code. Tide may squash the PR; the resulting single merged
`main` commit is the reviewed release-metadata provenance copied to the fixed
release branch.

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
before creating any package tag. Once the combined Mem PR merges, record its
exact merge commit as `RELEASE_MAIN_METADATA_SHA`.

## 4. Copy the reviewed release metadata to the release branch

Cherry-pick the combined reviewed `main` commit onto the branch. Always use
`-x`; the appended source-commit trailer is release provenance and must not be
removed.

```bash
git fetch origin main "release/<version>"
git switch "release/<version>"
git cherry-pick -x "$RELEASE_MAIN_METADATA_SHA"
git push origin "release/<version>"
export RELEASE_SHA="$(git rev-parse HEAD)"
```

Before compiling, verify that the copied commit contains only the intended
version and release metadata, and that its `-x` source SHA is reachable from
`origin/main`. Do not merge `main` into the branch to resolve a conflict. If
the cherry-pick conflicts, resolve only the release metadata conflict, preserve
the `-x` trailer, and record the resolution in the release record.

Run source preflights and build the native bundles from exactly `RELEASE_SHA`.
`ci/mem-native-link-arm64` is started by the normal merge path; release work
must only observe that merge-triggered job and must never dispatch or retry it
solely for a release. A completed failure blocks the release. A successful or
still-running job does not require a separate release-triggered run. The
native-link gate belongs in the pre-release artifact smoke if it requires a
built bundle.

### Mandatory exact-SHA E2E gate

Before creating any RC or GA tag or starting package distribution, require
terminal success for both canonical E2E contexts against the exact
`RELEASE_SHA`:

- `ci/mem-frontend-e2e`, covering `//nowledge-graph:frontend_web_e2e_test` and
  `//nowledge-graph:web_contract_tests`;
- `ci/mem-shell-e2e`, covering `//nowledge-graph:frontend_shell_e2e_test`.

Record the tested commit and the run or context URL for both gates in the
release record. Only terminal success counts. A failed, missing, pending,
cancelled, skipped, or superseded run blocks the release, as does a successful
run for any other commit.

The standard Prow jobs may run as postsubmits of a `main` merge. Such a run is
not evidence for the cherry-picked release-branch commit unless the job itself
reports `RELEASE_SHA` as the tested source. If the current CI path cannot test
the exact release commit, stop before tags and arrange controlled exact-SHA CI.
Do not replace this gate with a previous release's pass, an unchanged-frontend
claim, source preflight, artifact smoke, or manual inspection.

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

The desktop RC uploads its platform artifacts independently. The RPM is
bundled in the Linux DEB/AppImage job from the same backend and shell binaries,
after the DEB/AppImage artifact is saved; an RPM failure does not block those
core Linux artifacts. The RPM must still pass the Linux payload and ABI-floor
checks before it can be promoted. A failed RPM leaves no `linux-rpm` artifact
and fails `prerelease-upload-linux-rpm`; do not make it invisible by treating
the RC as complete.

### Mandatory post-build version E2E

Run a version E2E after compilation against the exact RC artifacts, identified
by their recorded SHA-256 values. Install or extract each shipped packaging
path and execute the packaged components. At minimum, compare the expected
release version with all applicable observations:

- the installed Desktop application and package identity;
- the embedded `nmem-server --version` output and its live health-details
  version after Desktop startup;
- the installed `nmem --version` output;
- the updater metadata and target selected for the candidate.

The check must explicitly reject an empty version, `0`, `0.0.0`, and known
build-system placeholders even if two components happen to return the same bad
value. It must fail nonzero if installation, process startup, readiness,
version probing, JSON parsing, or cleanup fails. Use strict shell error
handling and assert readiness before parsing a response; diagnostic output
after a failed command must never turn the job green.

This is an artifact E2E, not a source or archive-shape check. If the release
operator cannot execute a platform locally, obtain the same evidence from a
native release runner before promotion. Record the artifact hash, platform,
commands, observed versions, and terminal result in the release record.

This gate prevents the failure seen in Desktop 0.10.94
(`nowledge-co/mem#5897`): a Bazel target omitted the Cargo package version, the
bundled server reported `0.0.0`, and Desktop correctly refused to start it.
The source-level repair in `nowledge-co/mem#5898` does not replace this
post-build proof.

## 6. Promote the tested RC to GA

### 6.0 One dispatch (standard path)

`promote-rc-to-ga.yml` runs the whole promotion as one resumable dispatch:

```bash
gh workflow run promote-rc-to-ga.yml -R wey-gu/mem-releases \
  -f rc_tag=<version>-rcN -f ga_tag=<version>
```

A read-only plan observes the RC/GA Releases, the six R2 objects by
SHA-256, APT, the upgrade-pause routes, the Desktop update authority, and the
RC-recorded source tags, and checks every credential before any mutation.
Each stage then either runs, or proves its exact expected result is already
live and skips: Docker and GPU re-tags, Desktop packages plus RPM and APT,
authority status/recover/prepare, an automated verify-before-expose check,
authority publish, the GA Release (draft=false, then a separate make_latest
PATCH and a `releases/latest` readback), source tags with
`cli-publish-v<version>` last, the mobile dispatch, and a final read-only
verification. Anything that is neither absent nor exactly equal (different
R2 bytes, a tag at another commit, a Docker tag at another digest, an
authority holding another candidate) stops the plan before any mutation.

There is no human confirmation inside the run. The authority operator is the
dispatching GitHub user (`github.actor`), so that login must be a named
operator in the production Secrets Store document; the preflight status read
fails before any mutation otherwise. The updater publishes only after the
automated check proves that the prepared candidate names the RC-authenticated
bytes and source commit, the exact GA downloads and APT already serve, and the
Docker promotion verified the RC signatures. Desktop clients on the legacy,
R2-discovered `/latest` still see a newer GA as soon as its objects are
uploaded (issue #67); protocol-1 Check for Updates and GitHub `latest` move
only after verification.

The upgrade pause is emergency-only. The default path never opens or removes
pause routes; `desktop_upgrade_pause_active=true` (with the live routes in
place) stages behind the pause, opens the APT route, publishes APT, and removes
the Desktop pause routes only after the authority and GA Release are public.

To resume after a failure, dispatch again with the same inputs. `mobile=true`
(default) dispatches `release-mobile.yml` with publish and distribute on; opt
out explicitly and record why. Source tags and PyPI use a per-run GitHub App
token (`RELEASE_APP_ID`, `RELEASE_APP_PRIVATE_KEY`; App permission Contents:
read & write, installed on `nowledge-co/mem` and `nowledge-co/community`);
without those secrets the tag stage and mobile are skipped with a warning and
the summary lists the exact tags to create by hand. The authority must not
still serve an unaccepted earlier candidate; accept it outside release
automation first. The Changelog date flip remains a human step behind the
production website gate: the summary prints the GA `publishedAt` date to use.

The sections below describe each stage and its standalone recovery workflow.

### 6.1 Promote the core release

Once the core RC artifacts have passed their smoke gates, run
`promote-rc-to-ga.yml`. It promotes Docker, macOS, Windows, DEB, and AppImage
artifacts without requiring the RPM to exist. It is the only promotion that
normally updates APT. The production updater currently discovers the latest desktop
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

If an independently deployed emergency Worker is intentionally pausing Desktop
latest, update, and APT routes, keep it in place while staging the GA. First
verify the pause and protocol-1 stopped state, then dispatch the core promotion
with `desktop_upgrade_pause_active=true`. This mode still authenticates and
uploads the exact RC artifacts, verifies every versioned GA download, and
creates the draft GA Release, but it does not query mutable latest/update routes
or publish APT:

```bash
gh workflow run promote-rc-to-ga.yml -R wey-gu/mem-releases \
  -f rc_tag=<version>-rcN \
  -f ga_tag=<version> \
  -f push_latest=true \
  -f desktop_upgrade_pause_active=true \
  -f paused_stable_version=<previous-version>
```

After core and RPM staging succeeds, use the audited route workflow to remove
only the pause Worker's APT route. Keep Desktop latest, update, and
latest-download routes paused. The workflow requires a fresh UUID, verifies all
six draft assets and the complete pause boundary, deletes only the exact APT
route, retains the Worker deployment, and confirms that the old APT repository
is readable:

```bash
gh workflow run open-apt-pause-route.yml -R wey-gu/mem-releases \
  -f ga_tag=<version> \
  -f previous_version=<previous-version> \
  -f operation_id=<uuid-v4>
```

Then publish the authenticated DEB from the draft GA Release:

```bash
gh workflow run promote-apt-to-ga.yml -R wey-gu/mem-releases \
  -f ga_tag=<version> \
  -f paused_stable_version=<previous-version>
```

The APT workflow fails before mutation unless the GA Release is still a draft,
contains exactly one DEB, the Desktop pause remains fail-closed, the APT route
is open, the live APT version is not newer, and signing credentials are present.
Do not remove the remaining pause routes until core, RPM, APT, and the controlled
updater candidate have all passed their exact-version checks. Route removal is
the final exposure step; retain the pause Worker deployment as the recovery
baseline and perform the full six-platform legacy and protocol-1 readback
immediately afterward.

Use `desktop-update-control.yml` for the production authority. It deploys a
short-lived gateway bound to the canonical Secrets Store document, derives the
actor from the selected named credential, and deletes the gateway after one
operation. The CI token for this workflow must be stored as
`CLOUDFLARE_SECRETS_STORE_API_TOKEN` and have Workers Scripts Write plus Account
Secrets Store Edit; the ordinary release token is not a substitute. Run
`status` first. If the authority is stopped, recover the observed stable
revision before preparing the candidate. Run recover, prepare, and publish as
separate dispatches with the explicitly observed mode/revision and a distinct
persisted UUID for each mutation. Never infer the next input from an expected
increment.

Each successful mutation uploads an authority receipt. After the candidate is
published in the authority and the GitHub GA Release is public, remove the five
Desktop pause routes with the exact publish receipt and a fresh route UUID:

```bash
gh workflow run remove-desktop-upgrade-pause.yml -R wey-gu/mem-releases \
  -f ga_tag=<version> \
  -f previous_version=<previous-version> \
  -f operator_name=<named-operator> \
  -f authority_publish_run_id=<run-id> \
  -f authority_publish_operation_id=<uuid-v4> \
  -f authority_revision=<published-revision> \
  -f route_operation_id=<uuid-v4>
```

The workflow rebuilds and compares the six-platform manifest, verifies the
published GA, APT, direct downloads, and active pause, deletes only the reviewed
Desktop routes, retains the pause Worker deployment, and requires exact legacy
plus protocol-1 `allowed` readback before success.

Record the workflow run, the resulting GA release draft, the R2/CDN readback,
and the updater readback. A core release is not evidence that a deferred RPM
has been delivered.

If a promotion stopped part-way, dispatch `promote-rc-to-ga.yml` again with
the same inputs: it reconciles the partial state and runs only the missing
stages. `finish-ga-release.yml` remains a manual recovery tool when Docker,
R2, and APT succeeded but the GA draft is missing. It verifies the core
platforms and APT before creating the draft; it does not deploy the Backbone
Worker or promote the separately staged RPM.
For a pause-aware promotion that stopped after the exact R2 uploads but before
the draft was created, use the same recovery with
`desktop_upgrade_pause_active=true` and the reviewed
`paused_stable_version`. It re-verifies the active pause and every exact GA
download, leaves APT deferred, and creates the draft from the original RC
assets without rewriting R2.
If any core R2 object or the APT candidate is wrong, withhold that GA:
`finish-ga-release` will reject it, and the promotion plan refuses an R2 key
whose bytes differ from the RC asset. Record the exact keys, the updater response, and the failed run;
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
any retry. Direct GA requires its target R2 keys and GA Release tag to be
unused before any parallel publication job starts; RC promotion accepts only
keys and assets that already hold the exact RC bytes. If a
direct GA partially wrote R2, withhold its draft and cut a higher-version RC
after inspection; `finish-ga-release` is for a validated RC promotion, not a
direct rebuild.
