# ARM-only nightly qualification

Use this mode to build and smoke-test only the signed, notarized macOS ARM64 package from a pinned source commit. It is an engineering Actions artifact, not an RC or GA release.

## Dispatch

Choose the merged source commit you intend to inspect, then dispatch manually with the complete 40-character SHA. This example pins a merged source revision:

```sh
gh workflow run release-desktop.yml -R wey-gu/mem-releases --ref main \
  -f tag=nightly \
  -f source_ref=1c7f288e96b27fee437a9ccc5751449b5b247fac \
  -f publish=false \
  -f nightly_macos_arm64_only=true
```

The workflow rejects this mode for non-manual events, another tag, `publish=true`, Windows-only, or either Windows probe/benchmark input. Leave those inputs off. The resolver pins the full source SHA; the artifact name uses its 12-character prefix.

## Artifact and digests

After the existing ARM signing/notarization and packaged-backend smoke succeed, the run stores one seven-day Actions artifact containing only the DMG and `arm64-nightly-provenance.json`. The artifact is on the public `mem-releases` repository and remains subject to GitHub's repository access rules. The provenance records the full source SHA, version, run ID/URL, workflow SHA, and the DMG SHA-256. The run summary separately records the Actions artifact archive digest and URL; the archive digest is different from the DMG digest.

This mode creates no RC or GA release, tag, R2/APT object, or updater-feed entry. It does not change ordinary nightly behavior: the full-matrix nightly uploads no binaries and continues to own its release-shape report. The two nightly modes share the existing nightly concurrency group.

The workflow and its contract tests establish the build gate only. No ARM package is accepted until a real run completes signing, notarization, packaged smoke, upload, and receipt checks; any later native/product acceptance must be reviewed separately.
