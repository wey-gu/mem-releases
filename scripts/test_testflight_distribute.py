"""Reject recovery of a different app, source, version or uploaded build."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("testflight", Path(__file__).with_name("testflight-distribute.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SOURCE = "a" * 40
BUILD = "52735a8c-e99f-41f6-af01-e2efbb051278"
VERSION = "0.10.117"


class UploadAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.run = dict(id=123, event="workflow_dispatch", path=".github/workflows/release-mobile.yml", status="completed")
        self.jobs = dict(jobs=[dict(name="iOS -> TestFlight", steps=[dict(name="Build, sign and upload to TestFlight", conclusion="success")])])
        self.log = f"git log -1 --format=%H\n{SOURCE}\ntag={VERSION} tauri.conf={VERSION} plist.short={VERSION} plist.build={VERSION}\nUPLOAD SUCCEEDED with no errors\nDelivery UUID: {BUILD}\n"

    def admit(self, **overrides):
        values = dict(run=self.run, jobs=self.jobs, log=self.log, version=VERSION, source_sha=SOURCE, build_id=BUILD)
        return module.verify_original_upload(**(values | overrides))

    def test_accepts_only_the_original_successful_upload(self):
        self.assertEqual(self.admit()["build_id"], BUILD)

    def test_exact_rc_promotion_upload_can_resume_without_reupload(self):
        run = self.run | dict(path=".github/workflows/promote-mobile-to-ga.yml")
        jobs = dict(jobs=[dict(name="iOS -> TestFlight", steps=[
            dict(name="Upload the exact signed RC IPA", conclusion="success")])])
        self.assertEqual(self.admit(run=run, jobs=jobs)["build_id"], BUILD)
        with self.assertRaises(RuntimeError):
            self.admit(run=run)

    def test_legacy_warning_parser_failure_resumes_only_the_exact_released_tools(self):
        run = self.run | dict(path=".github/workflows/promote-mobile-to-ga.yml",
                              head_sha="520eea6effa0a08ca9d3150134ca983b558903e8", run_attempt=1)
        jobs = dict(jobs=[dict(name="iOS -> TestFlight", steps=[
            dict(name="Upload the exact signed RC IPA", conclusion="failure")])])
        log = self.log.replace("UPLOAD SUCCEEDED with no errors",
            "UPLOAD SUCCEEDED with 1 warning, 0 messages") + (
                "No errors uploading 'original.ipa'\nSDK version issue. (90725)\n"
                "RuntimeError: App Store upload was not confirmed; preserve logs before recovery\n")
        self.assertTrue(self.admit(run=run, jobs=jobs, log=log)["legacy_warning_receipt_recovered"])
        for change in [dict(run=run | dict(head_sha="b" * 40)),
                       dict(run=run | dict(run_attempt=2)),
                       dict(log=log.replace("(90725)", "(other)")),
                       dict(log=log.replace("RuntimeError: App Store upload was not confirmed; preserve logs before recovery", "")),
                       dict(log=log.replace("No errors uploading 'original.ipa'", ""))]:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.admit(**(dict(run=run, jobs=jobs, log=log) | change))

    def test_rejects_wrong_source_version_delivery_and_unsigned_upload(self):
        bad_jobs = copy.deepcopy(self.jobs)
        bad_jobs["jobs"][0]["steps"][0]["conclusion"] = "failure"
        for change in [dict(source_sha="b" * 40), dict(version="0.10.118"), dict(build_id="11111111-1111-4111-8111-111111111111"), dict(jobs=bad_jobs), dict(log=self.log.replace("UPLOAD SUCCEEDED with no errors", "UPLOAD FAILED"))]:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.admit(**change)

    def test_rejects_non_mobile_and_running_jobs(self):
        for change in [dict(path=".github/workflows/release-desktop.yml"), dict(status="in_progress")]:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.admit(run=self.run | change)

    def test_app_selection_is_bundle_scoped_and_unambiguous(self):
        client = Mock()
        app = dict(id="mem", attributes=dict(bundleId=module.BUNDLE_ID))
        client.call.return_value = dict(data=[app])
        self.assertEqual(module.find_app(client), "mem")
        self.assertIn("filter[bundleId]=" + module.BUNDLE_ID, client.call.call_args.args[1])
        for apps in [[], [app, app], [dict(id="other", attributes=dict(bundleId="other.app"))]]:
            client.call.return_value = dict(data=apps)
            with self.assertRaises(RuntimeError):
                module.find_app(client)

    def test_recovery_rejects_wrong_build_and_version_before_mutation(self):
        client = Mock()
        build = dict(id=BUILD, attributes=dict(version=VERSION, processingState="VALID"))
        client.call.return_value = dict(data=[build])
        self.assertEqual(module.wait_for_valid_build(client, "mem", VERSION, BUILD)["id"], BUILD)
        for bad in [build | dict(id="other"), build | dict(attributes=dict(version="0.10.118", processingState="VALID"))]:
            client.call.return_value = dict(data=[bad])
            with self.assertRaises(RuntimeError):
                module.wait_for_valid_build(client, "mem", VERSION, BUILD)
        self.assertTrue(all(call.args[0] == "GET" for call in client.call.call_args_list))


if __name__ == "__main__":
    unittest.main()
