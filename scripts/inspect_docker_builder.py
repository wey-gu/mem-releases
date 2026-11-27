"""Require the CPU BuildKit container to leave resources for its hosted runner."""

import argparse
import json
from pathlib import Path
import re
import subprocess


GIB = 1024 ** 3
EXPECTED_LIMITS = {
    "Memory": 12 * GIB,
    "MemorySwap": 24 * GIB,
    "CpuPeriod": 100000,
    "CpuQuota": 200000,
}


def verify(builder, containers):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", builder):
        raise ValueError("Invalid BuildKit builder name")
    if len(containers) != 1:
        raise ValueError("Expected exactly one native BuildKit container")
    container = containers[0]
    if container.get("Name") != f"/buildx_buildkit_{builder}0":
        raise ValueError("BuildKit container identity does not match")
    limits = {key: container.get("HostConfig", {}).get(key) for key in EXPECTED_LIMITS}
    if limits != EXPECTED_LIMITS:
        raise ValueError(f"CPU BuildKit limits do not match: {limits}")
    state = container.get("State", {})
    if state.get("Running") is not True or state.get("OOMKilled") is not False:
        raise ValueError("CPU BuildKit is not running cleanly")
    return {
        "container_id": container["Id"],
        "container_name": container["Name"],
        "image_id": container["Image"],
        "limits": limits,
        "running": True,
        "oom_killed": False,
    }


def inspect(builder, receipt, command=subprocess.check_output):
    record = {"passed": False, "builder": builder}
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", builder):
            raise ValueError("Invalid BuildKit builder name")
        raw = command(["docker", "inspect", f"buildx_buildkit_{builder}0"],
                      text=True, timeout=30)
        record.update(verify(builder, json.loads(raw)), passed=True)
    except Exception as error:
        record.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--builder", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    inspect(args.builder, args.receipt)
    print("PASS: CPU BuildKit has bounded memory, swap and CPU")
