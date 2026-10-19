#!/usr/bin/env python3
"""Require six successful receipts bound to the same candidate and build run."""

import json
import os
from pathlib import Path
import re
import sys

from verify_packaged_desktop import PLATFORMS, require


def accept(root, version, source_sha, build_run_id):
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
        require(re.fullmatch(r"[0-9a-f]{64}", receipt.get("package_sha256", "")) is not None,
                f"Missing package digest: {path}")
        require(receipt.get("app_output") == f"Nowledge Mem {version} ({platform})", f"App mismatch: {path}")
        require(receipt.get("health_details", {}).get("version") == version, f"Runtime mismatch: {path}")
        require(receipt.get("health_details", {}).get("build_sha") == source_sha, f"Runtime SHA mismatch: {path}")
        require(receipt.get("cli_output") == f"nmem {version}", f"CLI mismatch: {path}")
        seen.add(platform)
    require(seen == set(PLATFORMS), "Incomplete packaging coverage")
    return seen


if __name__ == "__main__":
    accept(Path(sys.argv[1]), os.environ["VERIFY_VERSION"], os.environ["SOURCE_SHA"], os.environ["BUILD_RUN_ID"])
    print("PASS: 6/6 packaged Desktop version receipts")
