#!/usr/bin/env python3
"""Run a reviewed release-helper DAG without repeating uncertain mutations."""

import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone


class DriverError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise DriverError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_plan(path, plan=None):
    path = Path(path).resolve()
    if plan is None:
        plan = json.loads(path.read_text())
    require(plan.get("schema") == 1, "Unsupported plan schema")
    release = plan.get("release", {})
    require(
        re.fullmatch(r"\d+\.\d+\.\d+(?:-rc[1-9]\d*)?", release.get("version", "")),
        "A concrete release version is required",
    )
    for field in ("source_sha", "tools_sha"):
        require(
            re.fullmatch(r"[0-9a-f]{40}", release.get(field, "")),
            f"Full {field} is required",
        )
    require(
        isinstance(plan.get("files"), dict) and plan["files"],
        "Pin helper files by path and SHA-256",
    )
    for name, sha in plan["files"].items():
        require(
            isinstance(name, str) and re.fullmatch(r"[0-9a-f]{64}", sha),
            "Invalid file pin",
        )
    steps = plan.get("steps")
    require(isinstance(steps, list) and steps, "At least one step is required")
    ids = set()
    for step in steps:
        name = step.get("id", "")
        require(
            re.fullmatch(r"[a-z][a-z0-9-]*", name) and name not in ids,
            "Step IDs must be unique lowercase names",
        )
        ids.add(name)
        require(step.get("kind") in ("check", "action"), f"{name}: invalid kind")
        argv = step.get("argv")
        require(
            isinstance(argv, list)
            and argv
            and argv[0]
            and all(isinstance(arg, str) and "\0" not in arg for arg in argv),
            f"{name}: argv must be an argument list",
        )
        require(
            isinstance(step.get("needs", []), list)
            and all(isinstance(x, str) for x in step.get("needs", [])),
            f"{name}: invalid dependencies",
        )
        require(isinstance(step.get("cwd", "."), str), f"{name}: invalid cwd")
        require(
            (path.parent / step.get("cwd", ".")).is_dir(), f"{name}: cwd is missing"
        )
    by_id = {step["id"]: step for step in steps}
    visited, visiting = set(), set()

    def visit(name):
        require(name in by_id, f"Unknown dependency: {name}")
        require(name not in visiting, f"Dependency cycle at {name}")
        if name in visited:
            return
        visiting.add(name)
        for dependency in by_id[name].get("needs", []):
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in ids:
        visit(name)
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return path, plan, fingerprint


def prepare_ga(path, version, source_sha, tools_sha):
    """Replace the copied GA observer launch dictionary for a fresh release."""
    path = path.resolve()
    require(not path.exists(), "Plan exists; inspect it rather than preparing another")
    require(
        not (path.parent / ".release-driver").exists(),
        "Existing state must be reconciled",
    )
    require(
        not list(path.parent.glob("*-dispatch.json"))
        and not (path.parent / "completion.json").exists(),
        "Existing operations must be reconciled; prepare only before the first dispatch",
    )
    stages = [
        ("start", "qualify_and_start_ga.py", []),
        ("promotions", "watch_promotions.py", ["start"]),
        ("distributions", "watch_distributions.py", ["start"]),
        ("cli", "finish_cli_acceptance.py", ["distributions"]),
        ("mobile", "finish_mobile_acceptance.py", ["distributions"]),
        ("authority", "start_authority_when_ready.py", ["promotions", "cli", "mobile"]),
        ("website", "website_after_publication.py", ["authority"]),
        ("readback", "postpublication_readback.py", ["authority"]),
        ("public-bytes", "public_bytes_after_publication.py", ["authority"]),
        ("seal", "finish_ga.py", ["website", "readback", "public-bytes", "mobile"]),
    ]
    for _, script, _ in stages:
        require((path.parent / script).is_file(), f"Missing GA helper: {script}")
    operations = path.parent / "release_ops.py"
    require(operations.is_file(), "Missing GA helper: release_ops.py")
    constants = {}
    for node in ast.parse(operations.read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    for name, expected in [
        ("SOURCE", source_sha),
        ("TOOLS", tools_sha),
        ("VERSION", version),
    ]:
        require(
            constants.get(name) == expected,
            f"release_ops.py {name} does not match the plan",
        )
    files = {
        file.name: digest(file)
        for file in sorted(path.parent.iterdir())
        if file.is_file() and file.suffix in (".py", ".mjs", ".sh")
    }
    plan = {
        "schema": 1,
        "release": {
            "version": version,
            "source_sha": source_sha,
            "tools_sha": tools_sha,
        },
        "files": files,
        "steps": [
            {
                "id": name,
                "kind": "action",
                "needs": needs,
                "argv": ["{python}", "-u", script],
            }
            for name, script, needs in stages
        ],
    }
    load_plan(path, plan)
    # Exclusive creation prevents accidental replacement of another operator's recipe.
    with path.open("x") as stream:
        json.dump(plan, stream, indent=2)
        stream.write("\n")
    return plan


def verify_files(path, plan):
    for name, sha in plan["files"].items():
        file = path.parent / name
        require(
            file.is_file() and digest(file) == sha, f"Pinned helper changed: {name}"
        )


def save(path, value):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def read_state(state_path, fingerprint, plan_path):
    state = json.loads(state_path.read_text())
    require(
        state.get("plan_sha256") == fingerprint,
        "Plan changed; reconcile the original state",
    )
    require(state.get("schema") == 1, "Unsupported state schema")
    require(
        state.get("plan_path") == str(plan_path),
        "Another plan owns this release directory",
    )
    return state


def process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def execute(path, plan, fingerprint, jobs, interval):
    # The state location is derived from the plan, never the caller's cwd.
    directory = path.parent / ".release-driver"
    directory.mkdir(exist_ok=True)
    with (directory / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DriverError("Another driver owns this plan") from None
        verify_files(path, plan)
        state_path = directory / "state.json"
        if state_path.exists():
            state = read_state(state_path, fingerprint, path)
        else:
            require(
                not [p for p in directory.iterdir() if p.name != "lock"],
                "State is missing; reconcile retained logs instead of repeating actions",
            )
            state = {
                "schema": 1,
                "plan_sha256": fingerprint,
                "plan_path": str(path),
                "release": plan["release"],
                "steps": {
                    s["id"]: {"status": "pending", "attempts": []}
                    for s in plan["steps"]
                },
            }
        for step in plan["steps"]:
            record = state["steps"][step["id"]]
            if step["kind"] == "action":
                require(
                    record["status"] in ("pending", "success"),
                    f"{step['id']}: {record['status']} action requires reconciliation; not retried",
                )
            else:
                if record["status"] == "running":
                    require(
                        not process_alive(record["attempts"][-1].get("pid")),
                        f"{step['id']}: previous helper may still be running",
                    )
                record["status"] = "pending"
        save(state_path, state)
        return schedule(path, plan, directory, state, state_path, jobs, interval)


def schedule(path, plan, directory, state, state_path, jobs, interval):
    children = {}
    try:
        while True:
            for name, child in list(children.items()):
                code = child.poll()
                if code is None:
                    continue
                record = state["steps"][name]
                record["status"] = "success" if code == 0 else "failed"
                record["attempts"][-1].update(exit_code=code, finished_at=now())
                save(state_path, state)
                del children[name]
                print(f'{name}: {record["status"]}', flush=True)
            if any(r["status"] == "failed" for r in state["steps"].values()):
                return 1
            for step in plan["steps"]:
                if len(children) >= jobs:
                    break
                name = step["id"]
                record = state["steps"][name]
                if record["status"] != "pending" or not all(
                    state["steps"][dep]["status"] == "success"
                    for dep in step.get("needs", [])
                ):
                    continue
                verify_files(path, plan)
                operation = str(uuid.uuid4())
                log_path = directory / f"{name}.{operation}.log"
                attempt = {
                    "operation_id": operation,
                    "started_at": now(),
                    "log": str(log_path),
                }
                record["attempts"].append(attempt)
                record["status"] = "running"
                # A crash after this durable intent is an uncertain action, not permission to repeat.
                save(state_path, state)
                with log_path.open("xb") as stream:
                    argv = [
                        sys.executable if arg == "{python}" else arg
                        for arg in step["argv"]
                    ]
                    env = dict(
                        os.environ,
                        RELEASE_DRIVER_OPERATION_ID=operation,
                        RELEASE_DRIVER_SOURCE_SHA=plan["release"]["source_sha"],
                        RELEASE_DRIVER_TOOLS_SHA=plan["release"]["tools_sha"],
                        RELEASE_DRIVER_VERSION=plan["release"]["version"],
                    )
                    child = subprocess.Popen(
                        argv,
                        cwd=path.parent / step.get("cwd", "."),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                # Popen has its own descriptor; retain no parent write handle.
                children[name] = child
                attempt["pid"] = child.pid
                save(state_path, state)
                print(f"{name}: started; log={log_path}", flush=True)
            if all(r["status"] == "success" for r in state["steps"].values()):
                return 0
            require(children, "No runnable steps remain")
            time.sleep(interval)
    finally:
        for child in children.values():
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for name, child in children.items():
            try:
                code = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                code = child.wait()
            record = state["steps"][name]
            record["status"] = "interrupted"
            record["attempts"][-1].update(exit_code=code, finished_at=now())
        save(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare-ga", "run", "status"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--version")
    parser.add_argument("--source-sha")
    parser.add_argument("--tools-sha")
    args = parser.parse_args()
    try:
        require(1 <= args.jobs <= 8, "--jobs must be between 1 and 8")
        if args.command == "prepare-ga":
            require(
                args.version and args.source_sha and args.tools_sha,
                "prepare-ga requires --version, --source-sha and --tools-sha",
            )
            plan = prepare_ga(args.plan, args.version, args.source_sha, args.tools_sha)
            print(
                f'Prepared {len(plan["steps"])} steps; no release operation was triggered'
            )
            return 0
        path, plan, fingerprint = load_plan(args.plan)
        if args.command == "status":
            state_path = path.parent / ".release-driver" / "state.json"
            if state_path.parent.exists() and not state_path.exists():
                require(
                    not [p for p in state_path.parent.iterdir() if p.name != "lock"],
                    "State is missing; reconcile retained logs",
                )
            state = (
                read_state(state_path, fingerprint, path)
                if state_path.exists()
                else {
                    "release": plan["release"],
                    "steps": {
                        s["id"]: {"status": "not-started"} for s in plan["steps"]
                    },
                }
            )
            print(json.dumps(state, indent=2))
            return 0
        signal.signal(
            signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
        )
        return execute(path, plan, fingerprint, args.jobs, 0.1)
    except KeyboardInterrupt:
        print(
            "Interrupted; retain state and reconcile any action before resuming",
            file=sys.stderr,
        )
        return 130
    except (DriverError, OSError, ValueError, TypeError, KeyError) as error:
        print(f"Release driver stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
