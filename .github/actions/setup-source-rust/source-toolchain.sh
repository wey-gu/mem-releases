#!/usr/bin/env bash
set -euo pipefail

mode="${1:?select or verify is required}"
source_root="$(cd "${2:?the source checkout is required}" && pwd)"
identity="$source_root/rust-toolchain-version.txt"
installer="$source_root/bazel/release/install-rust-toolchain.sh"

if [[ -f "$identity" || -f "$installer" ]]; then
  [[ -s "$identity" && -s "$installer" ]] || {
    echo 'source compiler identity and installer must be present together' >&2
    exit 1
  }
fi

if [[ -f "$source_root/rust-toolchain.toml" ]]; then
  channel="$(awk '
    { sub(/\r$/, "") }
    /^[[:space:]]*\[/ {
      in_toolchain = ($0 ~ /^[[:space:]]*\[toolchain\][[:space:]]*(#.*)?$/)
      next
    }
    in_toolchain && /^[[:space:]]*channel[[:space:]]*=/ {
      value = $0
      sub(/^[[:space:]]*channel[[:space:]]*=[[:space:]]*/, "", value)
      if (value !~ /^"[^"]+"[[:space:]]*(#.*)?$/) { invalid = 1; exit }
      sub(/^"/, "", value)
      sub(/"[[:space:]]*(#.*)?$/, "", value)
      print value
      count++
    }
    END { if (invalid || count != 1) exit 1 }
  ' "$source_root/rust-toolchain.toml")"
elif [[ -f "$source_root/rust-toolchain" ]]; then
  channel="$(tr -d '\r' < "$source_root/rust-toolchain")"
else
  # Preserve the last stable workflow compiler for refs predating source pins.
  channel=1.99.1
fi
[[ "$channel" =~ ^([0-9]+\.[0-9]+\.[0-9]+|nightly-[0-9]{4}-[0-9]{2}-[0-9]{2})$ ]] || {
  echo 'source compiler must be an exact stable release or dated nightly' >&2
  exit 1
}
if [[ "$channel" == nightly-* && ! -s "$identity" ]]; then
  echo 'a source nightly requires its full compiler identity and installer' >&2
  exit 1
fi

case "$mode" in
  select)
    printf 'RUSTUP_TOOLCHAIN=%s\n' "$channel" >> "${GITHUB_ENV:?}"
    printf 'channel=%s\n' "$channel" >> "${GITHUB_OUTPUT:?}"
    ;;
  verify)
    export RUSTUP_TOOLCHAIN="$channel"
    if [[ -s "$installer" ]]; then
      bash "$installer" "$source_root"
    else
      observed="$(cd "$source_root" && rustc --version --verbose | sed -n 's/^release: //p')"
      [[ "$observed" == "$channel" ]] || {
        echo 'installed compiler differs from the source release' >&2
        exit 1
      }
    fi
    ;;
  *) echo 'unknown source compiler operation' >&2; exit 1 ;;
esac
