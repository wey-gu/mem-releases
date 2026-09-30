#!/usr/bin/env python3
"""Finish a verified GA delivery without leaving its GitHub release in draft."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


class ReleaseError(RuntimeError):
    pass


def validate_version(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError(f"GA version must be clean semver: {version}")


def build_notes(payload, version):
    validate_version(version)
    if payload.get("found") is not True or payload.get("version") != version:
        raise ReleaseError(f"Deployed Changelog does not contain GA {version}")
    title = payload.get("title")
    notes = payload.get("release_notes")
    if not isinstance(title, str) or not title.strip() or not isinstance(notes, str) or not notes.strip():
        raise ReleaseError(f"Deployed Changelog has no usable release notes for {version}")
    return f"## {title.strip()}\n\n{notes.strip()}\n\n[Full changelog](https://mem.nowledge.co/changelog#{version})\n"


def prepare_notes(version, output):
    validate_version(version)
    query = urlencode({"version": version, "limit": 8})
    url = f"https://mem.nowledge.co/api/changelog/release-notes?{query}"
    with urlopen(url, timeout=10) as response:
        payload = json.load(response)
    output.write_text(build_notes(payload, version), encoding="utf-8")


def run_gh(args):
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return result.stdout


def get_release(version, repo, gh=run_gh):
    validate_version(version)
    releases = json.loads(gh(["api", "-X", "GET", f"repos/{repo}/releases?per_page=100"]))
    return next((release for release in releases if release.get("tag_name") == f"v{version}"), None)


def ensure_not_published(version, repo, gh=run_gh):
    release = get_release(version, repo, gh)
    if release and not release.get("draft"):
        raise ReleaseError(f"v{version} is already public; refusing to replace its artifacts")


def complete_release(version, repo, notes_file, latest, gh=run_gh):
    release = get_release(version, repo, gh)
    if not release or not release.get("draft") or release.get("prerelease"):
        raise ReleaseError(f"v{version} must be a draft GA release before publication")

    required = {
        f"Nowledge.Mem_{version}_aarch64.dmg",
        f"Nowledge.Mem_{version}_x64.dmg",
        f"Nowledge.Mem_{version}_x64-setup.exe",
        f"Nowledge.Mem_{version}_amd64.deb",
        f"Nowledge.Mem_{version}_amd64.AppImage",
    }
    uploaded = {asset.get("name") for asset in release.get("assets", [])}
    missing = sorted(required - uploaded)
    if missing:
        raise ReleaseError(f"v{version} is missing required GA assets: {', '.join(missing)}")

    body = release.get("body") or ""
    if not body.strip() or body.startswith(("Promoted from v", "Desktop bundles for ")):
        generated = notes_file.read_text(encoding="utf-8")
        if body.strip():
            generated += f"\n{body.strip()}\n"
        if not any(name.endswith(".rpm") for name in uploaded if isinstance(name, str)):
            generated += "\nLinux RPM availability is pending separate validation.\n"
        notes_file.write_text(generated, encoding="utf-8")
        gh(["release", "edit", f"v{version}", "-R", repo, "--notes-file", str(notes_file)])

    release_id = release["id"]
    gh(["api", "-X", "PATCH", f"repos/{repo}/releases/{release_id}", "-F", "draft=false"])
    # GitHub ignores make_latest when included in the same PATCH as draft=false.
    gh(["api", "-X", "PATCH", f"repos/{repo}/releases/{release_id}", "-f", f"make_latest={str(latest).lower()}"])

    published = get_release(version, repo, gh)
    if not published or published.get("draft") or not published.get("published_at"):
        raise ReleaseError(f"v{version} publication did not persist")
    current = json.loads(gh(["api", "-X", "GET", f"repos/{repo}/releases/latest"]))
    if latest and current.get("tag_name") != f"v{version}":
        raise ReleaseError(f"v{version} was published but is not the latest GitHub release")
    if not latest and current.get("tag_name") == f"v{version}":
        raise ReleaseError(f"v{version} unexpectedly became the latest GitHub release")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "check", "complete"):
        sub = subcommands.add_parser(command)
        sub.add_argument("version")
        if command != "prepare":
            sub.add_argument("--repo", default="wey-gu/mem-releases")
        if command != "check":
            sub.add_argument("--notes-file", type=Path, required=True)
        if command == "complete":
            sub.add_argument("--latest", choices=("true", "false"), required=True)
    args = parser.parse_args()

    try:
        if args.command == "prepare":
            prepare_notes(args.version, args.notes_file)
        elif args.command == "check":
            ensure_not_published(args.version, args.repo)
        else:
            complete_release(args.version, args.repo, args.notes_file, args.latest == "true")
    except (ReleaseError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
