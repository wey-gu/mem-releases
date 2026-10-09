"""Observe release Bazel builds without changing query output or cache policy."""

from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
import uuid


PRIVATE_OPTIONS = {
    "--google_credentials", "--remote_header", "--bes_header", "--credential_helper",
    "--remote_cache_header", "--remote_exec_header", "--remote_downloader_header",
    "--action_env", "--host_action_env", "--repo_env", "--test_env", "--define",
}
STARTUP_VALUE_OPTIONS = {
    "--bazelrc", "--output_base", "--output_user_root", "--install_base",
    "--host_jvm_args", "--server_javabase", "--max_idle_secs", "--connect_timeout_secs",
}


def redacted_command(args):
    result = []
    private_value = False
    for arg in args:
        key, separator, value = arg.partition("=") if arg.startswith("--") else (arg, "", "")
        if private_value:
            result.append("<redacted>")
            private_value = False
        elif key in PRIVATE_OPTIONS:
            result.append(key + "=<redacted>" if separator else key)
            private_value = not separator
        else:
            visible = value if separator else arg
            if "://" in visible:
                try:
                    url = urlsplit(visible)
                    visible = urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1],
                                          url.path, "<redacted>" if url.query else "", ""))
                except ValueError:
                    visible = "<redacted-url>"
            result.append(key + "=" + visible if separator else visible)
    return [arg.replace("\r", "\\r").replace("\n", "\\n") for arg in result]


def build_index(args):
    index = 0
    while index < len(args) and args[index].startswith("-"):
        option = args[index]
        index += 2 if option in STARTUP_VALUE_OPTIONS else 1
    return index if index < len(args) and args[index] == "build" else None


def option_value(args, name):
    for index in range(len(args) - 1, -1, -1):
        if args[index].startswith(name + "="):
            return args[index][len(name) + 1:]
        if args[index] == name and index + 1 < len(args):
            return args[index + 1]
    return None


def build_metrics(path):
    started, metrics = [], []
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if "started" in event:
                    started.append(event["started"])
                if "buildMetrics" in event:
                    metrics.append(event["buildMetrics"])
        if len(started) > 1 or len(metrics) != 1 or "actionSummary" not in metrics[0]:
            raise ValueError("Expected one build's ActionSummary")
        if started and not isinstance(started[0], dict):
            raise ValueError("Invalid build start event")
        actions = metrics[0]["actionSummary"]
        runners = {}
        for entry in actions.get("runnerCount", []):
            name, count = entry["name"], int(entry.get("count", 0))
            if not isinstance(name, str) or not name or count < 0:
                raise ValueError("Invalid runner count")
            runners[name] = runners.get(name, 0) + count
        result = {"status": "available", "runners": runners}
        if "actionCacheStatistics" in actions:
            cache = actions["actionCacheStatistics"]
            result["local_action_cache"] = {
                "hits": int(cache.get("hits", 0)), "misses": int(cache.get("misses", 0)),
            }
            if any(count < 0 for count in result["local_action_cache"].values()):
                raise ValueError("Negative local action cache count")
        return (started[0] if started else {}), result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, {"status": "unavailable", "reason": "Missing or invalid BEP build metrics"}


def code(value):
    return "<code>" + html.escape(str(value)).replace("|", "&#124;") + "</code>"


def summary(receipt):
    lines = [f"### Bazel build: {code(receipt['module'])}", "",
             f"- Exit code: **{receipt['exit_code']}**; elapsed: {receipt['elapsed_seconds']:.2f}s",
             f"- Bazel: {code(receipt['bazel_version'])}; invocation: {code(receipt['invocation_id'])}",
             f"- Command: {code(shlex.join(receipt['command']))}", ""]
    metrics = receipt["metrics"]
    if metrics["status"] == "available":
        lines += ["| Runner | Processes |", "| --- | ---: |"]
        lines += [f"| {code(name)} | {count} |" for name, count in sorted(metrics["runners"].items())]
        if not metrics["runners"]:
            lines += ["| No runner counters reported | - |"]
        cache = metrics.get("local_action_cache")
        lines += ["", f"Local action cache: {cache['hits']} hits, {cache['misses']} misses."
                  if cache else "Local action cache: not reported."]
    else:
        lines += ["Cache metrics: **unavailable** (missing or invalid BEP)."]
    return "\n".join(lines) + "\n\n"


def configure():
    real_bazel = os.environ.get("NMEM_OBS_REAL_BAZEL") or shutil.which("bazel")
    if not real_bazel:
        raise RuntimeError("Bazel must be installed before enabling build receipts")
    root = Path(os.environ["RUNNER_TEMP"])
    wrapper_dir = root / "nmem-bazel-wrapper"
    wrapper_dir.mkdir(exist_ok=True)
    wrapper = wrapper_dir / "bazel"
    wrapper.write_text('#!/usr/bin/env bash\nexec "$NMEM_OBS_PYTHON" "$NMEM_OBS_SCRIPT" "$@"\n',
                       encoding="utf-8", newline="\n")
    wrapper.chmod(0o755)
    values = {
        "NMEM_OBS_REAL_BAZEL": real_bazel,
        "NMEM_OBS_PYTHON": Path(sys.executable).as_posix(),
        "NMEM_OBS_SCRIPT": Path(__file__).resolve().as_posix(),
        "NMEM_OBS_RECEIPT_DIR": str(root / "nmem-bazel-receipts"),
    }
    with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as stream:
        for name, value in values.items():
            stream.write(f"{name}={value}\n")
    with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as stream:
        stream.write(str(wrapper_dir) + "\n")


def run(args):
    real_bazel = os.environ["NMEM_OBS_REAL_BAZEL"]
    index = build_index(args)
    if index is None:
        status = subprocess.call([real_bazel, *args])
        return 128 - status if status < 0 else status
    receipt_dir = Path(os.environ["NMEM_OBS_RECEIPT_DIR"])
    with tempfile.TemporaryDirectory(prefix="nmem-bazel-bep-") as directory:
        command = list(args)
        if not any(arg.split("=", 1)[0] in ("--announce_rc", "--noannounce_rc") for arg in args):
            command.insert(index + 1, "--announce_rc")
        event_file = option_value(command, "--build_event_json_file")
        if event_file is None:
            event_file = str(Path(directory) / "events.jsonl")
            command.insert(index + 1, f"--build_event_json_file={event_file}")
        safe_command = redacted_command([real_bazel, *command])
        print(f"Bazel module: {Path.cwd()}\nBazel command: {shlex.join(safe_command)}",
              file=sys.stderr, flush=True)
        start = time.monotonic()
        status = subprocess.call([real_bazel, *command])
        status = 128 - status if status < 0 else status
        elapsed = time.monotonic() - start
        started, metrics = build_metrics(Path(event_file))
        receipt = {
            "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "module": str(Path.cwd()), "source_sha": os.environ.get("NMEM_BUILD_SHA_FULL"),
            "command": safe_command, "exit_code": status, "elapsed_seconds": elapsed,
            "bazel_version": started.get("buildToolVersion", "unavailable"),
            "invocation_id": started.get("uuid", "unavailable"), "metrics": metrics,
        }
        print(summary(receipt), file=sys.stderr, flush=True)
        try:
            receipt_dir.mkdir(parents=True, exist_ok=True)
            path = receipt_dir / f"build-{uuid.uuid4().hex}.json"
            path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
                    stream.write(summary(receipt))
        except OSError:
            print("Bazel receipt could not be persisted; native build exit code is preserved.",
                  file=sys.stderr)
        return status


if __name__ == "__main__":
    if sys.argv[1:] == ["--configure"]:
        configure()
    else:
        sys.exit(run(sys.argv[1:]))
