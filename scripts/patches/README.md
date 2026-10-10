# RPM short-write backport

`rpm-0.16-short-write.patch` backports the checksum-writer portion of
[rpm-rs d4bbc47](https://github.com/rpm-rs/rpm-rs/commit/d4bbc47b1a1c1cf5c160ee8e93881e1f34c5c83b).
The old writer hashed the entire offered buffer before returning a partial
compression write, so a caller's `write_all` repeatedly hashed large suffixes.
The patch consumes the full buffer internally and includes a short-write digest
regression plus a complete gzip package/archive digest regression. Compression
remains configured by the App.

`build-tauri-rpm-cli.sh` pins Tauri CLI 2.11.4 by full Git SHA and checks the
published rpm 0.16.0 crate's SHA-256 before applying the patch. It preserves the
upstream dependency lock except for replacing that one registry package with
the patched source. Build-profile overrides apply only to this packaging tool.

Remove this backport when the selected Tauri CLI includes the upstream fix
(rpm-rs 0.23.0 or later), after equivalent package and digest validation. Updating
the App's Cargo.lock does not change the prebuilt npm CLI's dependencies.

Use `rpm-test.yml` for build-only validation with a frozen App commit, an original
Desktop run containing successful DEB/AppImage artifacts, the App version, and
the expected DEB digest. Set `source_tag` when validating an RC such as
`v0.10.99-rc1`; it defaults to `v<version>` for GA. Both repository tags and the
original run are checked, and private Cargo metadata is pinned to the source
gitlink. It repacks those App binaries without compiling them,
requires gzip level 1, and verifies payload identity, ABI compatibility, package
integrity, and the raw archive digest. It uploads a verification receipt with
the RPM and does not publish packages. The original artifacts must still exist.

`verify_rpm_archive.py` streams the uncompressed payload and checks its exact
SHA-256 against `PAYLOADDIGESTALT`. Ubuntu 22.04's RPM 4.17 `rpm2cpio` exits 1
when optional `LONGARCHIVESIZE` metadata is absent, even after emitting the full
archive. rpm-rs 0.16 does not write that tag. Accept this legacy exit status only
when the tag is absent and the complete digest matches; truncated output,
wrong digests, missing digests, and other converter failures remain fatal.
This check runs in both the release RPM lane and standalone validation.

Production integration is deferred until App 0.10.99. Keep this change out of
the current 0.10.98 release and run the full Linux package validation against a
frozen 0.10.99 candidate and its qualified DEB/AppImage artifacts before merging.
