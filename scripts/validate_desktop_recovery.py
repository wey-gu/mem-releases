"""Validate the provenance and payload of a Desktop artifact recovery."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


BUILD_ARTIFACTS = {
    "build-macos-arm64": "macos-arm64",
    "build-macos-x86_64": "macos-x86_64",
    "build-windows": "windows-x86_64",
    "build-linux-deb-appimage": "linux-deb-appimage",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_runs(original, jobs, recovery, version, tag_sha, recovery_sha):
    require(
        original["status"] == "completed",
        "Original run must be terminal before recovery",
    )
    require(
        original["conclusion"] in {"failure", "cancelled"},
        "Original run did not fail or cancel",
    )
    require(
        original["path"] == ".github/workflows/release-desktop.yml",
        "Wrong original workflow",
    )
    require(
        original["event"] == "workflow_dispatch",
        "Expected an explicit original GA dispatch",
    )
    require(original["head_branch"] == f"v{version}", "Original run version differs")
    require(original["head_sha"] == tag_sha, "Original workflow tag differs")
    for name in BUILD_ARTIFACTS:
        matches = [job for job in jobs if job["name"] == name]
        require(
            len(matches) == 1 and matches[0]["conclusion"] == "success",
            f"Unqualified build: {name}",
        )
    for job in jobs:
        if job["name"] == "publish":
            require(
                not job.get("steps"),
                "Original publisher started; inspect partial publication first",
            )
    require(
        recovery["status"] == "completed" and recovery["conclusion"] == "success",
        "RPM recovery failed",
    )
    require(
        recovery["path"] == ".github/workflows/rpm-test.yml",
        "Wrong RPM recovery workflow",
    )
    require(
        recovery["event"] == "workflow_dispatch",
        "Expected an explicit RPM recovery dispatch",
    )
    require(
        recovery["head_sha"] == recovery_sha,
        "RPM recovery tooling differs from the reviewed SHA",
    )


def validate_receipt(receipt, version, source_sha, original_run, recovery_sha):
    expected = {
        "version": version,
        "source_sha": source_sha,
        "source_run": original_run,
        "tooling_sha": recovery_sha,
        "compression": {"type": "gzip", "level": 1},
        "rpm_verified": True,
    }
    for key, value in expected.items():
        require(receipt.get(key) == value, f"RPM receipt mismatch: {key}")
    for key in ["deb_sha256", "rpm_sha256", "archive_sha256"]:
        require(
            re.fullmatch(r"[0-9a-f]{64}", receipt.get(key, "")),
            f"Invalid receipt digest: {key}",
        )


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--original-run", type=int, required=True)
    parser.add_argument("--rpm-run", type=int, required=True)
    parser.add_argument("--rpm-sha", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    require(
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version), "Expected a GA version"
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", args.source_sha), "Expected a full source SHA"
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", args.rpm_sha),
        "Expected a reviewed RPM workflow SHA",
    )

    def api(suffix):
        return json.loads(
            subprocess.check_output(
                ["gh", "api", f"repos/{args.repo}/{suffix}"], text=True
            )
        )

    original = api(f"actions/runs/{args.original_run}")
    recovery = api(f"actions/runs/{args.rpm_run}")
    jobs = api(f"actions/runs/{args.original_run}/jobs?per_page=100")["jobs"]
    tag = api(f"git/ref/tags/v{args.version}")["object"]
    if tag["type"] == "tag":
        tag = api(f"git/tags/{tag['sha']}")["object"]
    validate_runs(original, jobs, recovery, args.version, tag["sha"], args.rpm_sha)

    receipt = json.loads((args.artifacts / "linux-rpm/receipt.json").read_text())
    validate_receipt(
        receipt, args.version, args.source_sha, args.original_run, args.rpm_sha
    )
    for artifact, suffix in [
        ("macos-arm64", "dmg"),
        ("macos-x86_64", "dmg"),
        ("windows-x86_64", "exe"),
        ("linux-deb-appimage", "deb"),
        ("linux-deb-appimage", "AppImage"),
        ("linux-rpm", "rpm"),
    ]:
        files = list((args.artifacts / artifact).glob(f"*.{suffix}"))
        require(
            len(files) == 1, f"Expected exactly one {suffix} artifact in {artifact}"
        )
        require(
            args.version in files[0].name,
            f"Artifact filename version differs: {files[0].name}",
        )
        if suffix in {"deb", "rpm"}:
            require(
                sha256(files[0]) == receipt[f"{suffix}_sha256"],
                f"Recovered {suffix} digest differs",
            )
    print(f"Qualified six Desktop artifacts for {args.version} from {args.source_sha}")


if __name__ == "__main__":
    main()
