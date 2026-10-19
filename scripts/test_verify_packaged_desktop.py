"""Fail-closed parser, execution and aggregate guards; not native package proof."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
import unittest

from desktop_version_receipts import accept, validate_build_identity
from verify_packaged_desktop import PLATFORM_ARTIFACTS, PLATFORMS, command, exact_version, installed_cli, runtime_is_ready, unique


class PackagedVersionContract(unittest.TestCase):
    def test_public_liveness_does_not_replace_detailed_readiness(self):
        healthy = {"status": "ok", "database_connected": True, "services_ready": True}
        self.assertTrue(runtime_is_ready(healthy))
        for field, bad in (("status", "error"), ("database_connected", False),
                           ("services_ready", False), ("services_ready", "true")):
            with self.subTest(field=field, bad=bad):
                self.assertFalse(runtime_is_ready(dict(healthy, **{field: bad})))
        self.assertFalse(runtime_is_ready({"status": "ok"}))

    def test_same_source_other_rc_run_is_rejected(self):
        run = {"path": ".github/workflows/release-desktop.yml", "status": "completed", "conclusion": "success",
               "event": "push", "head_branch": "v0.10.104-rc2", "head_sha": "c" * 40, "run_attempt": 1}
        validate_build_identity(run, "v0.10.104-rc2")
        with self.assertRaises(RuntimeError):
            validate_build_identity(dict(run, head_branch="v0.10.104-rc1"), "v0.10.104-rc2")
        with self.assertRaises(RuntimeError):
            validate_build_identity(dict(run, event="workflow_dispatch"), "v0.10.104-rc2")

    def test_existing_wrapper_without_terminal_path_registration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / ".local/bin/nmem"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("#!/bin/sh\nexit 0\n")
            wrapper.chmod(0o755)
            env = {"HOME": directory, "PATH": str(wrapper.parent)}
            with patch("verify_packaged_desktop.terminal_path", return_value="/usr/bin"):
                self.assertIsNone(installed_cli(env))
            with patch("verify_packaged_desktop.terminal_path", return_value=str(wrapper.parent)):
                self.assertEqual(installed_cli(env), wrapper)

    def test_concrete_matching_version_required(self):
        self.assertEqual(exact_version("nmem 0.10.104", "nmem", "0.10.104"), "0.10.104")
        for output in ("", "nmem 0", "nmem 0.0.0", "nmem @VERSION@", "nmem 0.10.103",
                       "0.10.104", "nmem 0.10.104\nold output", "nmem 0.10.104-extra"):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                exact_version(output, "nmem", "0.10.104")

    def test_real_subprocess_errors_are_not_success(self):
        with self.assertRaises(subprocess.CalledProcessError):
            command([sys.executable, "-c", "raise SystemExit(17)"])
        with self.assertRaises(subprocess.TimeoutExpired):
            command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)
        self.assertEqual(command([sys.executable, "-c", "print('nmem 0.10.104')"]), "nmem 0.10.104")

    def test_ambiguous_payload_is_not_accepted(self):
        for candidates in ([], [Path("a"), Path("b")]):
            with self.subTest(candidates=candidates), self.assertRaises(RuntimeError):
                unique(candidates, "payload")

    def test_receipts_require_same_candidate_and_six_distinct_paths(self):
        sha = "a" * 40
        identity = {"release_tag": "v0.10.104-rc2", "build_workflow_sha": "c" * 40, "build_attempt": "1",
                    "artifacts": {name: str(i + 1) for i, name in enumerate(set(PLATFORM_ARTIFACTS.values()))}}
        template = {
            "schema": 1, "passed": True, "version": "0.10.104", "source_sha": sha,
            "build_run_id": "123", "package_sha256": "b" * 64,
            "health_details": {"version": "0.10.104", "build_sha": sha, "status": "ok",
                               "database_connected": True, "services_ready": True},
            "cli_output": "nmem 0.10.104",
            "server_output": f"nmem-server 0.10.104 (aaaaaaaaaaaa, built 123)\nnmem-build-sha:{sha}\nnmem-storage-build:public",
            "release_tag": identity["release_tag"], "build_workflow_sha": identity["build_workflow_sha"],
            "build_attempt": identity["build_attempt"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            originals = {}
            for platform in PLATFORMS:
                receipt = dict(template, platform=platform, app_output=f"Nowledge Mem 0.10.104 ({platform})",
                               artifact_id=identity["artifacts"][PLATFORM_ARTIFACTS[platform]])
                path = root / f"{platform}.json"
                originals[path] = receipt
                path.write_text(json.dumps(receipt))
            self.assertEqual(accept(root, "0.10.104", sha, "123", identity), set(PLATFORMS))
            first = next(iter(originals))
            for field, bad in (("passed", False), ("version", "0.0.0"), ("source_sha", "c" * 40),
                               ("build_run_id", "122"), ("package_sha256", ""),
                               ("release_tag", "v0.10.104-rc1"), ("artifact_id", "999"),
                               ("build_workflow_sha", "d" * 40), ("build_attempt", "2"),
                               ("server_output", ""), ("server_output", "nmem-server 0.10.103"),
                               ("platform", "win"), ("app_output", "Nowledge Mem 0 (mac)"),
                               ("cli_output", "nmem 0.10.103"),
                               ("health_details", {"version": "0.10.104", "build_sha": "unknown"})):
                mutant = copy.deepcopy(originals[first])
                mutant[field] = bad
                first.write_text(json.dumps(mutant))
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    accept(root, "0.10.104", sha, "123", identity)
            mutant = copy.deepcopy(originals[first])
            mutant['health_details'].update(status='error', database_connected=False, services_ready=False)
            first.write_text(json.dumps(mutant))
            with self.assertRaises(RuntimeError):
                accept(root, "0.10.104", sha, "123", identity)
            first.unlink()
            with self.assertRaises(RuntimeError):
                accept(root, "0.10.104", sha, "123", identity)


if __name__ == "__main__":
    unittest.main()
