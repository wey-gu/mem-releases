"""Stage and verify a bundle-only RPM recovery from a qualified DEB payload."""

import argparse
import hashlib
import json
import mmap
from pathlib import Path
import re
import shutil
import stat
import subprocess

from verify_rpm_archive import verify_archive_digest


def original_run_tag(run, source_tag):
    if run["path"] != ".github/workflows/release-desktop.yml":
        raise ValueError("Expected the original Desktop workflow")
    if run["head_branch"] not in {source_tag, "build-" + source_tag}:
        raise ValueError("Original tooling tag does not match the source RC")
    if (
        run["head_branch"] == "build-" + source_tag
        and run["event"] != "workflow_dispatch"
    ):
        raise ValueError("The build-only tooling alias requires a manual trigger")
    if run["event"] not in {"push", "workflow_dispatch"}:
        raise ValueError("Unsupported original Desktop trigger")
    return run["head_branch"]


def file_identity(path):
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"Unsupported payload entry: {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": digest, "mode": stat.S_IMODE(file_stat.st_mode)}


def inventory(root):
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError(f"Expected a payload directory: {root}")
    result = {}
    for path in sorted(root.rglob("*")):
        if not stat.S_ISDIR(path.lstat().st_mode):
            result[str(path.relative_to(root))] = file_identity(path)
    return result


def require_runtime_directories(payload, directory):
    if ".." in directory.relative_to(payload).parts:
        raise ValueError(f"Runtime directory is outside the payload: {directory}")
    while directory != payload:
        directory_stat = directory.lstat()
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or stat.S_IMODE(directory_stat.st_mode) != 0o755
        ):
            raise ValueError(
                f"RPM runtime directory type or permissions differ: {directory}"
            )
        directory = directory.parent


def runtime_inventory(payload, root):
    result = inventory(root)
    # RPM resources and extraction with umask 022 produce public directories.
    # Validate required ancestors without retaining unused empty DEB folders.
    require_runtime_directories(payload, root)
    for relative_path in result:
        require_runtime_directories(payload, (root / relative_path).parent)
    return result


def require_elf(path):
    with path.open("rb") as stream:
        header = stream.read(20)
    if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
        raise ValueError(f"Expected an x86_64 ELF binary: {path}")


def bundle_marker_offset(binary, reference):
    """Identify the mutable marker by comparison with the same-run AppImage."""
    deb_marker = b"__TAURI_BUNDLE_TYPE_VAR_DEB"
    appimage_marker = b"__TAURI_BUNDLE_TYPE_VAR_APP"
    candidates = []
    offset = 0
    while (offset := binary.find(deb_marker, offset)) >= 0:
        start = max(0, offset - 64)
        end = offset + len(deb_marker)
        window = binary[start:offset] + appimage_marker + binary[end : end + 64]
        if reference.count(window) == 1:
            candidates.append(offset)
        offset = end
    if len(candidates) != 1 or b"__TAURI_BUNDLE_TYPE_VAR_UNK" in binary:
        raise ValueError("Cannot uniquely identify the mutable Tauri bundle marker")
    return candidates[0]


def stage(source, payload, reference_executable, version, source_sha, receipt):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a GA version")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Expected a full source commit SHA")
    app = source / "nowledge-graph"
    for path in [app / "package.json", app / "src-tauri/tauri.conf.json"]:
        if json.loads(path.read_text())["version"] != version:
            raise ValueError(f"Source version mismatch: {path}")
    executable = payload / "usr/bin/nowledge-mem"
    backend = payload / "usr/lib/Nowledge Mem/_up_/rust-backend"
    for path in [
        executable,
        *(backend / name for name in ["nmem-server", "nmem", "browse-now"]),
    ]:
        require_elf(path)
    with (backend / "nmem-server").open("rb") as stream:
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as binary:
            if binary.find(b"nmem-build-sha:" + source_sha.encode()) < 0:
                raise ValueError(
                    "The DEB server does not contain the expected source marker"
                )
    if not (backend / "web-dist/index-web.html").is_file():
        raise ValueError("The DEB has no bundled Web entry point")
    target = app / "src-tauri/target/release/nowledge-mem"
    target.parent.mkdir(parents=True, exist_ok=True)
    staged_backend = app / "rust-backend"
    existing = (
        {path.name for path in staged_backend.iterdir()}
        if staged_backend.exists()
        else set()
    )
    if target.exists() or existing - {".gitkeep"}:
        raise ValueError("Recovery requires an unstaged source checkout")
    binary = executable.read_bytes()
    reference = reference_executable.read_bytes()
    offset = bundle_marker_offset(binary, reference)
    prefix = binary[:offset] + b"__TAURI_BUNDLE_TYPE_VAR_"
    suffix = binary[offset + len(b"__TAURI_BUNDLE_TYPE_VAR_DEB") :]
    # Restore only the mutable marker; Tauri changes UNK to RPM while bundling.
    shutil.copy2(executable, target)
    target.write_bytes(prefix + b"UNK" + suffix)
    shutil.copytree(backend, staged_backend, dirs_exist_ok=True)
    if (staged_backend / ".gitkeep").exists() and not (backend / ".gitkeep").exists():
        (staged_backend / ".gitkeep").unlink()
    # The binary already embeds the frontend. bundle does not rebuild it.
    (app / "dist").mkdir(exist_ok=True)
    result = {
        "version": version,
        "source_sha": source_sha,
        "compression": {"type": "gzip", "level": 1},
        "executable": inventory(executable.parent),
        "backend": inventory(backend),
        "deb_executable_sha256": hashlib.sha256(binary).hexdigest(),
        "appimage_executable_sha256": hashlib.sha256(reference).hexdigest(),
        "bundle_marker_offset": offset,
    }
    result["executable"]["nowledge-mem"]["sha256"] = hashlib.sha256(
        prefix + b"RPM" + suffix
    ).hexdigest()
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Staged {len(result['backend'])} backend files from the qualified DEB")


def verify(source, payload, rpm, receipt):
    expected = json.loads(receipt.read_text())
    actual = runtime_inventory(payload, payload / "usr/bin")
    if actual != expected["executable"]:
        raise ValueError("Repacked executable bytes or permissions changed")
    backends = list(payload.glob("usr/lib/**/rust-backend"))
    if not backends or any(
        runtime_inventory(payload, path) != expected["backend"] for path in backends
    ):
        raise ValueError("Repacked backend resource bytes or permissions changed")
    app = source / "nowledge-graph"
    config = json.loads((app / "src-tauri/tauri.linux.conf.json").read_text())[
        "bundle"
    ]["linux"]["rpm"]
    for destination, original in config["files"].items():
        wanted = (app / "src-tauri" / original).resolve()
        installed = payload / destination.lstrip("/")
        if wanted.is_dir():
            if inventory(wanted) != runtime_inventory(payload, installed):
                raise ValueError(f"RPM directory mapping differs: {destination}")
        else:
            require_runtime_directories(payload, installed.parent)
            if file_identity(wanted) != file_identity(installed):
                raise ValueError(
                    f"RPM file mapping bytes or permissions differ: {destination}"
                )
    metadata = subprocess.check_output(
        [
            "rpm",
            "-qp",
            "--qf",
            "%{VERSION}\n%{ARCH}\n%{PAYLOADCOMPRESSOR}\n%{PAYLOADFLAGS}\n",
            str(rpm),
        ],
        text=True,
    ).splitlines()
    if metadata != [expected["version"], "x86_64", "gzip", "1"]:
        raise ValueError(f"Unexpected RPM metadata: {metadata}")
    for key, tag in [
        ("postInstallScript", "POSTIN"),
        ("preRemoveScript", "PREUN"),
        ("postRemoveScript", "POSTUN"),
    ]:
        script = (app / "src-tauri" / config[key]).read_text().rstrip("\n")
        installed = subprocess.check_output(
            ["rpm", "-qp", "--qf", "%{" + tag + "}", str(rpm)], text=True
        )
        if installed.rstrip("\n") != script:
            raise ValueError(f"RPM scriptlet differs: {tag}")
    subprocess.run(["rpm", "--checksig", "--nosignature", str(rpm)], check=True)
    archive_digest = verify_archive_digest(rpm)
    expected["archive_sha256"] = archive_digest
    expected["rpm_verified"] = True
    with rpm.open("rb") as stream:
        expected["rpm_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    receipt.write_text(json.dumps(expected, indent=2) + "\n")
    print("RPM payload identity, file mappings, scriptlets, and metadata verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["stage", "verify"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--version")
    parser.add_argument("--reference-executable", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--rpm", type=Path)
    args = parser.parse_args()
    if args.mode == "stage":
        stage(
            args.source,
            args.payload,
            args.reference_executable,
            args.version,
            args.source_sha,
            args.receipt,
        )
    else:
        verify(args.source, args.payload, args.rpm, args.receipt)
