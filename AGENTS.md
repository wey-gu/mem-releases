# Release ordering

Publish the Changelog website before publishing any release packages. Do not
start package distribution until the Changelog deployment has completed
successfully.

# Release candidate integrity

For every Nowledge Mem App release, keep the version change and the release
notes as separate, ordered changes:

1. Create and merge a version-only PR in `nowledge-co/mem` first. It contains
   version fields and their generated manifests or lockfiles only; it must not
   include user-facing changelog prose or a `nowledge-labs-website` gitlink
   update.
2. After that PR is merged, draft and review the Changelog in
   `nowledge-labs-website`. Ordinary `main` PRs may continue to merge while
   the notes are being prepared.
3. Before merging the parent Changelog-pointer PR, start a release freeze.
   Fetch `origin/main`, record `RELEASE_BASE_SHA`, and refresh the notes
   against that exact source state. Do not merge unrelated source changes
   until the release candidate has passed its required preflight gates.
4. After the notes pointer is merged, record the resulting `main` commit as
   `RELEASE_SHA`. Run every source preflight and native-link check against
   `RELEASE_SHA`; create RC and GA tags only from that merged commit. Never
   tag an unmerged release branch.
5. The deployed Changelog must contain the unreleased entry represented by
   `RELEASE_SHA` before any package distribution begins.
6. After GA, merge the dated Changelog entry and its parent gitlink update
   back to `main`, then run the source release-history check for the GA tag.
   This closes the history loop and prevents the tagged release from becoming
   unreachable from `main`.
