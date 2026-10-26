# Nowledge Mem Release SOP

This is the standard path for a desktop or multi-surface App release. Its
purpose is to make the release candidate a fixed, reviewable source range
instead of a moving view of `main`.

## Invariants

- Select one already-merged main commit as `RELEASE_BASE_SHA = RELEASE_SHA`.
- Freeze that SHA on a release branch; later main merges are excluded.
- A required version-only PR merges independently of website notes. If its
  version is already present, no release-only Mem PR is needed.
- OpenAPI is authored/generated in Mem and consumed by the canonical website
  at a fixed SHA. Product CI and builds need no website checkout or writeback.
- Notes, translations and publication dates belong to the website. Engineering
  worksheets and source/CI/website/artifact receipts belong to mem-releases.
- Both canonical E2E gates must pass for RELEASE_SHA before any RC/GA tag.
- Public Changelog deployment/readback precedes package distribution.
- Promote accepted RC artifact bytes without rebuilding; preserve all Desktop
  authority, recovery and delivery-readback gates.

Existing active candidates keep their original fixed base and reviewed `-x`
provenance. This procedure changes new cuts only; never rewrite historic tags.

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

## 1. Merge the required version change

If necessary, open one version-only PR to `nowledge-co/mem:main`, containing
all required version fields and generated manifests/locks. Label it
`severity/critical` for release priority and merge through normal bot gates.
It does not wait for website notes and contains no website gitlink or
release-only engineering prose. Skip this PR if the intended version is
already present in the selected merged source.

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

## 2. Select and freeze the merged source

Select the exact intended main commit after the version is available. Record
it as both RELEASE_BASE_SHA and RELEASE_SHA; a release branch only names it.

```bash
git fetch origin main
export RELEASE_SHA=<full-selected-main-sha>
git merge-base --is-ancestor "$RELEASE_SHA" origin/main
export RELEASE_BASE_SHA="$RELEASE_SHA"
git switch -c "release/<version>" "$RELEASE_SHA"
git push -u origin "release/<version>"
```

Audit the complete `v<previous-published-version>..RELEASE_SHA` product range.
Later main changes are excluded. Do not cherry-pick release metadata to create
a second source identity, merge moving main, or merge the release branch back.
A necessary repair first passes normal main review, is replayed with `-x`,
receives a new RC and is requalified against its new exact SHA. Legacy replay
candidates keep their original source and patch-equivalence proof.

## 3. Prepare website notes independently

Write and review notes in `nowledge-co/nowledge-labs-website` from the frozen
range and product fragments. Store the engineering worksheet in
`release-records/<version>/`; do not compact CHANGELOG.md or advance the Mem
website gitlink as release bookkeeping.

Mem's canonical OpenAPI is
`nmem-rs/crates/nmem-server/src/assets/openapi.json`. API changes update it in
the product PR. In a canonical website checkout, consume the selected commit:

```bash
node scripts/sync-mem-openapi.mjs --repo /path/to/mem --ref <full-release-sha>
pnpm --dir nowledge-mem run generate:docs
node scripts/sync-mem-openapi.mjs --check
```

The sync command reads the exact merged Git blob, ignores working-tree edits,
records its SHA and SHA-256, and writes only website files. The website never
compiles Rust or modifies Mem. Run website localization and build CI. An
unchanged API may keep an older source receipt after comparing its digest to
the selected release asset; record that comparison with the release evidence.

Merge website notes in the canonical repository. Ask the website maintainer
to synchronize the deployment mirror through its supported deploy path. Wait
for successful deployment, then publicly read back the release entry, version,
unreleased state and representative notes before package distribution. Record
canonical/deployment SHAs and the OpenAPI digest in mem-releases. Source
qualification and candidate builds can proceed while website work is pending.

## 4. Qualify the exact selected source

Run source preflight against RELEASE_SHA. Require terminal success for both
`ci/mem-frontend-e2e` and `ci/mem-shell-e2e` on that SHA before any RC/GA tag or
package distribution. Record source SHA and context/run URLs. Failed, missing,
pending, cancelled, skipped, superseded and different-SHA results block tags.
A main postsubmit is reusable only because the candidate retains that exact
merged SHA. Arrange controlled exact-SHA CI if normal CI did not test it.

`ci/mem-native-link-arm64` is independent post-merge CI. Release work does not
query or inspect its status/logs, wait for it, trigger it, or gate on its outcome.
Keep native artifact smoke and packaged runtime-version evidence. A repair
changes RELEASE_SHA and requires all affected qualification again. No previous release's pass,
source manifest check or manual inspection substitutes for a required gate.

## 5. Cut and smoke the RC

Create coordinated annotated `v<version>-rcN` tags from `RELEASE_SHA` in each
source repository required by the release workflow, then trigger the official
RC workflows. Verify tag-to-SHA readback before treating the workflow as a
candidate build.

The RC must prove the actual published artifacts: install the desktop package,
verify the embedded native binary and updater path, run the required
packaged native artifact smoke, and verify Docker and CLI artifacts where
they are in scope. RC publication must not move `latest` or the GA updater feed.

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

For an immutable direct-GA build whose only failed job is `publish`, the
version E2E can still qualify its original artifacts when the sole failed
publisher step is draft GitHub asset attachment. It requires a complete
same-run, same-attempt job inventory: every required build must have succeeded,
as must R2 upload, APT publication and exact delivery verification. The identity
artifact retains that evidence. This permits testing and reconciling already
built bytes; it does not mark the failed publisher successful, admit a failed
build, waive native runtime tests, or permit a rebuild under an existing tag.
Reconcile all six draft assets by SHA-256 and finish delivery independently
before publishing the GitHub GA.

This gate prevents the failure seen in Desktop 0.10.94
(`nowledge-co/mem#5897`): a Bazel target omitted the Cargo package version, the
bundled server reported `0.0.0`, and Desktop correctly refused to start it.
The source-level repair in `nowledge-co/mem#5898` does not replace this
post-build proof.

## 6. Promote the tested RC to GA

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

### Withdraw a Desktop version without blocking website downloads

First withdraw the affected GitHub GA release to a draft, retain its artifacts,
and stop the production Desktop authority through its audited named-operator
workflow. Then install the download fallback with a fresh operation UUID:

```bash
gh workflow run install-desktop-upgrade-pause.yml -R wey-gu/mem-releases \
  -f ga_tag=<withdrawn-version> \
  -f operation_id=<uuid-v4>
```

The workflow reads all GitHub release pages and selects the highest numeric
SemVer below the withdrawn version that is public, not a prerelease, and has
all six Desktop packages. Drafts represent withdrawn versions and cannot be
fallbacks. Every selected CDN package must return HTTP 200 to a HEAD request
with a size matching its Release asset. Missing objects or sizes skip that
version; authentication, service, transport, or incomplete-source failures stop
before deployment. No usable older GA also stops before deployment. This proves
package availability, not native startup or installation compatibility.

The selected version is fixed in the generated Worker configuration for that
pause. Website latest/download routes return its metadata or redirect to its
platform-specific package, while controlled updates stay stopped, the legacy
updater returns no update, and exact downloads of the withdrawn version stay
blocked. The checked-in Wrangler file is a template: use the workflow-generated
configuration, never deploy its version placeholders directly. This is one
active global pause; if the fallback itself is later withdrawn, rerun this
workflow with the same paused version and a fresh UUID to select the next usable
GA. Selection is not performed on every user download.

Retain the workflow's pause-plan artifact: it records the paused version,
fallback version, GitHub release ID, publication time, and six checked package
URLs/sizes. Use its fallback version as `previous_version` when opening APT and as
`paused_stable_version` during pause-aware package promotion. On Desktop unpause,
pass it as `fallback_version`; keep `previous_version` bound to the stable
version in the authority publish receipt. Pass the version blocked by the exact
pause routes as `paused_version` when opening APT or removing Desktop routes.
After the seven-day artifact retention, read the active pause Worker's
`FALLBACK_VERSION` binding and deployment history; do not infer the fallback from
GitHub latest or the authority stable baseline. The six-platform post-deploy
readback checks exact filenames and requests a one-byte Range GET against each
planned size, in addition to metadata, redirects and stopped control. If the CDN
ignores Range with HTTP 200, require the full planned Content-Length, read one
stream chunk, then cancel instead of downloading the installer. It must pass
before declaring fallback delivery verified. This is a bounded availability
check, not a full download, signature or native install check. Merging these
scripts does not install or enable a production pause.

Pause installation and authority mutations currently use separate workflow
concurrency groups. The stopped check and post-deploy readback do not form an
atomic cross-workflow transaction. Operators must avoid concurrent authority
changes while installing pause routes; existing coordination work is tracked
in https://github.com/wey-gu/mem-releases/issues/141.

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
  -f paused_version=<withdrawn-version> \
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
  -f paused_version=<withdrawn-version> \
  -f fallback_version=<version-from-pause-plan> \
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

When `ga_tag` equals `paused_version`, the exact public package routes must
still return 503 with the pause identity and `no-store` before route removal.
The pre-removal gate checks both Backbone and CDN paths on all six platforms;
package identity is qualified by the unchanged Release/R2 manifest comparison.
For a different staged candidate, the gate still requires readable exact-version
downloads. After removal, both paths require the full normal public delivery
readback. This does not change the authority handoff: the workflow still requires
a `publish` receipt, not a recover-only receipt.

Once that exposure workflow succeeds, accept the published candidate as the
new stable recovery baseline. Use the published authority revision returned by
the publish receipt, the stable version that was retained during rollout, and a
fresh persisted UUID:

```bash
gh workflow run desktop-update-control.yml -R wey-gu/mem-releases \
  -f action=complete \
  -f operator_name=<named-operator> \
  -f expected_mode=published \
  -f expected_revision=<published-revision> \
  -f expected_stable_version=<previous-version> \
  -f operation_id=<uuid-v4> \
  -f reason='<audited-acceptance-reason>' \
  -f ga_tag=<version>
```

The completion operation re-verifies exact GA delivery and six-platform
protocol-1 delivery before advancing the candidate. It moves the former stable
release to `previous`, clears `candidate`, and leaves the authority in
`completed` mode so the next release can be prepared without overwriting an
unaccepted rollout.

Record the workflow run, the resulting GA release draft, the R2/CDN readback,
and the updater readback. A core release is not evidence that a deferred RPM
has been delivered.

If Docker, R2, and APT succeeded but the GA draft is missing, use
`finish-ga-release.yml` only after checking the exact partial state. It
verifies the core platforms and APT before creating the draft; it does not
deploy the Backbone Worker or promote the separately staged RPM. Do not
repeat the full promote merely to create the missing draft.
For a pause-aware promotion that stopped after the exact R2 uploads but before
the draft was created, use the same recovery with
`desktop_upgrade_pause_active=true` and the reviewed
`paused_stable_version`. It re-verifies the active pause and every exact GA
download, leaves APT deferred, and creates the draft from the original RC
assets without rewriting R2.
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

## 7. Finalize only the website publication date

After publishing the stable GitHub Release, run this from mem-releases using
the standalone canonical website checkout:

```bash
node scripts/date-ga-changelog.mjs <x.y.z> /path/to/website
```

The command dates only `nowledge-mem/data/changelog.ts` using actual
`published_at` in UTC. Draft/RC, missing or conflicting entries fail before
writing; repeat runs leave the correct date unchanged. It does not read or
write Mem's engineering CHANGELOG. Review/merge the website-only change,
request mirror synchronization, verify deployment and read back both endpoints:

```bash
curl -fsS https://mem.nowledge.co/api/changelog/release-notes | jq '{version,date}'
curl -fsS 'https://mem.nowledge.co/api/changelog/release-notes?version=<x.y.z>' | jq '{version,date}'
```

Keep engineering notes, source/CI/website/artifact receipts and final acceptance
in mem-releases. No post-GA Mem commit, website gitlink advance or branch-back
merge is required. For a new cut, the tag is the selected main commit; legacy
and repair replays retain the release-history patch-equivalence and `-x` proof.

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
