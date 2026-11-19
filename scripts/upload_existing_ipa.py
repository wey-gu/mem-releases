#!/usr/bin/env python3
"""Upload one admitted, already signed RC IPA; never build or resign it."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from mobile_promotion import extract_checked, ipa_identity, require, verify_files


def confirm_upload(returncode, output):
    require(returncode == 0 and "UPLOAD SUCCEEDED with no errors" in output
            and not re.search(r"UPLOAD FAILED|ERROR: |Validation failed", output),
            "App Store upload was not confirmed; preserve logs before recovery")
    deliveries = re.findall(r"Delivery UUID:\s*([0-9a-fA-F-]{36})", output)
    require(len(deliveries) == 1, "Expected one confirmed upload Delivery UUID")
    return str(uuid.UUID(deliveries[0]))


def assert_unuploaded(client, app_id, version):
    hits = client.call("GET", f"/v1/builds?filter[app]={app_id}&filter[version]={version}&limit=1")
    require(not hits["data"],
            "An App Store build already exists; recover its original upload instead of uploading again")


def main():
    root = Path("mobile-promotion")
    version, source_sha = os.environ["VERSION"], os.environ["SOURCE_SHA"]
    receipt = verify_files(root, version, source_sha, os.environ["OPERATION_ID"])
    require(str(uuid.UUID(os.environ["OPERATION_ID"])) == os.environ["OPERATION_ID"],
            "Persist a canonical operation UUID before upload")
    sha = subprocess.check_output(["git", "-C", "source", "rev-parse", "HEAD"], text=True).strip()
    require(sha == source_sha, "GA source checkout differs")
    config = json.loads(Path("source/nowledge-graph/mobile/src-tauri/tauri.conf.json").read_text())
    require(config["version"] == version, "Canonical mobile version differs")
    items = [item for item in receipt["files"] if item["path"].endswith(".ipa")]
    require(len(items) == 1, "Expected one admitted IPA")
    item = items[0]
    ipa = root / item["path"]
    identities = ipa_identity(ipa, version)
    extracted = Path(os.environ["RUNNER_TEMP"]) / "original-ipa-signature"
    extract_checked(ipa, extracted)
    subprocess.run(["codesign", "--verify", "--deep", "--strict",
                    str(extracted / "Payload/Nowledge Mem.app")], check=True)
    # This same source/identity line is consumed by the existing upload recovery gate.
    print(f"tag={version} tauri.conf={config['version']} "
          f"plist.short={identities[0]['version']} plist.build={identities[0]['build']}", flush=True)
    spec = importlib.util.spec_from_file_location("testflight", Path(__file__).with_name("testflight-distribute.py"))
    testflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(testflight)
    key = Path(os.environ["APPLE_API_KEY_PATH"]).read_text()
    client = testflight.Client(os.environ["APPLE_API_KEY"], os.environ["APPLE_API_ISSUER"], key)
    assert_unuploaded(client, testflight.find_app(client), version)
    intent = dict(operation_id=os.environ["OPERATION_ID"], source_sha=source_sha,
                  version=version, source_run=receipt["source_run"], ipa_sha256=item["sha256"],
                  upload_run=int(os.environ["GITHUB_RUN_ID"]), rebuilt=False, resigned=False)
    Path("ios-upload-intent.json").write_text(json.dumps(intent, indent=2) + "\n")
    try:
        result = subprocess.run([
            "xcrun", "altool", "--upload-app", "--type", "ios", "--file", str(ipa),
            "--apiKey", os.environ["APPLE_API_KEY"], "--apiIssuer", os.environ["APPLE_API_ISSUER"],
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=1800)
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or b""
        Path("ios-upload.log").write_text(partial.decode(errors="replace")
                                          if isinstance(partial, bytes) else partial)
        Path("ios-upload-uncertain.json").write_text(json.dumps(intent, indent=2) + "\n")
        raise RuntimeError("Upload outcome is uncertain; reconcile App Store state before retry") from error
    Path("ios-upload.log").write_text(result.stdout)
    print(result.stdout, flush=True)
    delivery = confirm_upload(result.returncode, result.stdout)
    print("Delivery UUID: " + delivery, flush=True)
    with ipa.open("rb") as stream:
        require(hashlib.file_digest(stream, "sha256").hexdigest() == item["sha256"],
                "The uploaded IPA was changed")
    intent.update(passed=True, build_id=delivery)
    Path("ios-upload-receipt.json").write_text(json.dumps(intent, indent=2) + "\n")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("build_id=" + delivery + "\n")


if __name__ == "__main__":
    main()
