# Nowledge Mem Release SOP

This is the standard path for a desktop or multi-surface App release. Its
purpose is to make the release candidate a fixed, reviewable source range
instead of a moving view of `main`.

## Invariants

- A release has one immutable source boundary: `RELEASE_BASE_SHA`.
- `main` may continue to accept ordinary work after the boundary is cut.
- No ordinary product change is cherry-picked into the release branch.
- Every release note, binary, tag, and smoke result names the same release
  branch commit.
- The public Changelog is deployed and read back before package distribution.
- An RC is promoted to GA without rebuilding.

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
to merge while release work is in progress. A necessary release-only repair is
allowed, but it must be reviewed, recorded, and result in a new RC number.

## 2. Make the version-only change on the release branch

Create a version-only commit or PR with base `release/<version>`. It contains
only version fields and generated manifests or lockfiles required by the
version change. It must not contain user-facing prose or a website gitlink
update.

Record its commit as `RELEASE_VERSION_SHA`. All subsequent release work uses
the release branch, never the moving `main` branch.

## 3. Write and review release notes against that branch

Build the changelog worksheet from the exact range:

```text
v<previous-version>..RELEASE_VERSION_SHA
```

Classify only changes in that range. A merged PR outside the range belongs to
the next release, even if it has a lower PR number or is user-visible.

Prepare two ordered changes, both based on the release branch:

1. a child PR in `nowledge-labs-website` with the `date: "unreleased"` entry;
2. a release-branch parent commit that advances the website gitlink and adds
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
before creating any package tag.

## 4. Freeze release metadata and compile

After notes and the parent gitlink are finalized, record the branch head as
`RELEASE_SHA`. Run source preflights and build the native bundles from exactly
that SHA. The native-link gate belongs in the pre-release artifact smoke if it
requires a built bundle.

If a check fails:

- fix only release infrastructure or a confirmed release blocker on the
  release branch;
- re-run the affected checks against the new branch head;
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

## 6. Promote the tested RC to GA

Promote the exact RC artifacts with `promote-rc-to-ga.yml`; do not rebuild for
GA. Validate matching artifact digests, release assets, package registries,
and updater/CDN readback before publishing the GA release.

## 7. Close the history loop

After GA, merge only the release branch's version and release-note metadata
back to `main`, date the Changelog entry, and update the parent gitlink. Run
the release-history check to prove the GA tag and the deployed Changelog remain
reachable from `main`.

## Fast-path rule

Skipping RC is reserved for an isolated, low-risk change with a previously
validated release pipeline. A versioned App release, a changed release
workflow, a renamed private dependency, or any native bundle change always
uses the RC path.
