#!/usr/bin/env bash
# Build the source-pinned packaging CLI with the upstream RPM short-write fix.
set -euo pipefail

tool_dir="${1:?Usage: build-tauri-rpm-cli.sh TOOL_DIRECTORY}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tauri_sha=8909f221d1515955fc843808032bdc5d62209c96
rpm_sha256=1630639f4dbc1c71ad7b704cda2171584c80735c502efae94804d02763fc6f7d
mkdir -p "$tool_dir"
tool_dir="$(cd "$tool_dir" && pwd)"

git init -q "$tool_dir/tauri"
git -C "$tool_dir/tauri" fetch --depth=1 https://github.com/tauri-apps/tauri.git "$tauri_sha"
git -C "$tool_dir/tauri" checkout --detach FETCH_HEAD
[[ "$(git -C "$tool_dir/tauri" rev-parse HEAD)" == "$tauri_sha" ]]

curl --fail --location --retry 3 \
  https://static.crates.io/crates/rpm/rpm-0.16.0.crate -o "$tool_dir/rpm.crate"
printf '%s  %s\n' "$rpm_sha256" "$tool_dir/rpm.crate" | sha256sum --check -
mkdir "$tool_dir/rpm"
tar -xzf "$tool_dir/rpm.crate" --strip-components=1 -C "$tool_dir/rpm"
patch --batch --forward -p1 -d "$tool_dir/rpm" < "$script_dir/patches/rpm-0.16-short-write.patch"

python3 - "$tool_dir" <<'PYTHON'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
lock = root / "tauri/Cargo.lock"
original = lock.read_text()
registry_entry = '''name = "rpm"
version = "0.16.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "1630639f4dbc1c71ad7b704cda2171584c80735c502efae94804d02763fc6f7d"
'''
assert original.count(registry_entry) == 1
lock.write_text(original.replace(registry_entry, 'name = "rpm"\nversion = "0.16.0"\n'))
(root / "patch.toml").write_text(
    "[patch.crates-io]\nrpm = { path = " + json.dumps(str(root / "rpm")) + " }\n"
)
PYTHON

# This profile applies only to the packaging tool, never to the App binaries.
export CARGO_PROFILE_RELEASE_LTO=false
export CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
export CARGO_PROFILE_RELEASE_OPT_LEVEL=3
cargo test --manifest-path "$tool_dir/rpm/Cargo.toml" \
  --release --no-default-features --features gzip-compression --lib short_write
cargo build --manifest-path "$tool_dir/tauri/Cargo.toml" \
  --config "$tool_dir/patch.toml" --locked --release -p tauri-cli --bin cargo-tauri
"$tool_dir/tauri/target/release/cargo-tauri" tauri --version
sha256sum "$tool_dir/tauri/target/release/cargo-tauri"
