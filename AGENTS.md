# Release ordering

Publish the Changelog website before publishing any release packages. Do not
start package distribution until the Changelog deployment has completed
successfully.

# Changelog repository and synchronization

Submit all Changelog and release-note changes to the canonical repository:
`git@github.com:nowledge-co/nowledge-labs-website`.

`git@github.com:wey-gu/nowledge-labs-website.git` is a mirror. Do not edit,
commit, or push release-note changes directly to it. During each release,
ask a maintainer to synchronize the mirror from the canonical repository.
Wait for that synchronization and the Changelog deployment to complete,
then read back the public Changelog before distributing release packages.

# Desktop update delivery

Before package distribution, verify named operator authentication and the
known-good stable release in the production desktop update authority. After all
GA artifacts are ready, prepare and publish the exact candidate using the
observed revision and a persisted operation UUID. Retain the stable recovery
baseline until the candidate is accepted.

Before declaring desktop delivery complete, verify
`/latest?platform=<platform>&update_control=1&request_id=<fresh-id>` with the
previous desktop version's native User-Agent for `mac`, `mac_intel`, `win`,
`linux-appimage`, `linux-deb`, and `linux-rpm`. Require a fresh protocol-1
`allowed` response with the expected version, request ID, target, download URL,
file size, SHA-256 and `no-store` cache policy. Ordinary `/latest` and
`/update.json` readback alone does not verify desktop Check for Updates.

# Release candidate integrity

For every Nowledge Mem App release, cut a release branch before release
metadata work begins. Keep the version change and parent release metadata as
separate, ordered commits in one release-scoped PR to `nowledge-co/mem`.

During an active release, label that PR and any necessary reviewed release
repair PR opened in `nowledge-co/mem` with `severity/critical` to raise merge
priority. The label does not relax review, CI, or bot-only merge gates and
must not be used to pull unrelated product work into the release.

Before creating any RC or GA tag or starting package distribution, require
terminal success for both canonical E2E gates against the exact `RELEASE_SHA`:
`ci/mem-frontend-e2e` (Web E2E and Web contract tests) and
`ci/mem-shell-e2e` (desktop shell E2E). Failed, missing, pending, cancelled,
skipped, superseded, or results for another SHA block the release. A previous
release's pass, an unchanged-frontend claim, source preflight, artifact smoke,
or manual inspection is not a substitute. If the normal postsubmit path cannot
test `RELEASE_SHA`, stop before tags and arrange controlled exact-SHA CI; never
waive this gate.

1. Fetch `origin/main`, record `RELEASE_BASE_SHA`, and create
   `release/<version>` from that exact commit. The branch is the fixed product
   boundary; never merge moving `main` into it.
2. Open one release-scoped PR in `nowledge-co/mem` **to `main`**. Its first
   commit contains version fields and their generated manifests or lockfiles
   only; it must not include user-facing changelog prose or a
   `nowledge-labs-website` gitlink update. Keep the PR open for the parent
   metadata commit.
3. Draft and review the Changelog in `nowledge-co/nowledge-labs-website`, then
   merge the child PR. Add a second commit to the same Mem PR that advances the
   website gitlink and engineering history. Ordinary product PRs may continue
   to merge while the notes are prepared. Merge the final combined PR through
   the normal bot path, then deploy and read back the public Changelog before
   package distribution.
4. Record the combined PR's exact merged `main` commit. Cherry-pick only that
   commit onto `release/<version>` with `git cherry-pick -x`. Never merge
   `main` into the release branch or pick unrelated product commits.
5. Record the resulting release-branch head as `RELEASE_SHA`. Run every source
   preflight and both mandatory E2E gates against `RELEASE_SHA`. Observe the
   merge-triggered `ci/mem-native-link-arm64` result and stop only if it has
   failed; do not manually trigger it for a release. Create RC and GA tags only
   from the resulting commit. A tag does not need to be a Git ancestor of
   `main`; it must have auditable `-x` provenance to the reviewed `main`
   metadata.
6. After GA, date the Changelog entry and merge its website and parent gitlink
   updates to `main`. Run the release-history check for patch-equivalence and
   `-x` provenance, rather than an ancestor-only check.
