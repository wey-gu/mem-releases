# Packaged Desktop version acceptance

After each RC build, run `desktop-version-e2e.yml` with its exact completed
tag-push `release-desktop` run ID, source RC tag, and full Mem `RELEASE_SHA`. Retain the
test workflow SHA/run URL alongside the release record. This is a read-only
acceptance workflow: it does not build replacements, publish packages, promote
GA, or change the update authority.

The workflow downloads the original artifacts from that run and requires all
six packaging paths: ARM DMG, Intel DMG, NSIS, DEB, AppImage, and RPM. It copies
the complete signed App from each DMG, installs original NSIS/DEB/RPM packages,
and uses AppImage's extracted AppRun with its original APPIMAGE/APPDIR identity.
RPM executes as an unprivileged user on Fedora; each other case uses a fresh
native hosted runner. Do not run the verifier against a developer profile.

The build run must identify that exact RC tag, including its suffix. Manual or
repository-dispatch builds lack that verifiable run identity and are rejected.
Receipts also bind the build workflow commit, attempt and immutable artifact ID;
another RC built from the same source version and SHA cannot qualify this one.

Each case executes the real App's standalone `--version`, then the bundled
server's `--version`, then starts the original App normally and waits for its
owned backend and normal CLI installation. App/server/installed `nmem` versions
must equal the numeric source version; the App's updater platform must match
the packaging path. The server's build marker and live `/health/details` SHA
must equal `RELEASE_SHA`. The RC suffix belongs to the distribution tag, not the
Rust runtime version. `browse-now` retains its independent component version.
Detailed backend status, database connection and services must all be ready.
CLI discovery observes a fresh login/Windows registry PATH; the verifier never
adds a missing wrapper directory to make installation appear successful.

Any install, launch, timeout, nonzero exit, malformed response, wrong identity,
or absent CLI fails acceptance. Successful receipts include package SHA-256,
build run ID, source SHA, actual execution paths, and observed outputs. The
aggregate job requires six distinct successful receipts for the same candidate.
Parser/aggregate contract tests are not native artifact proof.

The RC is not qualified while the workflow is pending or failed. Preserve its
failure logs, repair the responsible source/tooling boundary, rebuild changed
artifacts, and rerun against the exact resulting build. Source preflights, the
pre-tag frontend/shell E2E gates, Changelog deployment/readback, and owner smoke
remain separate release requirements.
