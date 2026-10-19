#!/usr/bin/env python3
"""Read-only, native version acceptance of an original published Docker image."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import uuid

from verify_packaged_desktop import command, exact_version, require, validate_server_output


REPOSITORY = "wey-gu/mem-releases"
IMAGE = "docker.io/nowledgelabs/mem"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
TAG = re.compile(r"v([0-9]+\.[0-9]+\.[0-9]+)-rc[1-9][0-9]*")
ARCHES = {"cpu": {"amd64", "arm64"}, "cuda": {"amd64"}, "vulkan": {"amd64"}}
GPU_QUERY = ["nvidia-smi", "--query-gpu=uuid,driver_version", "--format=csv,noheader,nounits"]
SURFACES = {
    "server": ("nmem-server --version", "nmem-server"),
    "cli": ("nmem --version", "nmem"),
    "cli_tui": ("nmem tui --version", "nmem-tui"),
    "tui": ("nmem-tui --version", "nmem-tui"),
    "legacy_reader": ("nmem-legacy-graph-reader --version", None),
}


def api(path, *, source=False, raw=False):
    env = dict(os.environ)
    if source:
        env["GH_TOKEN"] = env["MEM_READ_TOKEN"]
    argv = ["gh", "api", path]
    if raw:
        argv.append("--allow-escape-sequences")
    output = command(argv, env=env, timeout=90)
    return output if raw else json.loads(output)


def validate_identity(identity):
    require(set(identity) >= {"repository", "build_run_id", "build_attempt", "build_workflow_sha",
                             "release_tag", "source_sha", "variant", "digest"}, "Incomplete identity")
    require(identity["repository"] == REPOSITORY, "Wrong producer repository")
    require(type(identity["build_run_id"]) is int and identity["build_run_id"] > 0, "Invalid run ID")
    require(type(identity["build_attempt"]) is int and identity["build_attempt"] > 0, "Invalid attempt")
    require(SHA.fullmatch(identity["build_workflow_sha"]) is not None, "Invalid workflow SHA")
    require(SHA.fullmatch(identity["source_sha"]) is not None, "Invalid source SHA")
    require(TAG.fullmatch(identity["release_tag"]) is not None, "Invalid RC tag")
    require(identity["variant"] in ARCHES, "Invalid variant")
    require(DIGEST.fullmatch(identity["digest"]) is not None, "Invalid image digest")


def validate_producer(run, tag, tools_sha):
    require(run["repository"]["full_name"] == REPOSITORY, "Wrong producer repository")
    require(run["path"] == ".github/workflows/release-docker.yml", "Wrong producer workflow")
    require(run["event"] == "push" and run["head_branch"] == tag, "Wrong producer tag event")
    require(run["head_sha"] == tools_sha and SHA.fullmatch(tools_sha) is not None,
            "Producer is not the peeled tools tag commit")
    require(run["status"] in ("in_progress", "completed"), "Producer has not started")


def unique_job(jobs, name):
    matches = [job for job in jobs if job["name"] == name]
    require(len(matches) == 1, f"Expected one producer job: {name}")
    job = matches[0]
    require(job["status"] == "completed" and job["conclusion"] == "success", f"Producer job failed: {name}")
    return job


def identity(args):
    require(args.build_run_id > 0 and TAG.fullmatch(args.release_tag) is not None
            and SHA.fullmatch(args.source_sha) is not None and DIGEST.fullmatch(args.digest) is not None,
            "Invalid candidate inputs")
    run = api(f"repos/{REPOSITORY}/actions/runs/{args.build_run_id}")
    tools = api(f"repos/{REPOSITORY}/commits/{args.release_tag}")
    validate_producer(run, args.release_tag, tools["sha"])
    source = api(f"repos/nowledge-co/mem/commits/{args.release_tag}", source=True)
    require(source["sha"] == args.source_sha, "Mem source tag mismatch")
    jobs = api(f"repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    require(jobs["total_count"] == len(jobs["jobs"]), "Producer jobs pagination required")
    required = ["meta"]
    if args.variant == "cpu":
        required += ["build-amd64", "build-arm64", "publish", "verify (ubuntu-latest, amd64)",
                     "verify (ubuntu-24.04-arm, arm64)"]
        publisher = "publish"
        expression = r"Published manifest digest: (sha256:[0-9a-f]{64})"
    else:
        publisher = f"build-{args.variant}"
        required.append(publisher)
        expression = r'"containerimage.digest": "(sha256:[0-9a-f]{64})"'
    selected = [unique_job(jobs["jobs"], name) for name in required]
    publish = next(job for job in selected if job["name"] == publisher)
    log = api(f"repos/{REPOSITORY}/actions/jobs/{publish['id']}/logs", raw=True)
    digests = re.findall(expression, log)
    require(digests == [args.digest], "Original publication digest is missing, ambiguous or different")
    result = {
        "repository": REPOSITORY, "build_run_id": run["id"], "build_attempt": run["run_attempt"],
        "build_workflow_sha": run["head_sha"], "release_tag": args.release_tag,
        "source_sha": args.source_sha, "variant": args.variant, "digest": args.digest,
        "producer_status": run["status"], "producer_conclusion": run["conclusion"],
        "required_job_ids": {job["name"]: job["id"] for job in selected},
    }
    validate_identity(result)
    return result


def validate_outputs(outputs, version, source_sha):
    require(set(outputs) == set(SURFACES), "Missing or unexpected runtime surface")
    validate_server_output(outputs["server"], version, source_sha)
    exact_version(outputs["cli"], "nmem", version)
    exact_version(outputs["cli_tui"], "nmem-tui", version)
    exact_version(outputs["tui"], "nmem-tui", version)
    reader = json.loads(outputs["legacy_reader"])
    require(reader == {"engine": "kuzu-0.11.0", "protocol": 1, "mem_commit": source_sha}
            and type(reader["protocol"]) is int, "Legacy reader identity mismatch")


def validate_index(index, arch):
    children = [item for item in index["manifests"]
                if item.get("platform", {}).get("os") == "linux"
                and item["platform"].get("architecture") == arch]
    require(len(children) == 1 and DIGEST.fullmatch(children[0]["digest"]) is not None,
            "Missing or ambiguous native platform manifest")
    return children[0]["digest"]


def runner_matrix(variant, cuda_runner):
    require(variant in ARCHES, "Invalid variant")
    if variant == "cuda":
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", cuda_runner) is not None,
                "CUDA requires an approved existing NVIDIA runner label")
        return {"include": [{"arch": "amd64", "runner": cuda_runner}]}
    require(not cuda_runner, "CUDA runner is not applicable to this variant")
    rows = [{"arch": "amd64", "runner": "ubuntu-22.04"}]
    if variant == "cpu":
        rows.append({"arch": "arm64", "runner": "ubuntu-24.04-arm"})
    return {"include": rows}


def validate_gpu_devices(devices):
    require(isinstance(devices, dict) and bool(devices), "No NVIDIA GPU observed")
    for device, driver in devices.items():
        require(isinstance(device, str) and re.fullmatch(
            r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", device) is not None,
            "Invalid NVIDIA GPU identity")
        require(isinstance(driver, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", driver) is not None,
                "Invalid NVIDIA driver version")


def parse_gpu_devices(output):
    devices = {}
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        require(len(fields) == 2 and fields[0] not in devices, "Invalid or duplicate NVIDIA GPU row")
        devices[fields[0]] = fields[1]
    validate_gpu_devices(devices)
    return devices


def validate_cuda_runtime(runtime):
    require(isinstance(runtime, dict) and runtime.get("device_request") == "all",
            "CUDA NVIDIA device request is missing")
    validate_gpu_devices(runtime.get("host_gpus"))
    validate_gpu_devices(runtime.get("container_gpus"))
    require(runtime["host_gpus"] == runtime["container_gpus"], "Container NVIDIA driver/GPU differs from host")


def validate_receipt(receipt):
    validate_identity(receipt["identity"])
    require(receipt["arch"] in ARCHES[receipt["identity"]["variant"]], "Wrong native architecture")
    require(receipt["uid"] == "10001" and receipt["gid"] == "10001", "Wrong runtime user")
    require(DIGEST.fullmatch(receipt["child_digest"]) is not None, "Invalid child digest")
    require(DIGEST.fullmatch(receipt["config_id"]) is not None, "Invalid config ID")
    require(SHA.fullmatch(receipt["acceptance_workflow_sha"]) is not None, "Invalid acceptance workflow SHA")
    match = TAG.fullmatch(receipt["identity"]["release_tag"])
    validate_outputs(receipt["outputs"], match.group(1), receipt["identity"]["source_sha"])
    if receipt["identity"]["variant"] == "cuda":
        validate_cuda_runtime(receipt.get("cuda_runtime"))


def probe(identity, arch):
    validate_identity(identity)
    require(os.environ.get("GITHUB_ACTIONS") == "true", "Disposable native CI runner required")
    require(arch in ARCHES[identity["variant"]], "Wrong architecture for variant")
    native = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    require(native == arch, "Host is not the requested native architecture")
    engine = command(["docker", "info", "--format", "{{.Architecture}}"])
    require(engine in ({"x86_64", "amd64"} if arch == "amd64" else {"aarch64", "arm64"}),
            "Docker engine is not native")
    host_gpus = None
    if identity["variant"] == "cuda":
        host_gpus = parse_gpu_devices(command(GPU_QUERY, timeout=30))
        runtimes = json.loads(command(["docker", "info", "--format", "{{json .Runtimes}}"]))
        require(isinstance(runtimes, dict) and "nvidia" in runtimes,
                "NVIDIA Container Toolkit Docker runtime is missing")
    suffix = "" if identity["variant"] == "cpu" else "-" + identity["variant"]
    tag = IMAGE + ":" + identity["release_tag"][1:] + suffix
    manifest = json.loads(command(["docker", "buildx", "imagetools", "inspect", tag,
                                   "--format", "{{json .Manifest}}"], timeout=90))
    require(manifest["digest"] == identity["digest"], "Published tag digest changed")
    reference = IMAGE + "@" + identity["digest"]
    index = json.loads(command(["docker", "buildx", "imagetools", "inspect", reference, "--raw"], timeout=90))
    child_digest = validate_index(index, arch)
    child = IMAGE + "@" + child_digest
    command(["docker", "pull", "--platform", "linux/" + arch, child], timeout=480)
    config = json.loads(command(["docker", "image", "inspect", child]))
    require(len(config) == 1, "Ambiguous image config")
    config = config[0]
    require(config["Os"] == "linux" and config["Architecture"] == arch, "Wrong pulled platform")
    require(config["Config"]["User"] == "nowledge", "Image default user changed")
    labels = config["Config"]["Labels"]
    require(labels["org.opencontainers.image.version"] == identity["release_tag"][1:], "Wrong image version label")
    require(labels["org.opencontainers.image.source"] == "https://github.com/" + REPOSITORY,
            "Wrong build repository label")
    signatures = json.loads(command([
        "cosign", "verify", reference,
        "--certificate-identity", f"https://github.com/{REPOSITORY}/.github/workflows/release-docker.yml@refs/tags/{identity['release_tag']}",
        "--certificate-oidc-issuer", "https://token.actions.githubusercontent.com",
    ], timeout=120))
    require(isinstance(signatures, list) and signatures, "Missing original digest signature")
    require(all(item["critical"]["image"]["docker-manifest-digest"] == identity["digest"] for item in signatures),
            "Signature covers another digest")

    def execute(shell):
        # CUDA uses its documented NVIDIA host runtime, without replacing image files.
        name = "nmem-version-" + uuid.uuid4().hex
        try:
            device_options = ["--gpus", "all"] if identity["variant"] == "cuda" else []
            command(["docker", "create", *device_options, "--name", name, "--network", "none", "--entrypoint", "/bin/sh",
                     child, "-ec", "exec " + shell])
            try:
                output = command(["docker", "start", "--attach", name], timeout=90)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(
                    f"Packaged command {shell!r} exited {error.returncode}; "
                    f"stdout={str(error.stdout or '').strip()[:4096]!r}; "
                    f"stderr={str(error.stderr or '').strip()[:4096]!r}"
                ) from error
            state = json.loads(command(["docker", "inspect", "--format", "{{json .State}}", name]))
            require(state["Status"] == "exited" and state["ExitCode"] == 0 and not state["OOMKilled"],
                    "Packaged command did not exit successfully")
            return output
        finally:
            command(["docker", "rm", "--force", "--volumes", name])

    uid, gid = execute("id -u"), execute("id -g")
    require(uid == "10001" and gid == "10001", "Runtime is not the default unprivileged user")
    cuda_runtime = None
    if host_gpus is not None:
        cuda_runtime = {"device_request": "all", "host_gpus": host_gpus,
                        "container_gpus": parse_gpu_devices(execute(" ".join(GPU_QUERY)))}
        validate_cuda_runtime(cuda_runtime)
    outputs = {name: execute(shell) for name, (shell, _) in SURFACES.items()}
    version = TAG.fullmatch(identity["release_tag"]).group(1)
    validate_outputs(outputs, version, identity["source_sha"])
    receipt = {"identity": identity, "arch": arch, "child_digest": child_digest, "config_id": config["Id"],
               "uid": uid, "gid": gid, "outputs": outputs, "commands": {key: value[0] for key, value in SURFACES.items()},
               "acceptance_workflow_sha": os.environ["GITHUB_SHA"], "acceptance_run_id": os.environ["GITHUB_RUN_ID"]}
    if cuda_runtime is not None:
        receipt["cuda_runtime"] = cuda_runtime
    validate_receipt(receipt)
    return receipt


def aggregate(directory, expected):
    validate_identity(expected)
    receipts = [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]
    require(bool(receipts), "No native receipts")
    for receipt in receipts:
        validate_receipt(receipt)
    identity = receipts[0]["identity"]
    require(identity == expected, "Receipts do not match the admitted producer")
    require(all(item["identity"] == identity for item in receipts), "Receipts belong to different candidates")
    require(all(item["acceptance_workflow_sha"] == receipts[0]["acceptance_workflow_sha"]
                and item["acceptance_run_id"] == receipts[0]["acceptance_run_id"] for item in receipts),
            "Receipts belong to different acceptance runs")
    require(len(receipts) == len(ARCHES[identity["variant"]])
            and {item["arch"] for item in receipts} == ARCHES[identity["variant"]], "Incomplete native platform coverage")
    return {"accepted": True, "identity": identity, "architectures": sorted(ARCHES[identity["variant"]])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    matrix = sub.add_parser("matrix")
    matrix.add_argument("--variant", required=True, choices=ARCHES)
    matrix.add_argument("--cuda-runner", default="")
    producer = sub.add_parser("identity")
    producer.add_argument("--build-run-id", required=True, type=int)
    producer.add_argument("--release-tag", required=True)
    producer.add_argument("--source-sha", required=True)
    producer.add_argument("--variant", required=True, choices=ARCHES)
    producer.add_argument("--digest", required=True)
    native = sub.add_parser("probe")
    native.add_argument("--identity", type=Path, required=True)
    native.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    combined = sub.add_parser("aggregate")
    combined.add_argument("--receipts", type=Path, required=True)
    combined.add_argument("--identity", type=Path, required=True)
    for item in (matrix, producer, native, combined):
        item.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Output already exists")
    if args.mode == "matrix":
        result = runner_matrix(args.variant, args.cuda_runner)
    elif args.mode == "identity":
        result = identity(args)
    elif args.mode == "probe":
        result = probe(json.loads(args.identity.read_text()), args.arch)
    else:
        result = aggregate(args.receipts, json.loads(args.identity.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Docker {args.mode} passed: {args.output}")


if __name__ == "__main__":
    main()
