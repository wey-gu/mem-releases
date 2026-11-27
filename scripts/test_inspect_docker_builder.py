import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from scripts import inspect_docker_builder as builder


class InspectDockerBuilderTest(unittest.TestCase):
    def container(self):
        return {
            "Id": "a" * 64, "Name": "/buildx_buildkit_test0", "Image": "sha256:test",
            "HostConfig": dict(builder.EXPECTED_LIMITS),
            "State": {"Running": True, "OOMKilled": False},
            "Config": {"Env": ["SECRET=must-not-enter-receipt"]},
        }

    def test_requires_every_observed_limit_and_clean_state(self):
        good = self.container()
        self.assertEqual(builder.verify("test", [good])["limits"], builder.EXPECTED_LIMITS)
        for field in builder.EXPECTED_LIMITS:
            bad = copy.deepcopy(good)
            bad["HostConfig"][field] = 0
            with self.subTest(field=field), self.assertRaises(ValueError):
                builder.verify("test", [bad])
        for state in ({}, {"Running": False, "OOMKilled": False},
                      {"Running": True, "OOMKilled": True}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                builder.verify("test", [{**good, "State": state}])
        for values in ([], [good, good], [{**good, "Name": "/wrong-builder"}]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                builder.verify("test", values)

    def test_receipt_is_narrow_and_persists_command_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            calls = []

            def command(args, **kwargs):
                calls.append(args)
                return json.dumps([self.container()])

            result = builder.inspect("test", receipt, command)
            self.assertTrue(result["passed"])
            self.assertEqual(calls, [["docker", "inspect", "buildx_buildkit_test0"]])
            self.assertNotIn("SECRET", receipt.read_text())
            self.assertEqual(result, json.loads(receipt.read_text()))

            def failed(*args, **kwargs):
                raise subprocess.CalledProcessError(1, args[0])

            with self.assertRaises(subprocess.CalledProcessError):
                builder.inspect("test", receipt, failed)
            self.assertFalse(json.loads(receipt.read_text())["passed"])
            calls.clear()
            with self.assertRaises(ValueError):
                builder.inspect("--all", receipt, command)
            self.assertFalse(calls)

    def test_release_uses_the_limits_and_retains_admission_before_build(self):
        root = Path(__file__).resolve().parents[1]
        jobs = yaml.safe_load((root / ".github/workflows/release-docker.yml").read_text())["jobs"]
        steps = jobs["build-amd64"]["steps"]
        setup = next(i for i, step in enumerate(steps)
                     if step.get("uses", "").startswith("docker/setup-buildx-action@"))
        configured = dict(line.split("=", 1) for line in steps[setup]["with"]["driver-opts"].splitlines())
        self.assertEqual(configured, {"memory": "12g", "memory-swap": "24g",
                                      "cpu-period": "100000", "cpu-quota": "200000"})
        self.assertIn("inspect_docker_builder.py", steps[setup + 1]["run"])
        retain = steps[setup + 2]
        self.assertTrue(retain["uses"].startswith("actions/upload-artifact@"))
        self.assertEqual(retain["with"]["if-no-files-found"], "error")
        build = next(i for i, step in enumerate(steps)
                     if step.get("uses", "").startswith("docker/build-push-action@"))
        self.assertLess(setup + 2, build)
        for job in ("build-arm64", "build-cuda", "build-vulkan"):
            with self.subTest(job=job):
                action = next(step for step in jobs[job]["steps"]
                              if step.get("uses", "").startswith("docker/setup-buildx-action@"))
                self.assertNotIn("driver-opts", action.get("with", {}))


if __name__ == "__main__":
    unittest.main()
