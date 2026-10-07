#!/usr/bin/env bash
set -euo pipefail

tools_dir="$(cd "$(dirname "$0")" && pwd)"
release_file="$1"
repo="$2"
target_file="$3"
version="$(jq -r '.tag_name | ltrimstr("v")' "$release_file")"
branch="release/ga-date-${version}"

result="$(node "$tools_dir/date-ga-changelog.mjs" "$release_file" "$target_file")"
date="$(jq -r .date <<<"$result")"
if [[ "$(jq -r .changed <<<"$result")" == false ]]; then
  echo "$repo: $version is already dated $date." >> "$GITHUB_STEP_SUMMARY"
  exit 0
fi

# Never rewrite an existing date branch. A retry reuses its reviewed diff.
remote="$(git ls-remote --heads origin "refs/heads/$branch")"
if [[ -n "$remote" ]]; then
  git checkout -- "$target_file"
  git fetch --depth=2 origin "$branch"
  git checkout --detach FETCH_HEAD
  node "$tools_dir/date-ga-changelog.mjs" "$release_file" "$target_file" > date-result.json
  [[ "$(jq -r .changed date-result.json)" == false ]] || { echo 'Existing branch needs review before retry.' >&2; exit 1; }
  [[ "$(git diff-tree --no-commit-id --name-only -r HEAD)" == "$target_file" ]] || { echo 'Existing branch is not a one-file date change.' >&2; exit 1; }
  previous_dir="$(mktemp -d)"
  previous="$previous_dir/changelog.${target_file##*.}"
  trap 'rm -rf "$previous_dir"' EXIT
  git show "HEAD^:$target_file" > "$previous"
  node "$tools_dir/date-ga-changelog.mjs" "$release_file" "$previous" > /dev/null
  cmp "$previous" "$target_file" || { echo 'Existing branch contains other content changes.' >&2; exit 1; }
  rm -r "$previous_dir"
  rm date-result.json
else
  git checkout -b "$branch"
  git config user.name 'github-actions[bot]'
  git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
  git add -- "$target_file"
  git commit -m "chore(release): date ${version} as ${date}"
  git push origin "HEAD:refs/heads/$branch"
fi

existing="$(gh pr list -R "$repo" --head "$branch" --state all --json state,url --limit 20)"
url="$(jq -r '.[] | select(.state == "OPEN") | .url' <<<"$existing")"
if [[ -z "$url" ]]; then
  [[ "$(jq length <<<"$existing")" == 0 ]] || { echo 'Date PR was closed; inspect it before retrying.' >&2; exit 1; }
  body="$(mktemp)"
  trap 'rm -f "$body"' EXIT
  cat > "$body" <<EOF
### Issue
Issue Number: ref wey-gu/mem-releases#85

### Background
正式 GA https://github.com/wey-gu/mem-releases/releases/tag/v${version} 已公开。按 RELEASE_SOP.md，日期使用实际 published_at 的 UTC 日。

### What problem does this PR solve?
${version} 的发布记录仍为 unreleased。

### How does it work?
只将当前版本日期更新为 ${date}。其他条目、产品代码、标签和制品保持原样；由既有 bot 和评审流程合并。

### Tests
- [x] Unit / source-pin test：发布自动化的 date-ga-changelog 测试覆盖实际 UTC 日、版本隔离与重试。
- [x] Not a UI change
生产网站仍须 staging 与明确 GO；日期 PR 和合并不能作为已部署证据。网站部署校验通过后才能报告验收完成。

#### UI evidence
Not a UI change.

### Side effects / risks
发布日期元数据修正。网站部署与父 gitlink 同步沿用现有授权边界。
EOF
  url="$(gh pr create -R "$repo" --base main --head "$branch" --title "Date ${version} using its GA publication time" --body-file "$body" --label "component/${COMPONENT}" --label type/bug --assignee ThaddeusJiang)"
fi
echo "Date PR: $url" >> "$GITHUB_STEP_SUMMARY"
author="$(gh pr view "$url" --json author --jq .author.login)"
for reviewer in wey-gu hawkingrei; do
  if [[ "$reviewer" != "$author" ]]; then
    seen="$(gh pr view "$url" --json reviewRequests,reviews)"
    if ! jq -e --arg reviewer "$reviewer" '[.reviewRequests[].login, .reviews[].author.login] | index($reviewer) != null' <<<"$seen" > /dev/null; then
      gh pr edit "$url" --add-reviewer "$reviewer"
    fi
  else
    echo "Reviewer $reviewer is the PR author and cannot review their own PR." >> "$GITHUB_STEP_SUMMARY"
  fi
done
