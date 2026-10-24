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

After that six-platform readback succeeds, accept the published candidate with
the audited `complete` authority operation. This advances the candidate to the
stable recovery baseline, retains the former stable release as `previous`, and
is required before preparing the next Desktop release.

# Release candidate integrity

For a new Nowledge Mem App release, select an exact already-merged `main`
commit with the intended version, then create `release/<version>` at that SHA.
If a version bump is needed, merge one version-only PR first, independently of
website notes. The selected commit is both `RELEASE_BASE_SHA` and `RELEASE_SHA`;
never merge moving main into the fixed branch. No website gitlink, engineering
release prose or GA date commit is required in Mem.

During an active release, label that PR and any necessary reviewed release
repair PR opened in `nowledge-co/mem` with `severity/critical` to raise merge
priority. The label does not relax review, CI, or bot-only merge gates and
must not be used to pull unrelated product work into the release.

# Release version ownership and artifact proof

Treat the App release version as one canonical distribution input, not as a
reason to advance every internal package version. Derived Desktop, CLI,
updater, mobile, lockfile, and generated build metadata must be produced from
that input. Internal libraries should keep independent stable versions unless
their public compatibility or distribution contract actually changes. A
single edited source file does not preserve cache reuse if it still changes
the package version or generated build inputs for every internal crate.

Until the product repository implements this ownership split, update the full
version set required by its current generator and preflight. Do not omit
individual version fields ad hoc during a release. Land the ownership and
cache-boundary refactor as a separately reviewed product change.

After every RC build, run a fail-closed version E2E against the exact built
artifacts for every shipped packaging path. Install or extract the candidate,
execute its real App, embedded server, and installed CLI surfaces where
applicable, and require each observed version and updater/package identity to
equal the release version. Explicitly reject an empty value, `0`, `0.0.0`, or
any build-system placeholder. Source consistency tests, manifest inspection,
and archive listing are not substitutes for executing the packaged binaries.
Any install, launch, readiness, probe, or response-parsing failure must make
the version E2E exit nonzero.

Before creating any RC or GA tag or starting package distribution, require
terminal success for both canonical E2E gates against the exact `RELEASE_SHA`:
`ci/mem-frontend-e2e` (Web E2E and Web contract tests) and
`ci/mem-shell-e2e` (desktop shell E2E). Failed, missing, pending, cancelled,
skipped, superseded, or results for another SHA block the release. A previous
release's pass, an unchanged-frontend claim, source preflight, artifact smoke,
or manual inspection is not a substitute. If the normal postsubmit path cannot
test `RELEASE_SHA`, stop before tags and arrange controlled exact-SHA CI; never
waive this gate.

1. Merge the required version-only PR through normal review, CI and bot gates.
   Skip it when the intended version is already present in the selected source.
2. Select an exact main-reachable commit with that version. Record it as both
   `RELEASE_BASE_SHA` and `RELEASE_SHA`, then create `release/<version>` there.
   Audit the full previous-published-GA..RELEASE_SHA range. Later main changes
   belong to a later release; do not replay metadata to create a new SHA.
3. Mem owns `nmem-rs/crates/nmem-server/src/assets/openapi.json` and its typed
   generation. Product API changes update it in their own Mem PR. The canonical
   website synchronizes from a fixed merged Mem SHA, records its digest and
   generates pages; it never writes product code. Website CI owns copy/page
   and localization checks. A website checkout is not a Mem release prerequisite.
4. Review notes in the canonical website, deploy through its owner, and read
   back the public Changelog before package distribution. Record source SHA,
   website SHA and API digest in mem-releases. An unchanged API may retain an
   older website source receipt only after proving its bytes match RELEASE_SHA.
5. Qualify RELEASE_SHA with all source and mandatory E2E gates. Observe only
   the merge-triggered native-link result; a failure blocks release. Build RCs,
   execute packaged version E2E and promote accepted artifacts without rebuilding.
6. After GA, date only the canonical website entry with the actual GA UTC day.
   Keep engineering worksheets and delivery evidence in mem-releases. Do not
   merge the release branch back or submit release-only parent metadata to Mem.

Existing candidates retain their original frozen base and reviewed `-x`
provenance. Exceptional release repairs first land through normal review on
main, are replayed with `git cherry-pick -x`, and require a new RC plus fresh
exact-SHA qualification. The new main-reachable path does not rewrite old tags
or admit unrelated product work into an existing candidate.
