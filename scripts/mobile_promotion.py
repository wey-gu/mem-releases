#!/usr/bin/env python3
"""Admit original signed mobile RC artifacts without rebuilding or resigning."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import subprocess
import uuid
import zipfile


REPOSITORY = "wey-gu/mem-releases"
BUNDLE_ID = "co.nowledge.mem.mobile"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)



def confirmed_upload_delivery(output):
    """Accept explicit Apple success and one delivery, including warning-only receipts."""
    success = re.findall(r"^UPLOAD SUCCEEDED (with no errors|with [1-9][0-9]* warnings?, 0 messages)$",
                         output, re.MULTILINE)
    require(len(success) == 1 and not re.search(r"UPLOAD FAILED|ERROR: |Validation failed", output),
            "App Store upload was not explicitly confirmed")
    if success[0] != "with no errors":
        require(re.search(r"^No errors uploading .+\.ipa['\"]?$", output, re.MULTILINE),
                "Warning-only upload lacks the explicit no-errors receipt")
    deliveries = {str(uuid.UUID(value)) for value in re.findall(
        r"Delivery UUID:\s*([0-9a-fA-F-]{36})", output)}
    require(len(deliveries) == 1, "Expected one unambiguous upload Delivery UUID")
    return deliveries.pop()


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def api(path):
    env = None
    if path.startswith(("repos/nowledge-co/mem/", "repos/nowledge-co/community/")):
        token = os.environ.get("SOURCE_READ_TOKEN")
        if token:
            env = dict(os.environ, GH_TOKEN=token)
    return json.loads(subprocess.check_output(["gh", "api", "--method", "GET", path],
                                             text=True, env=env))


def validate_identity(version, rc_tag, source_sha, tools_sha):
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            and version != "0.0.0", "Expected a non-placeholder clean version")
    require(re.fullmatch(re.escape(version) + r"-rc[1-9][0-9]*", rc_tag),
            "RC and GA versions must match")
    for value in [source_sha, tools_sha]:
        require(re.fullmatch(r"[0-9a-f]{40}", value) and int(value, 16),
                "Expected an exact non-placeholder source/tool SHA")


def plain_log(value):
    value = "\n".join(re.sub(r"^\d{4}-\d\d-\d\dT\S+ ", "", line)
                      for line in value.splitlines())
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def verify_producer_ref(head_branch, rc_tag, tools_sha):
    require(head_branch in {f"release-tools/{rc_tag}-r1", f"v{rc_tag}"},
            "Expected an immutable same-RC mobile tooling ref")
    if head_branch == f"v{rc_tag}":
        require(api(f"repos/{REPOSITORY}/commits/{head_branch}")["sha"] == tools_sha,
                "Original RC tooling tag differs from the producer SHA")


def verify_build_only(run, jobs, logs, version, rc_tag, source_sha, tools_sha):
    validate_identity(version, rc_tag, source_sha, tools_sha)
    require(run.get("path") == ".github/workflows/release-mobile.yml"
            and run.get("event") == "workflow_dispatch"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("run_attempt") == 1
            and run.get("head_sha") == tools_sha,
            "Expected the original successful same-RC mobile producer")
    verify_producer_ref(run.get("head_branch"), rc_tag, tools_sha)
    expected = {"ios": "iOS -> TestFlight", "android": "Android -> APK + AAB"}
    selected = {}
    for platform, name in expected.items():
        hits = [job for job in jobs["jobs"] if job["name"] == name]
        require(len(hits) == 1 and hits[0]["conclusion"] == "success",
                f"Original {platform} build did not succeed")
        job = hits[0]
        text = plain_log(logs[platform])
        require(re.search(r"git log -1 --format=%H\n" + re.escape(source_sha)
                          + r"(?:\n|$)", text), "Original checkout SHA differs")
        steps = {step["name"]: step.get("conclusion") for step in job["steps"]}
        if platform == "ios":
            identity = (f"tag={version} tauri.conf={version} "
                        f"plist.short={version} plist.build={version}")
            require(identity in text and "--no-upload: stopping before App Store Connect" in text
                    and "UPLOAD SUCCEEDED" not in text
                    and steps.get("Build, sign and upload to TestFlight") == "success"
                    and steps.get("Distribute the build to TestFlight") == "skipped",
                    "Original iOS producer was not a signed build-only run")
        else:
            require(steps.get("Locate and verify Android artifacts") == "success"
                    and steps.get("Publish APK to nowledge-co/community release") == "skipped",
                    "Original Android producer was not signed and build-only")
        selected[platform] = job["id"]
    return selected


def extract_checked(archive, destination):
    """Reject traversal, aliases and special files before writing any bytes."""
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        require(len(members) <= 20000 and sum(m.file_size for m in members) <= 2**31,
                "Mobile archive exceeds bounded extraction limits")
        names = set()
        for member in members:
            raw_name = member.orig_filename
            name = PurePosixPath(raw_name)
            require(not name.is_absolute() and ".." not in name.parts
                    and "\\" not in raw_name and ":" not in raw_name
                    and "\n" not in raw_name and "\r" not in raw_name
                    and "\x00" not in raw_name,
                    "Unsafe archive member path")
            portable_name = name.as_posix().casefold()
            require(portable_name not in names, "Duplicate or aliased archive member")
            names.add(portable_name)
            mode = member.external_attr >> 16
            require(not stat.S_ISLNK(mode) and (not stat.S_IFMT(mode)
                    or stat.S_ISREG(mode) or stat.S_ISDIR(mode)), "Unsafe archive member type")
        require(bundle.testzip() is None, "Mobile archive CRC failure")
        destination.mkdir(parents=True, exist_ok=False)
        bundle.extractall(destination)
        for member in members:
            path = destination / member.filename
            mode = (member.external_attr >> 16) & 0o777
            if mode:
                path.chmod(mode)


def ipa_identity(path, version):
    identities = []
    with zipfile.ZipFile(path) as ipa:
        for name, expected_id in [
            ("Payload/Nowledge Mem.app/Info.plist", BUNDLE_ID),
            ("Payload/Nowledge Mem.app/PlugIns/Nowledge Mem Share.appex/Info.plist",
             BUNDLE_ID + ".share"),
        ]:
            member = ipa.getinfo(name)
            require(member.file_size <= 2**20, "Oversized iOS identity")
            value = plistlib.loads(ipa.read(name))
            require(value.get("CFBundleIdentifier") == expected_id
                    and value.get("CFBundleShortVersionString") == version
                    and value.get("CFBundleVersion") == version,
                    "Original IPA App/share version or identifier differs")
            sdk = re.fullmatch(r"iphoneos([0-9]+)(?:\.[0-9]+)*", value.get("DTSDKName", ""))
            require(sdk and int(sdk[1]) >= 26, "IPA requires the uploadable iOS SDK")
            identities.append(dict(path=name, identifier=expected_id, version=version,
                                   build=value["CFBundleVersion"], sdk=value["DTSDKName"]))
    return identities


def verify_files(root, version, source_sha, operation_id=None):
    receipt = json.loads((root / "admission.json").read_text())
    require(receipt["passed"] and receipt["version"] == version
            and receipt["source_sha"] == source_sha, "Admission identity differs")
    require(operation_id is None or receipt["operation_id"] == operation_id,
            "Admission operation differs")
    for item in receipt["files"]:
        path = root / item["path"]
        require(path.resolve().is_relative_to(root.resolve()) and not path.is_symlink()
                and path.is_file() and path.stat().st_size == item["size"]
                and checksum(path) == item["sha256"], "Promoted original file differs")
    return receipt


def admit(args):
    validate_identity(args.version, args.rc_tag, args.source_sha, args.tools_sha)
    require(str(uuid.UUID(args.operation_id)) == args.operation_id,
            "Persist a canonical operation UUID before dispatch")
    require(re.fullmatch(r"[0-9]+", args.source_run), "Expected one producer run ID")
    for sha in {args.tools_sha, os.environ["GITHUB_SHA"]}:
        require(api(f"repos/{REPOSITORY}/compare/{sha}...main")["merge_base_commit"]["sha"] == sha,
                "Producer and promotion tools must be normally merged")
    require(api(f"repos/{REPOSITORY}/commits/release-tools/{args.rc_tag}-r1")["sha"] == args.tools_sha,
            "Original producer tooling tag differs")
    require(api(f"repos/nowledge-co/mem/commits/v{args.rc_tag}")["sha"] == args.source_sha,
            "Original RC source tag differs")
    require(api(f"repos/nowledge-co/mem/compare/{args.source_sha}...main")
            ["merge_base_commit"]["sha"] == args.source_sha, "App source must be merged")
    if args.publish:
        require(api(f"repos/nowledge-co/mem/commits/v{args.version}")["sha"] == args.source_sha,
                "GA source must equal accepted RC source")
        release = api(f"repos/{REPOSITORY}/releases/tags/v{args.rc_tag}")
        require(not release["draft"] and release["prerelease"], "Publish the accepted RC first")
    run = api(f"repos/{REPOSITORY}/actions/runs/{args.source_run}")
    jobs = api(f"repos/{REPOSITORY}/actions/runs/{args.source_run}/jobs?per_page=100")
    require(jobs["total_count"] == len(jobs["jobs"]), "Truncated original job inventory")
    logs = {}
    for platform, name in [("ios", "iOS -> TestFlight"), ("android", "Android -> APK + AAB")]:
        hits = [job for job in jobs["jobs"] if job["name"] == name]
        require(len(hits) == 1, "Ambiguous original mobile job")
        logs[platform] = subprocess.check_output([
            "gh", "api", "--allow-escape-sequences",
            f"repos/{REPOSITORY}/actions/jobs/{hits[0]['id']}/logs"], text=True)
    selected = verify_build_only(run, jobs, logs, args.version, args.rc_tag,
                                 args.source_sha, args.tools_sha)
    inventory = api(f"repos/{REPOSITORY}/actions/runs/{args.source_run}/artifacts?per_page=100")
    require(inventory["total_count"] == len(inventory["artifacts"]), "Truncated artifact inventory")
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    files, archives = [], []
    for platform in ["ios", "android"]:
        matches = [a for a in inventory["artifacts"]
                   if a["name"] == f"NowledgeMem-{args.version}-{platform}" and not a["expired"]]
        require(len(matches) == 1, "Expected one immutable original archive")
        artifact = matches[0]
        require(artifact["workflow_run"]["id"] == run["id"]
                and artifact["workflow_run"]["head_sha"] == args.tools_sha,
                "Archive producer differs")
        archive = root / (platform + ".zip")
        with archive.open("xb") as output:
            subprocess.run(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"],
                           stdout=output, check=True, timeout=600)
        require(artifact.get("digest") == "sha256:" + checksum(archive)
                and archive.stat().st_size == artifact["size_in_bytes"], "Original archive digest differs")
        extract_checked(archive, root / platform)
        selected_files = [f for f in (root / platform).rglob("*") if f.is_file()]
        expected = [".ipa"] if platform == "ios" else [".aab", ".apk"]
        require(sorted(f.suffix for f in selected_files) == expected, "Unexpected mobile file inventory")
        for path in selected_files:
            item = dict(path=str(path.relative_to(root)), size=path.stat().st_size,
                        sha256=checksum(path), platform=platform)
            if path.suffix == ".ipa":
                item["identities"] = ipa_identity(path, args.version)
            elif path.suffix == ".apk":
                sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
                tools = sdk / "build-tools/36.0.0"
                badging = subprocess.check_output([str(tools / "aapt"), "dump", "badging", str(path)], text=True)
                require(re.search(r"package: name='" + re.escape(BUNDLE_ID)
                                  + r"' versionCode='[1-9][0-9]*' .*versionName='"
                                  + re.escape(args.version) + "'", badging), "Actual APK version differs")
                subprocess.run([str(tools / "apksigner"), "verify", str(path)], check=True)
            else:
                result = subprocess.check_output(["jarsigner", "-verify", str(path)], text=True)
                require("jar verified." in result, "AAB signature was not verified")
            files.append(item)
        archives.append(dict(id=artifact["id"], digest=artifact["digest"],
                             size=artifact["size_in_bytes"], platform=platform))
    tree = api(f"repos/nowledge-co/mem/git/trees/{args.source_sha}")["tree"]
    community = [entry["sha"] for entry in tree if entry["path"] == "community"]
    require(len(community) == 1, "Expected one recorded community pointer")
    receipt = dict(passed=True, operation_id=args.operation_id,
                   version=args.version, rc_tag=args.rc_tag, source_sha=args.source_sha,
                   tools_sha=args.tools_sha, source_run=run["id"], producer_attempt=1,
                   jobs=selected, archives=archives, files=files, community_sha=community[0],
                   rebuilt=False, resigned=False, device_execution_verified=False)
    (root / "admission.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(dict(passed=True, source_run=run["id"], version=args.version,
                          source_sha=args.source_sha, rebuilt=False)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--rc-tag", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--tools-sha", required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("mobile-promotion"))
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    admit(args)


if __name__ == "__main__":
    main()
