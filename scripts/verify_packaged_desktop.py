#!/usr/bin/env python3
"""Execute one installed Desktop package; write a receipt only after all gates pass.

Run only on a disposable native CI runner with an empty Nowledge Mem profile.
The workflow installs DEB/RPM/NSIS packages; this script extracts DMG/AppImage.
No source build, replacement binary, or injected backend is accepted.
"""

import argparse
import hashlib
import json
import ntpath
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


PLATFORMS = ("mac", "mac_intel", "win", "linux-deb", "linux-rpm", "linux-appimage")
PLATFORM_ARTIFACTS = {
    "mac": "macos-arm64", "mac_intel": "macos-x86_64", "win": "windows-x86_64",
    "linux-deb": "linux-deb-appimage", "linux-appimage": "linux-deb-appimage", "linux-rpm": "linux-rpm",
}
VERSION = re.compile(r"[1-9][0-9]*\.[0-9]+\.[0-9]+|0\.[1-9][0-9]*\.[0-9]+|0\.0\.[1-9][0-9]*")
WINDOWS_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Nowledge Mem"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(argv, *, env=None, cwd=None, timeout=30):
    result = subprocess.run(
        [str(arg) for arg in argv], env=env, cwd=cwd, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="strict", check=True,
    )
    return result.stdout.strip()


def exact_version(output, program, expected):
    first_line = output.splitlines()[0] if output else ""
    if program != "nmem-server":
        require(first_line == output, f"Unexpected extra {program} output: {output!r}")
    match = re.fullmatch(rf"{re.escape(program)} ([0-9]+\.[0-9]+\.[0-9]+)(?: \([^\r\n]+\))?", first_line)
    require(match is not None, f"Malformed {program} output: {output!r}")
    observed = match.group(1)
    require(VERSION.fullmatch(observed) is not None and observed == expected,
            f"{program} version {observed!r} != {expected!r}")
    return observed


def unique(paths, description):
    paths = list(paths)
    require(len(paths) == 1, f"Expected one {description}; found {paths}")
    return paths[0]


def windows_install_location(record):
    return ntpath.normcase(ntpath.normpath(record.get("InstallLocation", "").strip('"')))


def validate_windows_package_record(record, app, version):
    require(record.get("DisplayName") == "Nowledge Mem", "Wrong installed Windows product")
    require(record.get("DisplayVersion") == version and VERSION.fullmatch(version) is not None,
            f"Installed Windows version mismatch: {record.get('DisplayVersion')!r}")
    require(bool(record.get("InstallLocation"))
            and windows_install_location(record) == ntpath.normcase(ntpath.dirname(str(app))),
            "Windows package registration belongs to another install location")
    require(record.get("MainBinaryName", "").casefold() == ntpath.basename(str(app)).casefold(),
            "Windows package registration names another executable")


def windows_package_registration(app, version, key=WINDOWS_UNINSTALL_KEY):
    import winreg

    records = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            handle = winreg.OpenKey(hive, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            continue
        with handle:
            record = {}
            for name in ("DisplayName", "DisplayVersion", "InstallLocation", "MainBinaryName"):
                try:
                    record[name] = winreg.QueryValueEx(handle, name)[0]
                except FileNotFoundError:
                    pass
        if windows_install_location(record) == ntpath.normcase(ntpath.dirname(str(app))):
            records.append(record)
    record = unique(records, "Windows registration for the executing App")
    validate_windows_package_record(record, app, version)
    return record


def installed_app(args, work):
    package = args.package
    if args.platform in ("mac", "mac_intel"):
        mount = work / "mount"
        mount.mkdir()
        command(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount, package], timeout=90)
        try:
            source = unique(mount.glob("*.app"), "App bundle")
            app_bundle = work / source.name
            command(["ditto", source, app_bundle], timeout=180)
        finally:
            command(["hdiutil", "detach", mount], timeout=60)
        with (app_bundle / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        require(info.get("CFBundleShortVersionString") == args.version, "Bundle version mismatch")
        app = app_bundle / "Contents/MacOS" / info["CFBundleExecutable"]
        return [app], app_bundle / "Contents/Resources", {}
    if args.platform == "linux-appimage":
        package.chmod(package.stat().st_mode | 0o100)
        command([package, "--appimage-extract"], cwd=work, timeout=180)
        appdir = work / "squashfs-root"
        launcher = appdir / "AppRun"
        require(launcher.is_file(), "AppImage has no AppRun")
        # This is the documented extract-and-run path: preserve both identities.
        return [launcher], appdir, {"APPDIR": str(appdir), "APPIMAGE": str(package)}
    require(args.app is not None and args.app.is_file(), "Installed App path is required")
    if args.platform == "linux-deb":
        identity = command(["dpkg-query", "-W", "-f=${Version}", "nowledge-mem"])
        require(identity == args.version, f"Installed DEB version: {identity!r}")
        command(["dpkg-query", "-S", args.app])
        return [args.app], Path("/usr/lib"), {}
    if args.platform == "linux-rpm":
        identity = command(["rpm", "-q", "--qf", "%{VERSION}", "nowledge-mem"])
        require(identity == args.version, f"Installed RPM version: {identity!r}")
        command(["rpm", "-qf", args.app])
        return [args.app], Path("/usr/lib"), {}
    return [args.app], args.app.parent, {}


def bundled_server(resource_root, platform):
    name = "nmem-server.exe" if platform == "win" else "nmem-server"
    if platform in ("linux-deb", "linux-rpm"):
        candidates = []
        for root in (Path("/usr/lib"), Path("/usr/lib64")):
            for package in ("Nowledge Mem", "nowledge-mem"):
                base = root / package
                candidates.extend(base.glob(f"_up_/rust-backend/{name}"))
                candidates.extend(base.glob(f"{name}"))
        # Compatibility symlinks/stubs are not the payload.
        candidates = [p for p in candidates if p.is_file() and p.stat().st_size > 1_000_000]
    else:
        candidates = [p for p in resource_root.rglob(name) if p.is_file() and p.stat().st_size > 1_000_000]
    return unique(candidates, "bundled server payload").resolve()


def cli_command(path):
    if os.name == "nt" and path.suffix.lower() == ".cmd":
        return [os.environ["COMSPEC"], "/d", "/c", str(path), "--version"]
    return [str(path), "--version"]


def terminal_path(env):
    if os.name == "nt":
        import winreg

        values = []
        for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    values.append(winreg.QueryValueEx(handle, "Path")[0])
            except FileNotFoundError:
                # Boot installs wrappers on an owned worker thread. An absent
                # user PATH remains undiscoverable until that write completes.
                values.append("")
        return os.path.expandvars(os.pathsep.join(values))
    output = command(["/bin/bash", "-lc", 'printf "NMEM_TERMINAL_PATH=%s\\n" "$PATH"'], env=env, timeout=10)
    lines = [line.removeprefix("NMEM_TERMINAL_PATH=") for line in output.splitlines()
             if line.startswith("NMEM_TERMINAL_PATH=")]
    return unique(lines, "fresh login PATH")


def installed_cli(env):
    search = []
    if os.name == "nt":
        search.append(Path(env["LOCALAPPDATA"]) / "Nowledge Mem CLI/bin")
    else:
        search.extend((Path(env["HOME"]) / ".local/bin", Path("/usr/local/bin")))
    # Observe installation's discoverability in a fresh terminal; never repair
    # its PATH here, which would hide an installer/boot ownership failure.
    env["PATH"] = terminal_path(env)
    found = shutil.which("nmem", path=env["PATH"])
    if found is None:
        return None
    path = Path(found)
    # Directory aliases may select an installed entry. Resolve only its parent:
    # package-owned leaf links legitimately point into the resource payload.
    parent = path.parent.resolve()
    require(any(parent == directory.resolve() for directory in search), f"Foreign CLI selected: {path}")
    return path


def get_json(port, route):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{route}", timeout=3) as response:
        require(response.status == 200, f"HTTP status {response.status}")
        result = json.load(response)
    require(isinstance(result, dict), f"Non-object {route} response")
    return result


def runtime_is_ready(details):
    return (details.get("status") == "ok"
            and details.get("database_connected") is True
            and details.get("services_ready") is True)


def validate_server_output(output, version, source_sha):
    exact_version(output, "nmem-server", version)
    markers = [line for line in output.splitlines() if line.startswith("nmem-build-sha:")]
    require(markers == [f"nmem-build-sha:{source_sha}"], "Packaged server build SHA mismatch")


def verify(args):
    require(VERSION.fullmatch(args.version) is not None, "Expected a concrete nonzero version")
    require(re.fullmatch(r"[0-9a-f]{40}", args.source_sha) is not None, "Expected a full source SHA")
    require(re.fullmatch(rf"v{re.escape(args.version)}(?:-rc[1-9][0-9]*)?", args.release_tag) is not None, "Release identity mismatch")
    require(re.fullmatch(r"[0-9a-f]{40}", args.build_workflow_sha) is not None, "Expected build workflow SHA")
    require(args.build_attempt.isdigit() and int(args.build_attempt) > 0, "Invalid build attempt")
    require(args.artifact_id.isdigit() and int(args.artifact_id) > 0, "Invalid artifact ID")
    require(args.package.is_file(), "Package is missing")
    require(not args.receipt.exists(), "Receipt already exists")
    require(os.environ.get("GITHUB_ACTIONS") == "true", "Disposable native GitHub Actions runner required")
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    with args.package.open("rb") as stream:
        package_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    env = dict(os.environ)
    for name in ("NMEM_APP_CONFIG_DIR", "NMEM_SERVER_BIN", "NMEM_SERVER_URL", "NMEM_API_URL", "APPDIR", "APPIMAGE"):
        env.pop(name, None)
    # Keep native HOME/config roots on the disposable runner, allowing the real
    # boot lifecycle to install CLI wrappers (dev config isolation disables it).
    env.update({"NMEM_APP_DATA": str(work / "data"), "NMEM_MODEL_CACHE_DIR": str(work / "models"),
                "NMEM_DISABLE_UPDATES": "1", "NOWLEDGE_BACKEND_PORT": "14249"})
    for path in (work / "data", work / "models"):
        path.mkdir()
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1", 14249)) != 0, "Backend port is already in use")
    app, resources, package_env = installed_app(args, work)
    windows_registration = windows_package_registration(app[0], args.version) if args.platform == "win" else None
    env.update(package_env)
    app_output = command([*app, "--version"], env=env, timeout=20)
    require(app_output == f"Nowledge Mem {args.version} ({args.platform})", f"App identity mismatch: {app_output!r}")
    server = bundled_server(resources, args.platform)
    server_output = command([server, "--version"], env=env, timeout=20)
    validate_server_output(server_output, args.version, args.source_sha)
    log_path = work / "app.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen([str(item) for item in app], env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=os.name != "nt")
        try:
            deadline = time.monotonic() + 180
            details = None
            cli = None
            last_error = "App has not made its owned backend ready"
            while time.monotonic() < deadline:
                require(proc.poll() is None, f"App exited before readiness ({proc.returncode}); see {log_path}")
                try:
                    health = get_json(14249, "/health")
                    if health.get("status") != "ok":
                        raise RuntimeError(f"Backend is not healthy: {health!r}")
                    details = get_json(14249, "/health/details")
                    require(details.get("version") == args.version, f"Runtime version mismatch: {details!r}")
                    require(details.get("build_sha") == args.source_sha, f"Runtime SHA mismatch: {details!r}")
                    if runtime_is_ready(details):
                        cli = installed_cli(env)
                        if cli is not None:
                            break
                        last_error = "Installed CLI is not on PATH"
                    else:
                        last_error = f"Backend details are not ready: {details!r}"
                except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                    last_error = str(error)
                time.sleep(0.5)
            require(cli is not None and details is not None, f"Readiness timeout: {last_error}")
            cli_output = command(cli_command(cli), env=env, timeout=20)
            exact_version(cli_output, "nmem", args.version)
            require(proc.poll() is None, "App exited during CLI verification")
            receipt = {
                "schema": 1, "passed": True, "platform": args.platform, "version": args.version,
                "source_sha": args.source_sha, "build_run_id": args.build_run_id,
                "release_tag": args.release_tag, "build_workflow_sha": args.build_workflow_sha,
                "build_attempt": args.build_attempt, "artifact_id": args.artifact_id,
                "package": str(args.package), "package_sha256": package_sha,
                "app_command": [str(item) for item in app], "app_output": app_output,
                "server": str(server), "server_output": server_output, "health_details": details,
                "installed_cli": str(cli), "cli_output": cli_output,
                "windows_package_registration": windows_registration,
            }
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
        finally:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=20, check=False)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument("--app", type=lambda p: Path(p).resolve())
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--build-run-id", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--build-workflow-sha", required=True)
    parser.add_argument("--build-attempt", required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args)
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {args.platform} packaged App, server and installed CLI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
