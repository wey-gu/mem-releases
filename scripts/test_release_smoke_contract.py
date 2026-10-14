"""Guard the automated rc smoke (nowledge-co/mem issue 7108, W18).

The scriptable part of the rc smoke runs inside the release jobs on the
artifact each job built. These tests pin where it runs and what makes it a
gate: it must run BEFORE the artifact upload (a failure withholds the
artifact, so the rc cannot be promoted), stay bounded in time, assert the
exact source commit, and on Docker exercise both a write and a reread after
restart.
"""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SMOKE_STEP = "Smoke the packaged backend in isolation"


def load(name):
    import yaml  # PyYAML ships with the hosted Ubuntu images.

    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def is_upload(step):
    return str(step.get("uses", "")).startswith("actions/upload-artifact")


class DesktopSmokeContract(unittest.TestCase):
    def setUp(self):
        self.jobs = load("release-desktop.yml")["jobs"]

    def test_smoke_gates_linux_and_macos_arm64_before_upload(self):
        for job in ("build-linux-deb-appimage", "build-macos-arm64"):
            with self.subTest(job=job):
                steps = self.jobs[job]["steps"]
                names = [s.get("name") for s in steps]
                self.assertIn(SMOKE_STEP, names)
                smoke_at = names.index(SMOKE_STEP)
                first_upload = next(i for i, s in enumerate(steps) if is_upload(s))
                self.assertLess(smoke_at, first_upload, "smoke must run before the artifact is uploaded")
                step = steps[smoke_at]
                self.assertNotIn("continue-on-error", step)
                self.assertNotIn("if", step)
                self.assertLessEqual(step["timeout-minutes"], 5)
                run = step["run"]
                self.assertIn("scripts/release-smoke-core.sh", run)
                self.assertIn('--expect-version "$VERSION"', run)
                self.assertIn('--expect-sha "$(git rev-parse HEAD)"', run)
                self.assertIn("--package", run)

    def test_smoke_package_resolves_in_each_job(self):
        package = self.jobs["build-macos-arm64"]["steps"]
        step = next(s for s in package if s.get("name") == SMOKE_STEP)
        expr = step["env"]["PACKAGE"]
        self.assertIn("steps.locate.outputs.dmg", expr)
        self.assertIn("steps.locate.outputs.deb", expr)


class DockerSmokeContract(unittest.TestCase):
    def test_verify_runs_probe_from_the_built_commit(self):
        verify = load("release-docker.yml")["jobs"]["verify"]
        steps = verify["steps"]
        fetch = next(s for s in steps if s.get("name") == "Fetch the release smoke probe")
        self.assertEqual(fetch["with"]["ref"], "${{ needs.meta.outputs.source_sha }}")
        self.assertEqual(fetch["with"]["sparse-checkout"], "scripts/release_smoke_probe.py")
        smoke = next(s for s in steps if s.get("name") == "Pull and smoke-test")
        self.assertLess(steps.index(fetch), steps.index(smoke))
        self.assertEqual(smoke["env"]["SOURCE_SHA"], "${{ needs.meta.outputs.source_sha }}")
        run = smoke["run"]
        self.assertIn('--expect-sha "$SOURCE_SHA"', run)
        self.assertIn("probe write", run)
        self.assertIn('docker restart "$CID"', run)
        self.assertIn("probe reread", run)
        self.assertLess(run.index("probe write"), run.index("probe reread"))
        self.assertIn('"bind"', run, "the container must be proven free of host bind mounts")


if __name__ == "__main__":
    unittest.main()
