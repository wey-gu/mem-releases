#!/usr/bin/env python3
"""Supply bounded virtual memory on disposable hosted Linux Docker builders."""

import argparse
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile


GIB = 1024 ** 3
TARGET_BYTES = 32 * GIB
DISK_RESERVE_BYTES = 20 * GIB


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def admit(env):
    require(env.get("GITHUB_ACTIONS") == "true"
            and env.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and env.get("RUNNER_OS") == "Linux"
            and platform.system() == "Linux",
            "Resource preparation requires a disposable GitHub-hosted Linux runner")
    root = Path(env["RUNNER_TEMP"])
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(), "Invalid runner temp directory")
    return root


def memory_bytes(text):
    values = {}
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] in ("MemTotal:", "SwapTotal:"):
            require(len(fields) == 3 and fields[1].isdigit() and fields[2] == "kB",
                    "Malformed Linux memory inventory")
            require(fields[0] not in values, "Duplicate Linux memory field")
            values[fields[0]] = int(fields[1]) * 1024
    require(set(values) == {"MemTotal:", "SwapTotal:"} and values["MemTotal:"] > 0,
            "Missing Linux memory inventory")
    return {"ram_bytes": values["MemTotal:"], "swap_bytes": values["SwapTotal:"]}


def snapshot(root):
    inventory = Path("/proc/meminfo").read_text()
    result = memory_bytes(inventory)
    result["meminfo"] = inventory
    result["disk_free_bytes"] = shutil.disk_usage(root).free
    return result


def allocation_bytes(resources):
    deficit = max(0, TARGET_BYTES - resources["ram_bytes"] - resources["swap_bytes"])
    return math.ceil(deficit / GIB) * GIB


def verify(before, after, allocated):
    require(after["ram_bytes"] + after["swap_bytes"] >= TARGET_BYTES,
            "Virtual memory target was not reached")
    # mkswap reserves a small header; pre-existing swap must remain available.
    require(after["swap_bytes"] >= before["swap_bytes"] + allocated - 1024 * 1024,
            "Allocated swap was not activated or existing swap was lost")
    require(after["disk_free_bytes"] >= DISK_RESERVE_BYTES, "Docker disk reserve was consumed")


def prepare(env, receipt, observe=snapshot, command=subprocess.run):
    record = {"schema": 1, "passed": False, "target_bytes": TARGET_BYTES,
              "disk_reserve_bytes": DISK_RESERVE_BYTES, "commands": []}

    def run(args):
        record["commands"].append([str(arg) for arg in args])
        command(args, check=True, timeout=180)

    try:
        root = admit(env)
        record.update(architecture=platform.machine(), run_id=env.get("GITHUB_RUN_ID"),
                      run_attempt=env.get("GITHUB_RUN_ATTEMPT"), job=env.get("GITHUB_JOB"))
        before = observe(root)
        record["before"] = before
        allocated = allocation_bytes(before)
        record["allocated_bytes"] = allocated
        required_disk = allocated + DISK_RESERVE_BYTES
        require(observe(root)["disk_free_bytes"] >= required_disk,
                "Insufficient disk for bounded swap and Docker reserve")
        if allocated:
            directory = Path(tempfile.mkdtemp(prefix="nmem-docker-swap-", dir=root))
            swap = directory / "swapfile"
            # Exclusive creation prevents clobbering an existing swap file.
            fd = os.open(swap, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            record["swapfile"] = str(swap)
            run(["fallocate", "--length", str(allocated), str(swap)])
            run(["mkswap", str(swap)])
            run(["sudo", "swapon", str(swap)])
        record["after"] = observe(root)
        verify(before, record["after"], allocated)
        record["passed"] = True
    except BaseException as error:
        record.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    prepare(os.environ, args.receipt)
    print("PASS: Docker builder virtual memory and disk reserve")
