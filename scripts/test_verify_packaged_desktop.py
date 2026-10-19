"""Fail-closed parser, execution and aggregate guards; not native package proof."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from desktop_version_receipts import accept
from verify_packaged_desktop import PLATFORMS, command, exact_version, runtime_is_ready, unique


class PackagedVersionContract(unittest.TestCase):
    def test_public_liveness_does_not_replace_detailed_readiness(self):
        healthy = {"status": "ok", "database_connected": True, "services_ready": True}
        self.assertTrue(runtime_is_ready(healthy))
        for field, bad in (("status", "error"), ("database_connected", False),
                           ("services_ready", False), ("services_ready", "true")):
            with self.subTest(field=field, bad=bad):
                self.assertFalse(runtime_is_ready(dict(healthy, **{field: bad})))
        self.assertFalse(runtime_is_ready({"status": "ok"}))

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
        template = {
            "schema": 1, "passed": True, "version": "0.10.104", "source_sha": sha,
            "build_run_id": "123", "package_sha256": "b" * 64,
            "health_details": {"version": "0.10.104", "build_sha": sha, "status": "ok",
                               "database_connected": True, "services_ready": True},
            "cli_output": "nmem 0.10.104",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            originals = {}
            for platform in PLATFORMS:
                receipt = dict(template, platform=platform, app_output=f"Nowledge Mem 0.10.104 ({platform})")
                path = root / f"{platform}.json"
                originals[path] = receipt
                path.write_text(json.dumps(receipt))
            self.assertEqual(accept(root, "0.10.104", sha, "123"), set(PLATFORMS))
            first = next(iter(originals))
            for field, bad in (("passed", False), ("version", "0.0.0"), ("source_sha", "c" * 40),
                               ("build_run_id", "122"), ("package_sha256", ""),
                               ("platform", "win"), ("app_output", "Nowledge Mem 0 (mac)"),
                               ("cli_output", "nmem 0.10.103"),
                               ("health_details", {"version": "0.10.104", "build_sha": "unknown"})):
                mutant = copy.deepcopy(originals[first])
                mutant[field] = bad
                first.write_text(json.dumps(mutant))
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    accept(root, "0.10.104", sha, "123")
            mutant = copy.deepcopy(originals[first])
            mutant['health_details'].update(status='error', database_connected=False, services_ready=False)
            first.write_text(json.dumps(mutant))
            with self.assertRaises(RuntimeError):
                accept(root, "0.10.104", sha, "123")
            first.unlink()
            with self.assertRaises(RuntimeError):
                accept(root, "0.10.104", sha, "123")


if __name__ == "__main__":
    unittest.main()
