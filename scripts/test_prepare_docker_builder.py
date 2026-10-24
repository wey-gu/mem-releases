import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_docker_builder as builder


class DockerBuilderTest(unittest.TestCase):
    def test_linux_desktop_admits_and_retains_resources_before_compilation(self):
        workflow = Path(__file__).resolve().parents[1] / ".github/workflows/release-desktop.yml"
        text = workflow.read_text()
        for job in ("build-linux-deb-appimage", "build-linux-vulkan"):
            with self.subTest(job=job):
                body = text.split(f"\n  {job}:\n", 1)[1]
                body = re.split(r"\n  [A-Za-z0-9_-]+:\n", body, 1)[0]
                self.assertIn("prepare_linux_builder", body,
                              "Hosted Linux compiler bypasses resource admission")
                prepare = text.split("- &prepare_linux_builder\n", 1)[1].split("\n\n", 1)[0]
                checkout = text.split("- &checkout_linux_builder_tools\n", 1)[1].split("\n\n", 1)[0]
                retain = text.split("- &retain_linux_builder_admission\n", 1)[1].split("\n\n", 1)[0]
                self.assertIn("release-tools/scripts/prepare_docker_builder.py", prepare)
                self.assertIn('--receipt "$RUNNER_TEMP/linux-builder-prepared.json"', prepare)
                self.assertIn("repository: ${{ github.repository }}", checkout)
                self.assertIn("ref: ${{ github.sha }}", checkout)
                self.assertIn("path: release-tools", checkout)
                self.assertIn("always()", retain)
                self.assertIn("linux-builder-prepared.json", retain)
                self.assertIn("actions/upload-artifact@", retain)
                self.assertLess(body.index("checkout_linux_builder_tools"), body.index("prepare_linux_builder"))
                self.assertLess(body.index("prepare_linux_builder"), body.index("retain_linux_builder_admission"))
                self.assertLess(body.index("retain_linux_builder_admission"), body.index("Build Rust backend bundle (Linux"))

    def test_all_optimized_image_entry_points_prepare_resources_before_buildx(self):
        workflows = Path(__file__).resolve().parents[1] / ".github/workflows"
        callers = {
            "release-docker.yml": ("build-amd64", "build-arm64", "build-cuda", "build-vulkan"),
            "promote-gpu-variant.yml": ("build",),
        }
        helper = "release-tools/scripts/prepare_docker_builder.py"
        for filename, jobs in callers.items():
            text = (workflows / filename).read_text()
            for job in jobs:
                with self.subTest(workflow=filename, job=job):
                    body = text.split(f"\n  {job}:\n", 1)[1]
                    body = re.split(r"\n  [A-Za-z0-9_-]+:\n", body, 1)[0]
                    if "- *prepare_docker_builder" in body:
                        binding = text.split("- &prepare_docker_builder\n", 1)[1].split("\n\n", 1)[0]
                        self.assertIn(helper, binding)
                        position = body.index("- *prepare_docker_builder")
                    else:
                        self.assertIn(helper, body, "Optimized compiler entry point bypasses resource preparation")
                        position = body.index(helper)
                    self.assertLess(position, body.index("docker/setup-buildx-action@"))

    def resources(self, ram=16, swap=0, disk=60):
        return dict(ram_bytes=ram * builder.GIB, swap_bytes=swap * builder.GIB,
                    disk_free_bytes=disk * builder.GIB)

    def test_inventory_rejects_missing_malformed_or_duplicate_fields(self):
        for text in ("", "MemTotal: 0 kB\nSwapTotal: 0 kB", "MemTotal: 10 MB\nSwapTotal: 0 kB",
                     "MemTotal: 1 kB\nMemTotal: 1 kB\nSwapTotal: 0 kB"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                builder.memory_bytes(text)
        self.assertEqual(builder.memory_bytes("MemTotal: 123 kB\nSwapTotal: 4 kB\nOther: 5 kB"),
                         dict(ram_bytes=123 * 1024, swap_bytes=4096))

    def test_plan_accounts_for_existing_swap_and_rounds_up(self):
        self.assertEqual(builder.allocation_bytes(self.resources(swap=8)), 8 * builder.GIB)
        self.assertEqual(builder.allocation_bytes(self.resources(ram=64)), 0)
        inventory = self.resources()
        inventory["ram_bytes"] -= 4096
        self.assertEqual(builder.allocation_bytes(inventory), 17 * builder.GIB)

    def test_disallows_workstation_and_self_hosted_before_any_command(self):
        with tempfile.TemporaryDirectory() as directory:
            for env in ({}, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_OS": "Linux"}):
                calls = []
                receipt = Path(directory) / "receipt.json"
                with self.assertRaises(RuntimeError):
                    builder.prepare(env, receipt, command=lambda *a, **kw: calls.append(a))
                self.assertFalse(calls)
                self.assertFalse(json.loads(receipt.read_text())["passed"])

    def run_prepare(self, directory, observations, command=None):
        env = dict(GITHUB_ACTIONS="true", RUNNER_ENVIRONMENT="github-hosted", RUNNER_OS="Linux",
                   RUNNER_TEMP=directory)
        receipt = Path(directory) / "receipt.json"
        with patch.object(builder.platform, "system", return_value="Linux"):
            return builder.prepare(env, receipt, observe=lambda root: next(observations),
                                   command=command or (lambda *a, **kw: None))

    def test_sufficient_memory_skips_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            record = self.run_prepare(directory, iter([self.resources(ram=32)] * 3))
            self.assertTrue(record["passed"])
            self.assertEqual(record["commands"], [])

    def test_insufficient_disk_refuses_allocation(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(RuntimeError, "Insufficient disk"):
            self.run_prepare(directory, iter([self.resources(disk=25)] * 2))

    def test_command_failure_is_persisted_and_stops_sequence(self):
        for failed in ("fallocate", "mkswap", "sudo"):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory:
                calls = []

                def command(args, **kwargs):
                    calls.append(args)
                    if args[0] == failed:
                        raise subprocess.CalledProcessError(1, args)

                with self.assertRaises(subprocess.CalledProcessError):
                    self.run_prepare(directory, iter([self.resources()] * 2), command)
                record = json.loads((Path(directory) / "receipt.json").read_text())
                self.assertFalse(record["passed"])
                self.assertEqual(calls[-1][0], failed)

    def test_success_requires_observed_swap_and_preserves_existing_swap(self):
        for after, passed in ((self.resources(swap=16, disk=44), True),
                              (self.resources(swap=0), False),
                              (self.resources(swap=16, disk=10), False)):
            with self.subTest(after=after), tempfile.TemporaryDirectory() as directory:
                values = iter([self.resources(swap=4)] * 2 + [after])
                if passed:
                    self.assertTrue(self.run_prepare(directory, values)["passed"])
                else:
                    with self.assertRaises(RuntimeError):
                        self.run_prepare(directory, values)
                self.assertEqual(json.loads((Path(directory) / "receipt.json").read_text())["passed"], passed)



if __name__ == "__main__":
    unittest.main()
