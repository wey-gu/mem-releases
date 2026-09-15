# RPM short-write backport

`rpm-0.16-short-write.patch` backports the checksum-writer portion of
[rpm-rs d4bbc47](https://github.com/rpm-rs/rpm-rs/commit/d4bbc47b1a1c1cf5c160ee8e93881e1f34c5c83b).
The old writer hashed the entire offered buffer before returning a partial
compression write, so a caller's `write_all` repeatedly hashed large suffixes.
The patch consumes the full buffer internally and includes a short-write digest
regression. Compression remains configured by the App; recovery requires gzip
level 1 and verifies both the compressed package and raw archive digests.

`build-tauri-rpm-cli.sh` pins Tauri CLI 2.11.4 by full Git SHA and checks the
published rpm 0.16.0 crate's SHA-256 before applying the patch. It preserves the
upstream dependency lock except for replacing that one registry package with
the patched source. Build-profile overrides apply only to this packaging tool.

Remove this backport when the selected Tauri CLI includes the upstream fix
(rpm-rs 0.23.0 or later), after equivalent package and digest validation. Updating
the App's Cargo.lock does not change the prebuilt npm CLI's dependencies.

For an interrupted direct GA, run `rpm-test.yml` with the frozen App commit,
original Desktop run, version, and expected DEB digest. After it succeeds and the
original Desktop run is terminal without starting publication, dispatch
`release-desktop.yml` with `reuse_run_id`, `rpm_recovery_run_id`, and the reviewed
full `rpm_recovery_sha`. The recovery gate requires all other platform builds to
have succeeded and checks the RPM receipt before reusing the existing publisher.
The Changelog production gate still applies before package distribution.
