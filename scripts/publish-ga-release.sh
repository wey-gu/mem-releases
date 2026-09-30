#!/usr/bin/env bash
# Called only by the protected GA jobs, before distribution and after delivery verification.
set -euo pipefail

mode="$1"
version="$2"
repo="${GITHUB_REPOSITORY:?}"
tag="v${version}"
notes_file="ga-release-notes.md"

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "::error::Invalid GA version: $version"; exit 1; }

if [[ "$mode" == prepare ]]; then
  if gh release view "$tag" -R "$repo" --json isDraft --jq '.isDraft' 2>/dev/null | grep -qx false; then
    echo "::error::$tag is already public; refusing to replace its assets"
    exit 1
  fi
  curl -fsS --get 'https://mem.nowledge.co/api/changelog/release-notes' \
    --data-urlencode "version=$version" --data-urlencode 'limit=8' |
    jq -er --arg version "$version" '
      select(.found == true and .version == $version and
             (.title | type == "string" and length > 0) and
             (.release_notes | type == "string" and length > 0)) |
      "## \(.title)\n\n\(.release_notes)\n\n[Full changelog](https://mem.nowledge.co/changelog#\($version))\n"
    ' > "$notes_file"
  exit 0
fi

[[ "$mode" == publish && "$#" == 4 ]] || { echo "::error::Usage: $0 publish VERSION TARGET LATEST"; exit 1; }
target="$3" # RC tag for promotion/recovery, exact workflow SHA for a direct GA build.
latest="$4"
[[ "$latest" == true || "$latest" == false ]] || exit 1
[[ -s "$notes_file" ]] || { echo "::error::Missing GA release notes"; exit 1; }

release="$(gh release view "$tag" -R "$repo" --json isDraft,isPrerelease,assets,body)"
echo "$release" | jq -e '.isDraft == true and .isPrerelease == false' >/dev/null
for suffix in aarch64.dmg x64.dmg x64-setup.exe amd64.deb amd64.AppImage; do
  echo "$release" | jq -e --arg name "Nowledge.Mem_${version}_${suffix}" \
    '.assets | any(.name == $name)' >/dev/null || {
      echo "::error::Missing $suffix from $tag"; exit 1;
    }
done

target_sha="$(gh api "repos/$repo/commits/$target" --jq .sha)"
existing_sha="$(gh api "repos/$repo/commits/$tag" --jq .sha 2>/dev/null || true)"
if [[ -n "$existing_sha" && "$existing_sha" != "$target_sha" ]]; then
  echo "::error::$tag points to $existing_sha, expected $target_sha"
  exit 1
fi

body="$(echo "$release" | jq -r .body)"
if [[ -z "$body" || "$body" == "Promoted from v"* || "$body" == "Desktop bundles for "* ]]; then
  gh release edit "$tag" -R "$repo" --target "$target_sha" --notes-file "$notes_file"
else
  gh release edit "$tag" -R "$repo" --target "$target_sha"
fi

id="$(gh release view "$tag" -R "$repo" --json databaseId --jq .databaseId)"
gh api -X PATCH "repos/$repo/releases/$id" -F draft=false >/dev/null
gh api -X PATCH "repos/$repo/releases/$id" -f "make_latest=$latest" >/dev/null
gh release view "$tag" -R "$repo" --json isDraft,publishedAt \
  --jq 'select(.isDraft == false and .publishedAt != null) | .publishedAt' | grep -q .
[[ "$(gh api "repos/$repo/commits/$tag" --jq .sha)" == "$target_sha" ]] || {
  echo "::error::$tag was published on the wrong commit"; exit 1;
}
if [[ "$latest" == true ]]; then
  [[ "$(gh api "repos/$repo/releases/latest" --jq .tag_name)" == "$tag" ]]
fi
