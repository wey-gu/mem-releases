#!/usr/bin/env bash
# Show release build commands and reuse Bazel's native cache diagnostics.
set -uo pipefail

args=("$@")
command_index=0
while (( command_index < $# )) && [[ "${args[$command_index]}" == -* ]]; do
  case "${args[$command_index]}" in
    --bazelrc|--output_base|--output_user_root|--install_base|--host_jvm_args|--server_javabase|--max_idle_secs|--connect_timeout_secs)
      (( command_index += 2 )) ;;
    *) (( command_index += 1 )) ;;
  esac
done
if [[ "${args[$command_index]:-}" != build ]]; then
  exec "$NMEM_BAZEL_LOG_REAL" "$@"
fi

display=()
private_value=false
for arg in "$@"; do
  if [[ "$private_value" == true ]]; then
    display+=("<redacted>")
    private_value=false
    continue
  fi
  case "${arg%%=*}" in
    --google_credentials|--remote_header|--remote_cache_header|--remote_exec_header|--remote_downloader_header|--bes_header|--credential_helper|--action_env|--host_action_env|--repo_env|--test_env|--define)
      if [[ "$arg" == *=* ]]; then
        display+=("${arg%%=*}=<redacted>")
      else
        display+=("$arg")
        private_value=true
      fi
      ;;
    *)
      case "$arg" in
        *://*@*|*://*\?*|*://*\#*)
          [[ "$arg" == --*=* ]] && display+=("${arg%%=*}=<redacted-url>") || display+=("<redacted-url>") ;;
        *) display+=("$arg") ;;
      esac
      ;;
  esac
done
printf -v module '%q' "$PWD"
printf -v command '%q ' "$NMEM_BAZEL_LOG_REAL" "${display[@]}"
printf 'Bazel module: %s\nBazel command: %s\n' "$module" "$command" >&2

if ! build_log=$(mktemp); then
  exec "$NMEM_BAZEL_LOG_REAL" "$@"
fi
"$NMEM_BAZEL_LOG_REAL" "$@" 2>&1 | tee "$build_log"
status=${PIPESTATUS[0]}

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    printf '### Bazel build\n\n```text\nModule: %s\nCommand: %s\nExit code: %s\n' "$module" "$command" "$status"
    grep -E '(^|[[:space:]])INFO: Elapsed time:' "$build_log" || true
    if ! grep -E '(^|[[:space:]])INFO: [[:digit:]]+ process(es)?([:.]|$)' "$build_log"; then
      printf 'Bazel cache statistics: not reported.\n'
    fi
    printf '```\n\n'
  } >> "$GITHUB_STEP_SUMMARY" || printf 'Could not write Bazel Job Summary; native exit code is preserved.\n' >&2
fi
rm -f "$build_log"
exit "$status"
