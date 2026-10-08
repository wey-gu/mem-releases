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

# Release candidate integrity

For every Nowledge Mem App release, cut a release branch before release
metadata work begins. Keep the version change and the release notes as
separate, ordered changes:

1. Fetch `origin/main`, record `RELEASE_BASE_SHA`, and create
   `release/<version>` from that exact commit. The branch is the fixed product
   boundary; never merge moving `main` into it.
2. Create and merge a version-only PR in `nowledge-co/mem` **to `main`**. It contains
   version fields and their generated manifests or lockfiles only; it must not
   include user-facing changelog prose or a `nowledge-labs-website` gitlink
   update.
3. Draft and review the Changelog in `nowledge-co/nowledge-labs-website`, then
   merge its parent gitlink/history PR **to `main`**. Ordinary product PRs may continue
   to merge while the notes are prepared. Deploy and read back the public
   Changelog before package distribution.
4. Record the exact merged `main` commits for the version-only and parent
   metadata changes. Cherry-pick only those commits onto `release/<version>`
   with `git cherry-pick -x`, in dependency order. Never merge `main` into the
   release branch or pick unrelated product commits.
5. Record the resulting release-branch head as `RELEASE_SHA`. Run every source
   preflight against `RELEASE_SHA`. Observe the merge-triggered
   `ci/mem-native-link-arm64` result and stop only if it has failed; do not
   manually trigger it for a release. Create RC and GA tags only from the
   resulting commit. A tag does not need to be a Git ancestor of `main`; it
   must have auditable `-x` provenance to the reviewed `main` metadata.
6. After GA, date the Changelog entry and merge its website and parent gitlink
   updates to `main`. Run the release-history check for patch-equivalence and
   `-x` provenance, rather than an ancestor-only check.
