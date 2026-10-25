#!/usr/bin/env python3
"""Require six successful receipts bound to the same candidate and build run."""

import json
import os
from pathlib import Path
import re
import sys

from verify_packaged_desktop import PLATFORM_ARTIFACTS, PLATFORMS, require, runtime_is_ready, validate_server_output
from verify_packaged_desktop import validate_windows_package_record


def validate_build_identity(run, release_tag, tools_sha=None):
    require(run.get("path") == ".github/workflows/release-desktop.yml", "Wrong build workflow")
    require(run.get("status") == "completed" and run.get("conclusion") == "success", "Build did not succeed")
    tagged_ga_dispatch = (
        run.get("event") == "workflow_dispatch"
        and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", release_tag) is not None
        and tools_sha is not None
    )
    require((run.get("event") == "push" or tagged_ga_dispatch)
            and run.get("head_branch") == release_tag, "Build run belongs to another release")
    require(re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")) is not None, "Missing build workflow SHA")
    if tools_sha is not None:
        require(run["head_sha"] == tools_sha, "Build is not the peeled tools tag commit")
    require(type(run.get("run_attempt")) is int and run["run_attempt"] > 0, "Missing build attempt")


def accept(root, version, source_sha, build_run_id, identity):
    paths = list(root.rglob("*.json"))
    require(len(paths) == len(PLATFORMS), f"Expected six receipts, found {len(paths)}")
    seen = set()
    for path in paths:
        receipt = json.loads(path.read_text())
        platform = receipt.get("platform")
        require(platform in PLATFORMS and platform not in seen, f"Duplicate/unknown platform: {platform}")
        require(receipt.get("schema") == 1 and receipt.get("passed") is True, f"Failed receipt: {path}")
        for name, expected in (("version", version), ("source_sha", source_sha), ("build_run_id", build_run_id)):
            require(receipt.get(name) == expected, f"{name} mismatch: {path}")
        for name in ("release_tag", "build_workflow_sha", "build_attempt"):
            require(receipt.get(name) == identity[name], f"{name} mismatch: {path}")
        require(receipt.get("artifact_id") == identity["artifacts"][PLATFORM_ARTIFACTS[platform]],
                f"Artifact identity mismatch: {path}")
        require(re.fullmatch(r"[0-9a-f]{64}", receipt.get("package_sha256", "")) is not None,
                f"Missing package digest: {path}")
        require(receipt.get("app_output") == f"Nowledge Mem {version} ({platform})", f"App mismatch: {path}")
        if platform == "win":
            app = receipt.get("app_command", [])
            require(len(app) == 1, "Missing installed Windows executable path")
            validate_windows_package_record(receipt.get("windows_package_registration") or {}, app[0], version)
        server_output = receipt.get("server_output", "")
        validate_server_output(server_output, version, source_sha)
        require(receipt.get("health_details", {}).get("version") == version, f"Runtime mismatch: {path}")
        require(receipt.get("health_details", {}).get("build_sha") == source_sha, f"Runtime SHA mismatch: {path}")
        require(runtime_is_ready(receipt.get("health_details", {})), f"Backend is not ready: {path}")
        require(receipt.get("cli_output") == f"nmem {version}", f"CLI mismatch: {path}")
        seen.add(platform)
    require(seen == set(PLATFORMS), "Incomplete packaging coverage")
    return seen


if __name__ == "__main__":
    identity = {"release_tag": os.environ["RELEASE_TAG"], "build_workflow_sha": os.environ["BUILD_WORKFLOW_SHA"],
                "build_attempt": os.environ["BUILD_ATTEMPT"], "artifacts": json.loads(os.environ["ARTIFACTS"])}
    accept(Path(sys.argv[1]), os.environ["VERIFY_VERSION"], os.environ["SOURCE_SHA"], os.environ["BUILD_RUN_ID"], identity)
    print("PASS: 6/6 packaged Desktop version receipts")
