"""Exercise the driver with disposable local helpers, never release credentials."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from scripts import release_driver as driver


CLI = Path(driver.__file__).resolve()


class ReleaseDriverTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.helper = self.root / "helper.py"
        self.helper.write_text(
            """import os, pathlib, sys, time
mode = sys.argv[1]
if mode == "wait":
    pathlib.Path("ready").write_text("ready")
    time.sleep(30)
elif mode == "fail":
    sys.exit(7)
elif mode == "conditional":
    sys.exit(0 if pathlib.Path("allow").exists() else 9)
else:
    with pathlib.Path("events").open("a") as out:
        out.write(mode + ":" + os.environ["RELEASE_DRIVER_OPERATION_ID"] + "\\n")
"""
        )
        self.plan = self.root / "plan.json"
        self.data = {
            "schema": 1,
            "release": {
                "version": "0.10.119-rc1",
                "source_sha": "a" * 40,
                "tools_sha": "b" * 40,
            },
            "files": {"helper.py": driver.digest(self.helper)},
            "steps": [self.step("first")],
        }
        self.write_plan()

    def step(self, name, kind="action", needs=(), mode=None):
        return {
            "id": name,
            "kind": kind,
            "needs": list(needs),
            "argv": ["{python}", "helper.py", mode or name],
        }

    def write_plan(self):
        self.plan.write_text(json.dumps(self.data))

    def state(self):
        return json.loads((self.root / ".release-driver/state.json").read_text())

    def invoke(self, command="run", *args):
        return subprocess.run(
            [sys.executable, str(CLI), command, "--plan", str(self.plan), *args],
            cwd="/",
            text=True,
            capture_output=True,
            timeout=15,
        )

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def wait_for(self, path):
        end = time.monotonic() + 10
        while not path.exists():
            self.assertLess(time.monotonic(), end, f"Timed out waiting for {path}")
            time.sleep(0.02)

    def start(self):
        child = subprocess.Popen(
            [sys.executable, str(CLI), "run", "--plan", str(self.plan)],
            cwd="/",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def stop():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()

        self.addCleanup(stop)
        return child

    def test_dependency_order_and_resume_skip_successful_actions_from_other_cwd(self):
        self.data["steps"] = [self.step("second", needs=["first"]), self.step("first")]
        self.write_plan()
        self.success(self.invoke())
        lines = (self.root / "events").read_text().splitlines()
        self.assertEqual([line.split(":")[0] for line in lines], ["first", "second"])
        self.success(self.invoke())
        self.assertEqual((self.root / "events").read_text().splitlines(), lines)
        state = self.state()
        self.assertEqual(
            state["steps"]["first"]["attempts"][0]["operation_id"],
            lines[0].split(":")[1],
        )

    def test_checks_are_fresh_but_successful_action_is_not_repeated(self):
        self.data["steps"] = [
            self.step("check", "check"),
            self.step("publish", needs=["check"]),
        ]
        self.write_plan()
        self.success(self.invoke())
        self.success(self.invoke())
        self.assertEqual(
            [
                line.split(":")[0]
                for line in (self.root / "events").read_text().splitlines()
            ],
            ["check", "publish", "check"],
        )
        self.assertEqual(len(self.state()["steps"]["check"]["attempts"]), 2)

    def test_failed_check_blocks_action_then_resumes_after_condition_recovers(self):
        self.data["steps"] = [
            self.step("check", "check", mode="conditional"),
            self.step("publish", needs=["check"]),
        ]
        self.write_plan()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertFalse((self.root / "events").exists())
        self.assertEqual(self.state()["steps"]["check"]["attempts"][0]["exit_code"], 9)
        (self.root / "allow").touch()
        self.success(self.invoke())
        self.assertEqual(len(self.state()["steps"]["check"]["attempts"]), 2)
        self.assertEqual(len(self.state()["steps"]["publish"]["attempts"]), 1)

    def test_failed_action_is_retained_and_never_automatically_retried(self):
        self.data["steps"] = [self.step("publish", mode="fail")]
        self.write_plan()
        self.assertNotEqual(self.invoke().returncode, 0)
        before = self.state()
        self.assertEqual(before["steps"]["publish"]["attempts"][0]["exit_code"], 7)
        result = self.invoke()
        self.assertIn("requires reconciliation", result.stderr)
        self.assertEqual(self.state(), before)

    def test_status_has_no_side_effects(self):
        self.success(self.invoke("status"))
        self.assertFalse((self.root / ".release-driver").exists())
        self.success(self.invoke())
        before = (self.root / ".release-driver/state.json").read_bytes()
        self.success(self.invoke("status"))
        self.assertEqual(
            (self.root / ".release-driver/state.json").read_bytes(), before
        )

    def test_plan_drift_is_rejected_before_any_new_helper(self):
        self.success(self.invoke())
        self.data["steps"][0]["argv"][-1] = "different"
        self.write_plan()
        result = self.invoke()
        self.assertIn("Plan changed", result.stderr)
        self.assertEqual(len((self.root / "events").read_text().splitlines()), 1)

    def test_another_plan_filename_cannot_repeat_actions_in_the_same_record(self):
        self.success(self.invoke())
        alias = self.root / "another-plan.json"
        alias.write_bytes(self.plan.read_bytes())
        result = subprocess.run(
            [sys.executable, str(CLI), "run", "--plan", str(alias)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another plan owns", result.stderr)
        self.assertEqual(len((self.root / "events").read_text().splitlines()), 1)

    def test_missing_state_does_not_reset_retained_action_logs(self):
        self.success(self.invoke())
        (self.root / ".release-driver/state.json").unlink()
        result = self.invoke()
        self.assertIn("State is missing", result.stderr)
        self.assertIn("State is missing", self.invoke("status").stderr)
        self.assertEqual(len((self.root / "events").read_text().splitlines()), 1)

    def test_old_success_does_not_waive_a_fresh_failed_check(self):
        self.data["steps"] = [
            self.step("check", "check", mode="conditional"),
            self.step("publish", needs=["check"]),
        ]
        self.write_plan()
        (self.root / "allow").touch()
        self.success(self.invoke())
        (self.root / "allow").unlink()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(self.state()["steps"]["check"]["status"], "failed")
        self.assertEqual(len(self.state()["steps"]["publish"]["attempts"]), 1)

    def test_helper_drift_is_rejected(self):
        self.helper.write_text('raise RuntimeError("changed")')
        self.assertIn("Pinned helper changed", self.invoke().stderr)
        self.assertFalse((self.root / ".release-driver/state.json").exists())

    def test_missing_executable_records_intent_and_does_not_retry(self):
        self.data["steps"][0]["argv"] = ["/no/such/release-executable"]
        self.write_plan()
        self.assertNotEqual(self.invoke().returncode, 0)
        record = self.state()["steps"]["first"]
        self.assertEqual(record["status"], "running")
        self.assertEqual(len(record["attempts"]), 1)
        self.assertIn("requires reconciliation", self.invoke().stderr)
        self.assertEqual(len(self.state()["steps"]["first"]["attempts"]), 1)

    def test_lock_prevents_duplicate_driver_and_status_remains_readable(self):
        self.data["steps"] = [self.step("publish", mode="wait")]
        self.write_plan()
        child = self.start()
        self.wait_for(self.root / "ready")
        self.assertIn("Another driver owns", self.invoke().stderr)
        self.success(self.invoke("status"))
        child.terminate()
        self.assertEqual(child.wait(timeout=10), 130)
        self.assertEqual(self.state()["steps"]["publish"]["status"], "interrupted")
        self.assertIn("requires reconciliation", self.invoke().stderr)

    def test_interrupted_read_only_check_can_resume(self):
        self.data["steps"] = [self.step("verify", "check", mode="wait")]
        self.write_plan()
        child = self.start()
        self.wait_for(self.root / "ready")
        child.terminate()
        self.assertEqual(child.wait(timeout=10), 130)
        state = self.state()
        self.assertEqual(state["steps"]["verify"]["status"], "interrupted")
        # Changing an external readiness condition is safe for a read-only check.
        # The startup reset is observable by invoking the same helper again.
        resumed = self.start()
        end = time.monotonic() + 10
        while len(self.state()["steps"]["verify"]["attempts"]) < 2:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        resumed.terminate()
        self.assertEqual(resumed.wait(timeout=10), 130)
        self.assertEqual(len(self.state()["steps"]["verify"]["attempts"]), 2)

    def test_invalid_graphs_and_moving_source_are_rejected(self):
        cases = [
            [self.step("a", needs=["missing"])],
            [self.step("a", needs=["b"]), self.step("b", needs=["a"])],
            [self.step("a"), self.step("a")],
        ]
        for steps in cases:
            with self.subTest(steps=steps):
                self.data["steps"] = steps
                self.write_plan()
                self.assertNotEqual(self.invoke().returncode, 0)
        self.data["steps"] = [self.step("a")]
        self.data["release"]["source_sha"] = "main"
        self.write_plan()
        self.assertIn("Full source_sha", self.invoke().stderr)

    def test_failure_stops_local_children_without_dispatching_dependents(self):
        self.data["steps"] = [
            self.step("observer", mode="wait"),
            self.step("failing", mode="fail"),
            self.step("publish", needs=["failing"]),
        ]
        self.write_plan()
        self.assertNotEqual(self.invoke("run", "--jobs", "2").returncode, 0)
        state = self.state()["steps"]
        self.assertEqual(state["failing"]["status"], "failed")
        self.assertEqual(state["observer"]["status"], "interrupted")
        self.assertEqual(state["publish"]["status"], "pending")
        self.assertEqual(state["publish"]["attempts"], [])

    def create_ga_helpers(self):
        (self.root / "release_ops.py").write_text(
            f'SOURCE = {"a" * 40!r}\nTOOLS = {"b" * 40!r}\nVERSION = "0.10.119"\n'
        )
        for name in [
            "qualify_and_start_ga",
            "watch_promotions",
            "watch_distributions",
            "finish_cli_acceptance",
            "finish_mobile_acceptance",
            "start_authority_when_ready",
            "website_after_publication",
            "postpublication_readback",
            "public_bytes_after_publication",
            "finish_ga",
        ]:
            (self.root / f"{name}.py").write_text(
                'raise RuntimeError("must not run during prepare")'
            )

    def test_prepare_ga_pins_helpers_without_executing_or_overwriting(self):
        self.create_ga_helpers()
        path = self.root / "ga.json"
        plan = driver.prepare_ga(path, "0.10.119", "a" * 40, "b" * 40)
        self.assertEqual(len(plan["steps"]), 10)
        by_id = {s["id"]: s for s in plan["steps"]}
        self.assertEqual(by_id["authority"]["needs"], ["promotions", "cli", "mobile"])
        self.assertIn("website", by_id["seal"]["needs"])
        self.assertTrue(all(s["kind"] == "action" for s in plan["steps"]))
        self.assertEqual(plan["files"]["helper.py"], driver.digest(self.helper))
        self.assertFalse((path.parent / ".release-driver").exists())
        with self.assertRaises(driver.DriverError):
            driver.prepare_ga(path, "0.10.119", "a" * 40, "b" * 40)

    def test_prepare_refuses_an_already_started_release(self):
        self.create_ga_helpers()
        (self.root / "promote-dispatch.json").write_text("{}")
        with self.assertRaisesRegex(driver.DriverError, "Existing operations"):
            driver.prepare_ga(self.root / "ga.json", "0.10.119", "a" * 40, "b" * 40)

    def test_prepare_refuses_missing_helper(self):
        with self.assertRaisesRegex(driver.DriverError, "Missing GA helper"):
            driver.prepare_ga(self.root / "ga.json", "0.10.119", "a" * 40, "b" * 40)

    def test_prepare_rejects_mismatched_source_tools_and_version_without_importing_helpers(
        self,
    ):
        self.create_ga_helpers()
        for version, source, tools in [
            ("0.10.120", "a" * 40, "b" * 40),
            ("0.10.119", "c" * 40, "b" * 40),
            ("0.10.119", "a" * 40, "c" * 40),
        ]:
            with self.subTest(version=version, source=source, tools=tools):
                with self.assertRaisesRegex(driver.DriverError, "does not match"):
                    driver.prepare_ga(self.root / "ga.json", version, source, tools)
                self.assertFalse((self.root / "ga.json").exists())


if __name__ == "__main__":
    unittest.main()
