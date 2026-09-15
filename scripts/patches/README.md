# RPM short-write backport

`rpm-0.16-short-write.patch` backports the checksum-writer portion of
[rpm-rs d4bbc47](https://github.com/rpm-rs/rpm-rs/commit/d4bbc47b1a1c1cf5c160ee8e93881e1f34c5c83b).
The old writer hashed the entire offered buffer before returning a partial
compression write, so a caller's `write_all` repeatedly hashed large suffixes.
The patch consumes the full buffer internally and includes a short-write digest
regression. Compression remains configured by the App.

`build-tauri-rpm-cli.sh` pins Tauri CLI 2.11.4 by full Git SHA and checks the
published rpm 0.16.0 crate's SHA-256 before applying the patch. It preserves the
upstream dependency lock except for replacing that one registry package with
the patched source. Build-profile overrides apply only to this packaging tool.

Remove this backport when the selected Tauri CLI includes the upstream fix
(rpm-rs 0.23.0 or later), after equivalent package and digest validation. Updating
the App's Cargo.lock does not change the prebuilt npm CLI's dependencies.

Use `rpm-test.yml` for build-only validation with a frozen App commit, an original
Desktop run containing successful DEB/AppImage artifacts, the version, and the
expected DEB digest. It repacks those App binaries without compiling them,
requires gzip level 1, and verifies payload identity, ABI compatibility, package
integrity, and the raw archive digest. It uploads a verification receipt with
the RPM and does not publish packages. The original artifacts must still exist.
