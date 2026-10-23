#!/usr/bin/env python3
"""Exercise historical-root custody with an original packaged native server.

This supplements, and never replaces, the six-platform App/server/CLI gate.
All stores are synthetic and isolated. A truncated final WAL transaction is
not a simulation of arbitrary corruption or physical power loss.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request

from verify_packaged_desktop import bundled_server, command, installed_app, require
from verify_packaged_desktop import runtime_is_ready, validate_server_output


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LEASE = ".nmem-app-data-lifecycle-v1.lock"
HTTP_EVENTS = []


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree(root, graph_only=False):
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"Aliased fixture member: {path}")
        if path.is_dir():
            continue
        require(path.is_file(), f"Nonregular fixture member: {path}")
        relative = path.relative_to(root).as_posix()
        if path.name == LEASE:
            require(path.stat().st_size == 0, "Nonempty lifecycle lease")
            continue
        if graph_only and not relative.startswith("nowledge_graph"):
            continue
        result[relative] = {"size": path.stat().st_size, "sha256": digest(path)}
    return result


def request(origin, method, route, body=None, missing_ok=False):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    query = urllib.request.Request(origin + route, data=data, headers=headers, method=method)
    try:
        response = OPENER.open(query, timeout=10)
    except urllib.error.HTTPError as error:
        raw = error.read()
        HTTP_EVENTS.append({"origin": origin, "method": method, "route": route, "status": error.code,
                            "body_base64": base64.b64encode(raw).decode()})
        if missing_ok and method == "GET" and route.startswith("/memories/") and error.code == 404:
            payload = json.loads(raw)
            require(isinstance(payload, dict) and payload.get("detail"), "Malformed missing-Memory response")
            return None
        raise
    with response:
        raw = response.read()
        HTTP_EVENTS.append({"origin": origin, "method": method, "route": route, "status": response.status,
                            "body_base64": base64.b64encode(raw).decode()})
        require(response.status in (200, 201), f"Unexpected HTTP status for {route}")
        payload = json.loads(raw)
    require(isinstance(payload, dict), f"Non-object JSON for {route}")
    return payload


def retained_records(expected, observed):
    """The final Memory may be torn; earlier committed Graph records may not."""
    require(len(expected) == 10 and len({x["id"] for x in expected}) == 10, "Invalid seed identity set")
    require(set(observed).issubset({x["id"] for x in expected}), "Unexpected restored identity")
    for index, item in enumerate(expected):
        actual = observed.get(item["id"])
        if index < 9:
            require(actual is not None, f"Checkpointed/earlier WAL Memory missing: {item['id']}")
        if actual is not None:
            require(actual == item, f"Graph Memory changed: {item['id']}")
    return sorted(observed)


def collect_records(origin):
    """Enumerate the Graph-backed all-state/all-Space list, never expected IDs."""
    observed = {}
    offset, total = 0, None
    while True:
        page = request(origin, "GET", f"/memories?state=all&space_id=all&limit=100&offset={offset}")
        pagination, items = page.get("pagination"), page.get("memories")
        require(isinstance(pagination, dict) and isinstance(items, list), "Malformed Graph list")
        count = pagination.get("total")
        require(type(count) is int and 0 <= count <= 1000, "Unbounded or invalid synthetic Graph count")
        require(total is None or total == count, "Graph changed during enumeration")
        total = count
        require(pagination.get("offset") == offset and pagination.get("limit") == 100, "Wrong Graph page")
        require(len(items) == min(100, total - offset), "Incomplete Graph page")
        require(type(pagination.get("has_more")) is bool and pagination["has_more"] == (offset + len(items) < total),
                "Inconsistent Graph pagination")
        for value in items:
            require(isinstance(value, dict), "Malformed Graph Memory")
            item = {key: value.get(key) for key in ("id", "title", "content")}
            require(all(isinstance(x, str) and x for x in item.values()), "Incomplete Graph identity/content")
            require(item["id"] not in observed, "Duplicate Graph identity across pages")
            observed[item["id"]] = item
        offset += len(items)
        if not pagination["has_more"]:
            require(len(observed) == total, "Incomplete Graph enumeration")
            return observed


def path_identity(value):
    return os.path.normcase(os.path.realpath(value)).removeprefix("\\\\?\\")


def recovery_notice(details, current, historical):
    notice = details.get("db_recovery_notice")
    require(isinstance(notice, dict), "Missing durable recovery notice")
    require(notice.get("reason") == "malformed_wal", "Fixture did not trigger typed malformed WAL")
    require(notice.get("recovery_method") == "verified_prefix", "Verified-prefix recovery was not used")
    require(path_identity(notice.get("chosen_db_path", "")) == path_identity(current / "nowledge_graph_v2.db"),
            "Recovery notice selects another graph")
    paths = notice.get("historical_root_paths", [])
    require(isinstance(paths, list), "Malformed historical path list")
    require([path_identity(x) for x in paths] == [path_identity(x) for x in historical],
            "Recovery notice does not name the exact historical roots")
    return notice


def environment(directory):
    home = directory / "home"
    current = home / ("AppData/Local/NowledgeGraph" if os.name == "nt" else "Documents/NowledgeGraph")
    historical = home / ("AppData/Roaming/NowledgeGraph" if os.name == "nt" else "Library/Application Support/NowledgeGraph")
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update({"LANG": "C.UTF-8", "HOME": str(home), "USERPROFILE": str(home),
                "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
                "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_CACHE_HOME": str(home / ".cache"), "XDG_STATE_HOME": str(home / ".local/state"),
                "TMPDIR": str(directory / "tmp"), "TEMP": str(directory / "tmp"), "TMP": str(directory / "tmp"),
                "NMEM_APP_DATA": str(current), "NMEM_APP_CONFIG_DIR": str(directory / "config"),
                "NMEM_CLI_CONFIG_DIR": str(directory / "cli"), "NMEM_AI_NOW_HOME": str(directory / "ai-now"),
                "NMEM_MODEL_CACHE_DIR": str(directory / "models"), "NMEM_LOCAL_EMBED_WARMUP": "0",
                "NMEM_SERVER_ADDR": "127.0.0.1:0", "NMEM_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS": "10",
                "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost"})
    for path in (home, current, directory / "tmp", directory / "config", directory / "cli", directory / "ai-now", directory / "models"):
        path.mkdir(parents=True, exist_ok=True)
    return env, current, historical


def owned_ports(pid):
    if os.name == "nt":
        script = ("$ErrorActionPreference = 'Stop'; "
                  "$connections = @(Get-NetTCPConnection -State Listen -ErrorAction Stop); "
                  f"$ports = @($connections | Where-Object {{ $_.OwningProcess -eq {pid} -and $_.LocalAddress -eq '127.0.0.1' }} | "
                  "Select-Object -ExpandProperty LocalPort | Sort-Object -Unique); "
                  "ConvertTo-Json -InputObject $ports -Compress")
        output = command(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=10)
        value = json.loads(output) if output else []
        values = value if isinstance(value, list) else [value]
        require(all(type(x) is int and 0 < x < 65536 for x in values), "Malformed owned listener result")
        return sorted(set(values))
    result = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
                            capture_output=True, text=True, timeout=5)
    require(result.returncode in (0, 1), "Owned listener inspection failed")
    return sorted({int(x) for x in re.findall(r"^n127\.0\.0\.1:(\d+)$", result.stdout, re.MULTILINE)})


class Server:
    def __init__(self, binary, directory, identity, record):
        self.identity = identity
        self.env, self.current, self.historical = environment(directory)
        self.log_path = directory / f"server-{len(record['processes'])}.log"
        self.log = self.log_path.open("xb")
        try:
            self.process = subprocess.Popen([str(binary)], cwd=directory, env=self.env, stdout=self.log,
                                            stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        except BaseException:
            self.log.close()
            raise
        self.evidence = {"pid": self.process.pid, "log": str(self.log_path), "reaped": False}
        record["processes"].append(self.evidence)
        self.origin = None

    def wait(self, state):
        deadline = time.monotonic() + 120
        last = "No owned listener"
        while time.monotonic() < deadline:
            require(self.process.poll() is None, f"Server exited before {state}: {self.log_path}")
            ports = owned_ports(self.process.pid)
            require(len(ports) <= 1, "Ambiguous owned listeners")
            if ports:
                self.origin = f"http://127.0.0.1:{ports[0]}"
                try:
                    health = request(self.origin, "GET", "/health")
                    last = str(health)
                    self.evidence["last_health"] = health
                    if health.get("status") == state:
                        self.evidence.update(origin=self.origin, health=health)
                        if state == "recovery_required":
                            require(health.get("reason") == "conflict", "Wrong parked reason")
                            return health
                        details = request(self.origin, "GET", "/health/details")
                        require(details.get("version") == self.identity.version, "Runtime version mismatch")
                        require(details.get("build_sha") == self.identity.source_sha, "Runtime source mismatch")
                        if runtime_is_ready(details):
                            self.evidence["details"] = details
                            return details
                except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                    last = str(error)
            time.sleep(0.2)
        raise RuntimeError(f"Readiness deadline: {state}, {last}")

    def read(self, item):
        value = request(self.origin, "GET", "/memories/" + item["id"], missing_ok=True)
        if value is None:
            return None
        node = value.get("memory", value)
        actual = {key: node.get(key) for key in ("id", "title", "content")}
        require(actual == item, "Graph readback differs from committed Memory")
        return actual

    def create(self, title, content):
        value = request(self.origin, "POST", "/memories", {"title": title, "content": content, "importance": 0.4})
        identifier = value.get("id") or value.get("memory", {}).get("id")
        require(isinstance(identifier, str) and re.fullmatch(r"[0-9a-f-]{36}", identifier), "Missing seed Memory identity")
        item = {"id": identifier, "title": title, "content": content}
        require(self.read(item) == item, "Committed seed Memory unavailable")
        return item

    def records(self):
        observed = collect_records(self.origin)
        for item in observed.values():
            require(self.read(item) == item, "Graph list/by-ID disagreement")
        return observed

    def checkpoint(self):
        response = request(self.origin, "POST", "/health/checkpoint", {})
        require(response.get("success") is True and response.get("checkpointed") is True, "Checkpoint did not complete")
        self.evidence["explicit_checkpoint"] = response

    def finish(self, mode="clean"):
        require(self.process.poll() is None, "Process exited before owned stop")
        if mode == "clean":
            response = request(self.origin, "POST", "/health/shutdown", {})
            self.evidence["shutdown_response"] = response
            self.process.wait(timeout=60)
            require(self.process.returncode == 0, "Shutdown did not exit successfully")
        else:
            require(mode in ("crash_seed", "stop_parked"), "Unknown stop mode")
            self.process.kill()
            self.process.wait(timeout=15)
            expected = 1 if os.name == "nt" else -signal.SIGKILL
            require(self.process.returncode == expected, "Owned hard stop identity mismatch")
        self.evidence.update(stop_mode=mode, exit_code=self.process.returncode, reaped=True)
        self.log.close()
        self.evidence["log_sha256"] = digest(self.log_path)

    def cleanup_failure(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=15)
            self.evidence["failure_cleanup_forced"] = True
        self.evidence.update(exit_code=self.process.returncode, reaped=True)
        self.log.close()
        self.evidence["log_sha256"] = digest(self.log_path)


def run(args):
    require(args.platform in ("mac", "win"), "Only native macARM and Windows are admitted")
    require((args.platform == "win") == (os.name == "nt"), "Wrong native operating system")
    if args.platform == "mac":
        require(platform.system() == "Darwin" and platform.machine().lower() == "arm64", "Native Apple Silicon required")
    require(re.fullmatch(r"[0-9a-f]{40}", args.source_sha), "Full source SHA required")
    require(re.fullmatch(rf"v{re.escape(args.version)}-rc[1-9][0-9]*", args.release_tag), "RC identity mismatch")
    require(re.fullmatch(r"[0-9a-f]{40}", args.build_workflow_sha), "Full producer SHA required")
    require(all(re.fullmatch(r"[1-9][0-9]*", x) for x in (args.build_run_id, args.build_attempt, args.artifact_id)),
            "Concrete producer run, attempt and artifact IDs required")
    require(not args.receipt.exists(), "Preserve the prior receipt")
    args.work.mkdir(parents=True, exist_ok=False)
    HTTP_EVENTS.clear()
    record = {"schema": 1, "passed": False, "platform": args.platform, "version": args.version,
              "source_sha": args.source_sha, "package_sha256": digest(args.package),
              "package": str(args.package), "verifier_source_sha": command(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent),
              "verifier_script_sha256": digest(Path(__file__)), "started_at": datetime.now(timezone.utc).isoformat(),
              "release_tag": args.release_tag, "build_run_id": args.build_run_id,
              "build_workflow_sha": args.build_workflow_sha, "build_attempt": args.build_attempt,
              "artifact_id": args.artifact_id,
              "processes": [], "cases": [], "http_events": HTTP_EVENTS,
              "coverage": "Packaged headless Graph recovery, historical preservation and current-source refusal; not Desktop UI or physical power loss"}
    active = None

    def save():
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(record, indent=2) + "\n")

    save()
    try:
        app, resources, _ = installed_app(args, args.work)
        app_output = command([*app, "--version"], timeout=30)
        require(app_output == f"Nowledge Mem {args.version} ({args.platform})", "Original App identity mismatch")
        record.update(app_command=[str(x) for x in app], app_output=app_output)
        binary = bundled_server(resources, args.platform)
        record.update(server=str(binary), server_sha256=digest(binary))
        output = command([binary, "--version"], timeout=30)
        validate_server_output(output, args.version, args.source_sha)
        record["server_output"] = output
        seed = args.work / "seed"
        seed.mkdir()
        active = Server(binary, seed, args, record)
        active.wait("ok")
        expected = [active.create(f"checkpointed-{i}", f"checkpointed content {i}") for i in range(6)]
        active.checkpoint()
        for item in expected:
            require(active.read(item) == item, "Checkpointed read mismatch")
        expected.extend(active.create(f"wal-{i}", f"committed WAL content {i}") for i in range(4))
        active.finish("crash_seed")
        current_seed = active.current
        active = None
        wal = current_seed / "nowledge_graph_v2.db.wal"
        require(wal.is_file() and wal.stat().st_size > 5, "No WAL to truncate")
        intact = args.work / "frozen-intact"
        shutil.copytree(current_seed, intact)
        frozen = args.work / "frozen-current"
        shutil.copytree(intact, frozen)
        torn = frozen / wal.name
        raw_wal = {"size": torn.stat().st_size, "sha256": digest(torn)}
        with torn.open("r+b") as stream:
            stream.truncate(raw_wal["size"] - 5)
            stream.flush()
            os.fsync(stream.fileno())
        record.update(expected_records=expected, wal_before=raw_wal, frozen_current=tree(frozen),
                      frozen_intact=tree(intact), input_controls=[])
        for kind, expected_items in (("main_only", expected[:6]), ("intact_wal", expected)):
            directory = args.work / kind
            _, current, _ = environment(directory)
            if kind == "main_only":
                shutil.copyfile(intact / "nowledge_graph_v2.db", current / "nowledge_graph_v2.db")
                version = intact / "db_version.json"
                if version.is_file():
                    shutil.copyfile(version, current / version.name)
            else:
                shutil.copytree(intact, current, dirs_exist_ok=True)
            control = {"kind": kind, "passed": False}
            record["input_controls"].append(control)
            active = Server(binary, directory, args, record)
            details = active.wait("ok")
            require(not details.get("db_recovery_notice"), "Unmodified input already required recovery")
            observed = active.records()
            require(observed == {x["id"]: x for x in expected_items}, "Invalid WAL-only fixture: main must contain6, intact WAL10")
            active.finish()
            active = None
            control.update(passed=True, graph_records=observed)
            save()
        require(tree(intact) == record["frozen_intact"], "Frozen intact input changed")

        legacy_seed = args.work / "historical-seed"
        legacy_seed.mkdir()
        active = Server(binary, legacy_seed, args, record)
        active.wait("ok")
        historical_item = active.create("historical-only", "Historical data must not enter the current graph")
        active.checkpoint()
        active.finish()
        historical_source = active.current / "nowledge_graph_v2.db"
        frozen_historical = args.work / "frozen-historical"
        frozen_historical.mkdir()
        shutil.copyfile(historical_source, frozen_historical / "nowledge_graph_v1.db")
        active = None
        record.update(historical_item=historical_item, frozen_historical=tree(frozen_historical),
                      historical_fixture_contract="Checkpointed synthetic current-format main renamed v1 with no version record, matching source A2 fixture")
        save()
        control_records = None
        for kind in ("single", "historical", "current_named_v1"):
            directory = args.work / kind
            env, current, historical = environment(directory)
            shutil.copytree(frozen, current, dirs_exist_ok=True)
            if kind != "single":
                shutil.copytree(frozen_historical, historical)
            if kind == "current_named_v1":
                (historical / "db_version.json").write_text(json.dumps({"db_version": 2, "db_codename": "cedar", "app_version": "synthetic-current-control"}))
            before_current = tree(current, graph_only=True)
            before_historical = tree(historical) if historical.exists() else {}
            case = {"kind": kind, "passed": False, "current_before": before_current, "historical_before": before_historical}
            record["cases"].append(case)
            active = Server(binary, directory, args, record)
            if kind == "current_named_v1":
                health = active.wait("recovery_required")
                for _ in range(2):
                    require(request(active.origin, "GET", "/health") == health, "Parking response changed")
                active.finish("stop_parked")
                require(tree(current, graph_only=True) == before_current, "Refused current graph changed")
            else:
                details = active.wait("ok")
                case["notice"] = recovery_notice(details, current, [] if kind == "single" else [historical])
                observed = active.records()
                case["retained_ids"] = retained_records(expected, observed)
                require(active.read(historical_item) is None, "Historical Memory selected or merged")
                if kind == "single":
                    control_records = observed
                else:
                    require(observed == control_records, "Historical root changed recovered Graph data")
                active.finish()
                active = None
                active = Server(binary, directory, args, record)
                restarted = active.wait("ok")
                recovery_notice(restarted, current, [] if kind == "single" else [historical])
                again = active.records()
                require(again == observed, "Recovered Graph changed after restart")
                require(active.read(historical_item) is None, "Historical Memory appeared after restart")
                active.finish()
            active = None
            require((tree(historical) if historical.exists() else {}) == before_historical, "Historical root changed")
            case.update(passed=True, historical_after=tree(historical) if historical.exists() else {})
            save()
        require(len(record["cases"]) == 3 and all(x["passed"] for x in record["cases"]), "Incomplete recovery coverage")
        require(len(record["input_controls"]) == 2 and all(x["passed"] for x in record["input_controls"]), "WAL-only input not established")
        require(all(x["reaped"] and not x.get("failure_cleanup_forced") for x in record["processes"]), "Incomplete native teardown")
        record["passed"] = True
    except BaseException as error:
        record.update(failure_type=type(error).__name__, failure=str(error))
        raise
    finally:
        try:
            if active is not None:
                active.cleanup_failure()
        except BaseException as error:
            record.update(passed=False, cleanup_failure_type=type(error).__name__, cleanup_failure=str(error))
            raise
        finally:
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            save()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=lambda x: Path(x).resolve(), required=True)
    parser.add_argument("--app", type=lambda x: Path(x).resolve())
    parser.add_argument("--platform", choices=("mac", "win"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--build-run-id", required=True)
    parser.add_argument("--build-workflow-sha", required=True)
    parser.add_argument("--build-attempt", required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--work", type=lambda x: Path(x).resolve(), required=True)
    parser.add_argument("--receipt", type=lambda x: Path(x).resolve(), required=True)
    args = parser.parse_args()
    run(args)
    print("PASS: original packaged Graph recovery and custody")


if __name__ == "__main__":
    main()
