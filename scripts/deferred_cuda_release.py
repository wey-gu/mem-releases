#!/usr/bin/env python3
"""Admit only a reviewed deferred CUDA plan for an already accepted public GA."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.request import Request, urlopen

import verify_packaged_docker as docker
from verify_packaged_desktop import VERSION, require


def validate_plan(plan):
    version = plan.get("ga_version", "")
    require(VERSION.fullmatch(version) is not None,
            "Invalid GA version")
    require(re.fullmatch("v" + re.escape(version) + r"-rc[1-9][0-9]*",
                         plan.get("release_tag", "")) is not None, "RC belongs to another GA")
    for field in ("source_sha", "tools_sha"):
        require(docker.SHA.fullmatch(plan.get(field, "")) is not None, "Invalid frozen SHA")
    require(type(plan.get("producer_run_id")) is int and plan["producer_run_id"] > 0,
            "Invalid original producer")
    if "expected_digest" in plan:
        require(docker.DIGEST.fullmatch(plan["expected_digest"]) is not None,
                "Invalid original CUDA digest")
    require(re.fullmatch(r"https://github\.com/nowledge-co/mem/issues/[1-9][0-9]*#issuecomment-[0-9]+",
                         plan.get("core_acceptance", "")) is not None,
            "A reviewed core GA acceptance receipt is required")


def select_plan(plans, event_name, event, version):
    require(isinstance(plans, list), "Invalid plan registry")
    for plan in plans:
        validate_plan(plan)
    require(len({p["ga_version"] for p in plans}) == len(plans)
            and len({p["producer_run_id"] for p in plans}) == len(plans), "Ambiguous plan registry")
    if event_name == "workflow_run":
        run = event["workflow_run"]
        require(run["repository"]["full_name"] == docker.REPOSITORY,
                "Foreign completion event")
        return next((p for p in plans if p["producer_run_id"] == run["id"]), None)
    require(event_name == "workflow_dispatch", "Unsupported release event")
    matches = [p for p in plans if p["ga_version"] == version]
    require(len(matches) == 1, "No reviewed plan for requested GA")
    return matches[0]


def published_ga(plan):
    latest = docker.api(f"repos/{docker.REPOSITORY}/releases/latest")
    require(latest["tag_name"] == "v" + plan["ga_version"]
            and latest["draft"] is False and latest["prerelease"] is False
            and bool(latest.get("published_at")), "GA is unpublished or superseded; hold CUDA")
    source = docker.api(f"repos/nowledge-co/mem/commits/v{plan['ga_version']}", source=True)
    require(source["sha"] == plan["source_sha"], "GA source changed")
    return latest


def validate_binding(plan, identity):
    docker.validate_identity(identity)
    expected = dict(build_run_id=plan["producer_run_id"], source_sha=plan["source_sha"],
                    build_workflow_sha=plan["tools_sha"], release_tag=plan["release_tag"],
                    variant="cuda")
    if "expected_digest" in plan:
        expected["digest"] = plan["expected_digest"]
    require(all(identity.get(key) == value for key, value in expected.items()),
            "Native candidate differs from the reviewed CUDA plan")


def admit(plan, runner):
    validate_plan(plan)
    docker.runner_matrix("cuda", runner)
    run = docker.api(f"repos/{docker.REPOSITORY}/actions/runs/{plan['producer_run_id']}")
    require(run["status"] == "completed" and run["conclusion"] == "success",
            "Original producer has not succeeded; hold CUDA")
    require(run["head_sha"] == plan["tools_sha"], "Original build tools changed")
    digest = plan.get("expected_digest")
    if digest is None:
        jobs = docker.api(f"repos/{docker.REPOSITORY}/actions/runs/{run['id']}"
                          f"/attempts/{run['run_attempt']}/jobs?per_page=100")
        require(jobs["total_count"] == len(jobs["jobs"]), "Producer jobs pagination required")
        publisher = docker.unique_job(jobs["jobs"], "build-cuda")
        log = docker.api(f"repos/{docker.REPOSITORY}/actions/jobs/{publisher['id']}/logs", raw=True)
        digests = re.findall(r'"containerimage.digest": "(sha256:[0-9a-f]{64})"', log)
        require(len(digests) == 1, "Original CUDA publication digest is missing or ambiguous")
        digest = digests[0]
    identity = docker.identity(SimpleNamespace(
        build_run_id=plan["producer_run_id"], release_tag=plan["release_tag"],
        source_sha=plan["source_sha"], variant="cuda", digest=digest,
        build_tools_tag=""))
    require(identity["digest"] == digest, "Original publication digest mismatch")
    validate_binding(plan, identity)
    latest = published_ga(plan)
    request = Request("https://mem.nowledge.co/api/changelog/release-notes?locale=en",
                      headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=45) as response:
        require(response.status == 200, "Changelog read failed")
        notes = json.load(response)
    require(notes.get("found") is True and notes.get("version") == plan["ga_version"]
            and notes.get("date") == latest["published_at"][:10], "Public GA Changelog is not ready")
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans", type=Path, default=Path("release-plans/deferred-cuda.json"))
    parser.add_argument("--version", default="")
    parser.add_argument("--runner", default="")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    require(os.environ.get("GITHUB_REPOSITORY") == docker.REPOSITORY, "Wrong release repository")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main", "Only merged main may arm publication")
    if args.record:
        admission = json.loads(Path("admission.json").read_text())
        identity = json.loads(Path("identity.json").read_text())
        require(identity == admission["identity"], "Admitted identity changed")
        accepted = docker.aggregate(Path("receipts"), identity)
        native = json.loads(Path("receipts/amd64.json").read_text())
        require(native["acceptance_run_id"] == os.environ["GITHUB_RUN_ID"]
                and native["acceptance_workflow_sha"] == os.environ["GITHUB_SHA"],
                "Native receipt belongs to another acceptance run")
        plan = admission["plan"]
        validate_plan(plan)
        require("expected_digest" in plan, "Admission did not freeze the original publication digest")
        validate_binding(plan, identity)
        published_ga(plan)
        tags = [plan["ga_version"] + "-cuda", plan["ga_version"].rsplit(".", 1)[0] + "-cuda"]
        for tag in tags:
            manifest = json.loads(docker.command([
                "docker", "buildx", "imagetools", "inspect", docker.IMAGE + ":" + tag,
                "--format", "{{json .Manifest}}"], timeout=90))
            require(manifest["digest"] == identity["digest"], "Published CUDA digest mismatch")
        receipt = dict(passed=True, source_sha=identity["source_sha"], digest=identity["digest"],
                       native_acceptance=accepted, plan=plan, tags=tags, rebuilt=False,
                       acceptance_run_id=os.environ["GITHUB_RUN_ID"],
                       verified_at=datetime.now(timezone.utc).isoformat())
        Path("cuda-publication.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print("PASS original CUDA digest promoted and independently read back")
        return
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    plan = select_plan(json.loads(args.plans.read_text()), os.environ["GITHUB_EVENT_NAME"],
                       event, args.version)
    if plan is None:
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("eligible=false\n")
        print("No reviewed plan for this producer; no publication")
        return
    identity = admit(plan, args.runner)
    plan = dict(plan, expected_digest=identity["digest"])
    Path("identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    Path("admission.json").write_text(json.dumps(dict(
        plan=plan, identity=identity, runner=args.runner,
        admitted_at=datetime.now(timezone.utc).isoformat()), indent=2) + "\n")
    values = dict(eligible="true", ga=plan["ga_version"], rc=plan["release_tag"][1:],
                  digest=identity["digest"], runner=args.runner)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
