#!/usr/bin/env python3
"""Attach the exact admitted APK to the existing GA source tag, without clobbering."""

import json
import os
from pathlib import Path
import subprocess
import time

from mobile_promotion import api, require, verify_files


def main():
    root = Path("mobile-promotion")
    version, source_sha = os.environ["VERSION"], os.environ["SOURCE_SHA"]
    receipt = verify_files(root, version, source_sha, os.environ["OPERATION_ID"])
    require(api(f"repos/nowledge-co/mem/commits/v{version}")["sha"] == source_sha,
            "GA source tag differs")
    require(api(f"repos/nowledge-co/community/commits/v{version}")["sha"] == receipt["community_sha"],
            "GA community tag differs from the recorded App pointer")
    selected = [item for item in receipt["files"] if item["path"].endswith(".apk")]
    require(len(selected) == 1, "Expected one admitted APK")
    item = selected[0]
    apk = root / item["path"]
    releases = []
    for page in range(1, 11):
        batch = api(f"repos/nowledge-co/community/releases?per_page=100&page={page}")
        releases.extend(batch)
        if len(batch) < 100:
            break
        require(page < 10, "Truncated community release inventory")
    matches = [release for release in releases if release["tag_name"] == "v" + version]
    require(len(matches) <= 1, "Ambiguous GA community release")
    Path("android-promotion-intent.json").write_text(json.dumps(dict(
        operation_id=os.environ["OPERATION_ID"], source_sha=source_sha,
        version=version, source_run=receipt["source_run"], apk_sha256=item["sha256"],
        existing_release=matches[0]["id"] if matches else None,
    ), indent=2) + "\n")
    if not matches:
        subprocess.run(["gh", "release", "create", "v" + version, "-R", "nowledge-co/community",
                        "--verify-tag", "--latest=false", "--title", "v" + version,
                        "--notes", "Nowledge Mem Android APK " + version], check=True)
    release = api(f"repos/nowledge-co/community/releases/tags/v{version}")
    require(not release["draft"] and not release["prerelease"], "Expected the public GA mobile release")
    existing = [asset for asset in release["assets"] if asset["name"] == apk.name]
    require(len(existing) <= 1, "Ambiguous existing APK")
    if existing:
        require(existing[0].get("digest") == "sha256:" + item["sha256"]
                and existing[0]["size"] == item["size"], "Refuse an existing APK with different bytes")
    else:
        subprocess.run(["gh", "release", "upload", "v" + version, str(apk),
                        "-R", "nowledge-co/community"], check=True)
    for attempt in range(6):
        release = api(f"repos/nowledge-co/community/releases/tags/v{version}")
        assets = [asset for asset in release["assets"] if asset["name"] == apk.name]
        if (len(assets) == 1 and assets[0].get("digest") == "sha256:" + item["sha256"]
                and assets[0]["size"] == item["size"]):
            break
        require(attempt < 5, "Published APK readback differs")
        time.sleep(2)
    Path("android-promotion-receipt.json").write_text(json.dumps(dict(
        passed=True, operation_id=os.environ["OPERATION_ID"],
        source_sha=source_sha, version=version, source_run=receipt["source_run"],
        original_sha256=item["sha256"], asset=assets[0], rebuilt=False, resigned=False,
    ), indent=2) + "\n")


if __name__ == "__main__":
    main()
