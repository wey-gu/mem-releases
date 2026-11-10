#!/usr/bin/env python3
"""Attach an uploaded build to the TestFlight beta group.

Uploading to App Store Connect does not make a build testable. Three things
have to happen, and only the first two can be automated:

  1. the build finishes PROCESSING            (poll)
  2. it is attached to a beta group           (one API call)
  3. Apple's Beta App Review passes           (external groups only; not ours
                                               to grant, so this only submits)

Environment:
  APPLE_API_KEY        App Store Connect key id
  APPLE_API_ISSUER     issuer uuid
  APPLE_API_KEY_PATH   path to AuthKey_<id>.p8
  BETA_GROUP_ID        target beta group
  VERSION              the version just uploaded
  GITHUB_STEP_SUMMARY  optional; a summary is appended when present
"""
import json
import os
import re
import uuid
import sys
import time
import urllib.error
import urllib.request

import jwt

BASE = "https://api.appstoreconnect.apple.com"
POLL_ATTEMPTS = 40
POLL_SECONDS = 30
BUNDLE_ID = "co.nowledge.mem.mobile"


def _token(key_id: str, issuer: str, private_key: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": issuer, "iat": now, "exp": now + 900, "aud": "appstoreconnect-v1"},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


class Client:
    def __init__(self, key_id: str, issuer: str, private_key: str) -> None:
        self._args = (key_id, issuer, private_key)

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            BASE + path,
            method=method,
            data=json.dumps(body).encode() if body else None,
            headers={
                "Authorization": f"Bearer {_token(*self._args)}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode()[:400]
            raise RuntimeError(f"{method} {path} -> {error.code} {detail}") from error


def verify_original_upload(run, jobs, log, version, source_sha, build_id):
    """Bind recovery to the source and Delivery UUID of one successful upload."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise RuntimeError("Recovery requires a clean release version")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise RuntimeError("Recovery requires an exact source SHA")
    if str(uuid.UUID(build_id)) != build_id:
        raise RuntimeError("Recovery requires the original Delivery UUID")
    if run.get("path") != ".github/workflows/release-mobile.yml" or run.get("event") != "workflow_dispatch":
        raise RuntimeError("Original run is not a manual mobile release")
    if run.get("status") != "completed":
        raise RuntimeError("Original upload run must be terminal")
    ios = [job for job in jobs["jobs"] if job["name"] == "iOS -> TestFlight"]
    if len(ios) != 1 or not any(step["name"] == "Build, sign and upload to TestFlight" and step["conclusion"] == "success" for step in ios[0]["steps"]):
        raise RuntimeError("Original signed upload did not succeed")
    plain = "\n".join(re.sub(r"^\d{4}-\d\d-\d\dT\S+ ", "", line) for line in log.splitlines())
    plain = re.sub(r"\x1b\[[0-9;]*m", "", plain)
    if not re.search(r"git log -1 --format=%H\n" + re.escape(source_sha) + r"(?:\n|$)", plain):
        raise RuntimeError("Original upload source SHA does not match")
    identity = f"tag={version} tauri.conf={version} plist.short={version} plist.build={version}"
    if identity not in plain or "UPLOAD SUCCEEDED with no errors" not in plain or f"Delivery UUID: {build_id}" not in plain:
        raise RuntimeError("Original upload version or Delivery UUID does not match")
    return dict(passed=True, source_run=run["id"], source_sha=source_sha, version=version, build_id=build_id)


def find_app(client):
    apps = client.call("GET", f"/v1/apps?filter[bundleId]={BUNDLE_ID}&limit=2")["data"]
    if len(apps) != 1 or apps[0]["attributes"]["bundleId"] != BUNDLE_ID:
        raise RuntimeError("Expected exactly the Nowledge Mem iOS app")
    return apps[0]["id"]


def wait_for_valid_build(client: Client, app_id: str, version: str, expected_build_id: str | None = None) -> dict:
    """Return the newest build for `version` once App Store Connect calls it VALID.

    Sorting by -uploadedDate matters: a re-run of the same tag leaves older
    builds with the same version string, and attaching the wrong one would look
    like success while shipping a stale binary.
    """
    for _ in range(POLL_ATTEMPTS):
        hits = client.call(
            "GET",
            f"/v1/builds?filter[app]={app_id}&filter[version]={version}"
            "&limit=1&sort=-uploadedDate",
        )["data"]
        if hits:
            build = hits[0]
            if build["attributes"]["version"] != version:
                raise RuntimeError("App Store Connect returned a different build version")
            if expected_build_id and build["id"] != expected_build_id:
                raise RuntimeError("Expected original uploaded build ID does not match")
            state = build["attributes"]["processingState"]
            print(f"build {build['id']} version={version} state={state}", flush=True)
            if state == "VALID":
                return build
            if state in {"INVALID", "FAILED"}:
                raise RuntimeError(f"App Store Connect rejected the build: {state}")
        else:
            print("build not visible yet", flush=True)
        time.sleep(POLL_SECONDS)
    raise RuntimeError(
        f"build {version} did not reach VALID within "
        f"~{POLL_ATTEMPTS * POLL_SECONDS // 60} minutes"
    )


def main() -> int:
    try:
        key_id = os.environ["APPLE_API_KEY"]
        issuer = os.environ["APPLE_API_ISSUER"]
        group = os.environ["BETA_GROUP_ID"]
        version = os.environ["VERSION"]
        private_key = open(os.environ["APPLE_API_KEY_PATH"]).read()
    except KeyError as missing:
        print(f"::error::missing environment variable {missing}", file=sys.stderr)
        return 2

    client = Client(key_id, issuer, private_key)
    try:
        app_id = find_app(client)
        build = wait_for_valid_build(client, app_id, version, os.environ.get("EXPECTED_BUILD_ID"))

        # "What to Test", shown to testers in TestFlight and read by the Beta
        # App Review team. Set BEFORE submitting: a submitted build's
        # localisation is no longer editable.
        notes = (
            f"Nowledge Mem {version}\n\n"
            f"What changed in this release:\n"
            f"https://mem.nowledge.co/changelog\n\n"
            f"Please report anything that looks wrong via the in-app feedback."
        )
        existing = client.call(
            "GET", f"/v1/builds/{build['id']}/betaBuildLocalizations")["data"]
        if existing:
            client.call(
                "PATCH", f"/v1/betaBuildLocalizations/{existing[0]['id']}",
                {"data": {"type": "betaBuildLocalizations",
                          "id": existing[0]["id"],
                          "attributes": {"whatsNew": notes}}})
        else:
            client.call(
                "POST", "/v1/betaBuildLocalizations",
                {"data": {"type": "betaBuildLocalizations",
                          "attributes": {"locale": "en-US", "whatsNew": notes},
                          "relationships": {"build": {"data": {
                              "type": "builds", "id": build["id"]}}}}})
        print("set What to Test notes")

        # Submit for Beta App Review BEFORE attaching to the group. An external
        # group refuses a build that has not been submitted:
        #   422 ENTITY_UNPROCESSABLE "Build is not in an externally assignable
        #   state."
        # Doing it the other way round is what failed on 0.10.64.
        review = "submitted for Beta App Review"
        try:
            client.call(
                "POST", "/v1/betaAppReviewSubmissions",
                {"data": {"type": "betaAppReviewSubmissions",
                          "relationships": {"build": {"data": {
                              "type": "builds", "id": build["id"]}}}}})
        except RuntimeError as error:
            # A retry reaching this point again is normal, not a failure.
            if "ENTITY_ERROR" in str(error) or "already" in str(error).lower():
                review = "already submitted for Beta App Review"
            else:
                raise
        print(review)

        # Attaching can only succeed once review has been requested, and Apple
        # takes a moment to move the build into that state.
        for attempt in range(10):
            try:
                client.call(
                    "POST", f"/v1/betaGroups/{group}/relationships/builds",
                    {"data": [{"type": "builds", "id": build["id"]}]})
                print(f"attached build {build['id']} to beta group {group}")
                break
            except RuntimeError as error:
                if "externally assignable" in str(error) and attempt < 9:
                    print("not externally assignable yet; waiting")
                    time.sleep(30)
                    continue
                raise
        else:
            raise RuntimeError("build never became externally assignable")
    except RuntimeError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write(
                f"\n### TestFlight {version}\n\n"
                f"Build `{build['id']}` is VALID and attached to the beta group.\n\n"
                f"{review} The group is EXTERNAL, so testers see it only once "
                f"Apple approves. CI cannot wait on that.\n"
            )
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "verify-upload":
        from pathlib import Path
        run, jobs, log = [Path(path) for path in sys.argv[2:5]]
        receipt = verify_original_upload(json.loads(run.read_text()), json.loads(jobs.read_text()), log.read_text(), os.environ["VERSION"], os.environ["SOURCE_SHA"], os.environ["EXPECTED_BUILD_ID"])
        Path("testflight-upload-admission.json").write_text(json.dumps(receipt, indent=2) + "\n")
    else:
        raise SystemExit(main())
