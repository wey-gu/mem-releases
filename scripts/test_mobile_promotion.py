"""Fail closed on wrong source, uploaded producers, modified bytes and unsafe ZIPs."""

import copy
import json
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

import mobile_promotion as mobile
from upload_existing_ipa import assert_unuploaded, confirm_upload


VERSION = "0.10.121"
RC = VERSION + "-rc3"
SOURCE = "a" * 40
TOOLS = "b" * 40
DELIVERY = "52735a8c-e99f-41f6-af01-e2efbb051278"


class OriginalProducerTest(unittest.TestCase):
    def setUp(self):
        self.run = dict(id=123, path=".github/workflows/release-mobile.yml",
                        event="workflow_dispatch", status="completed", conclusion="success",
                        run_attempt=1, head_sha=TOOLS, head_branch=f"release-tools/{RC}-r1")
        self.jobs = dict(jobs=[
            dict(id=1, name="iOS -> TestFlight", conclusion="success", steps=[
                dict(name="Build, sign and upload to TestFlight", conclusion="success"),
                dict(name="Distribute the build to TestFlight", conclusion="skipped"),
            ]),
            dict(id=2, name="Android -> APK + AAB", conclusion="success", steps=[
                dict(name="Locate and verify Android artifacts", conclusion="success"),
                dict(name="Publish APK to nowledge-co/community release", conclusion="skipped"),
            ]),
        ])
        self.logs = dict(
            ios=f"git log -1 --format=%H\n{SOURCE}\ntag={VERSION} tauri.conf={VERSION} "
                f"plist.short={VERSION} plist.build={VERSION}\n"
                "==> --no-upload: stopping before App Store Connect\n",
            android=f"git log -1 --format=%H\n{SOURCE}\n",
        )

    def admit(self, **changes):
        values = dict(run=self.run, jobs=self.jobs, logs=self.logs,
                      version=VERSION, rc_tag=RC, source_sha=SOURCE, tools_sha=TOOLS)
        return mobile.verify_build_only(**(values | changes))

    def test_same_original_both_platforms_are_required(self):
        self.assertEqual(self.admit(), dict(ios=1, android=2))

    def test_rejects_another_or_retried_producer(self):
        for change in [dict(head_sha="c" * 40), dict(head_branch="main"),
                       dict(run_attempt=2), dict(conclusion="failure"),
                       dict(status="in_progress"), dict(event="push")]:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.admit(run=self.run | change)
        with self.assertRaises(RuntimeError):
            self.admit(jobs=dict(jobs=self.jobs["jobs"][:1]))

    def test_rejects_uploads_and_mismatched_source(self):
        for logs in [self.logs | dict(ios=self.logs["ios"] + "UPLOAD SUCCEEDED"),
                     self.logs | dict(android="git log -1 --format=%H\n" + "c" * 40)]:
            with self.subTest(logs=logs), self.assertRaises(RuntimeError):
                self.admit(logs=logs)
        jobs = copy.deepcopy(self.jobs)
        jobs["jobs"][1]["steps"][1]["conclusion"] = "success"
        with self.assertRaises(RuntimeError):
            self.admit(jobs=jobs)

    def test_rejects_placeholder_and_different_release_versions(self):
        for version, rc in [("0.0.0", "0.0.0-rc1"), (VERSION, "0.10.122-rc3"),
                            (VERSION, VERSION + "-ci1"), ("", RC)]:
            with self.subTest(version=version, rc=rc), self.assertRaises(RuntimeError):
                mobile.validate_identity(version, rc, SOURCE, TOOLS)


class OriginalBytesTest(unittest.TestCase):
    def test_traversal_and_duplicates_are_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["../escape", "/absolute", "C:/escape", "dir\\escape", "bad\nfile"]:
                archive = root / "input.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr(name, b"not allowed")
                with self.subTest(name=name), self.assertRaises(RuntimeError):
                    mobile.extract_checked(archive, root / "output")
                self.assertFalse((root / "output").exists())

    def test_actual_app_share_and_sdk_identities_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "original.ipa"
            for change in [{}, {"CFBundleVersion": "0.0.0"}, {"DTSDKName": "iphoneos18.5"},
                           {"CFBundleIdentifier": "another.app"}]:
                with zipfile.ZipFile(ipa, "w") as bundle:
                    for suffix, identifier in [("", mobile.BUNDLE_ID),
                                               ("/PlugIns/Nowledge Mem Share.appex", mobile.BUNDLE_ID + ".share")]:
                        value = dict(CFBundleIdentifier=identifier, CFBundleVersion=VERSION,
                                     CFBundleShortVersionString=VERSION, DTSDKName="iphoneos26.0")
                        value.update(change)
                        bundle.writestr("Payload/Nowledge Mem.app" + suffix + "/Info.plist",
                                        plistlib.dumps(value))
                if change:
                    with self.subTest(change=change), self.assertRaises(RuntimeError):
                        mobile.ipa_identity(ipa, VERSION)
                else:
                    self.assertEqual(len(mobile.ipa_identity(ipa, VERSION)), 2)

    def test_aliases_and_symlinks_do_not_write_a_partial_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "input.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("File", b"one")
                bundle.writestr("./file", b"two")
            with self.assertRaises(RuntimeError):
                mobile.extract_checked(archive, root / "output")
            link = zipfile.ZipInfo("link")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(link, "../../escape")
            with self.assertRaises(RuntimeError):
                mobile.extract_checked(archive, root / "output")
            self.assertFalse((root / "output").exists())

    def test_modified_promoted_file_is_not_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "original.apk"
            path.write_bytes(b"signed original bytes")
            receipt = dict(passed=True, version=VERSION, source_sha=SOURCE, files=[
                dict(path=path.name, size=path.stat().st_size, sha256=mobile.checksum(path))])
            (root / "admission.json").write_text(json.dumps(receipt))
            mobile.verify_files(root, VERSION, SOURCE)
            path.write_bytes(b"different bytes")
            with self.assertRaises(RuntimeError):
                mobile.verify_files(root, VERSION, SOURCE)


class UploadBoundaryTest(unittest.TestCase):
    def test_exit_zero_without_explicit_success_is_not_upload_evidence(self):
        success = "UPLOAD SUCCEEDED with no errors\nDelivery UUID: " + DELIVERY
        self.assertEqual(confirm_upload(0, success), DELIVERY)
        for code, text in [(1, success), (0, "UPLOAD FAILED"), (0, ""),
                           (0, success + "\nERROR: rejected"),
                           (0, "UPLOAD SUCCEEDED with no errors")]:
            with self.subTest(code=code, text=text), self.assertRaises(RuntimeError):
                confirm_upload(code, text)

    def test_existing_store_build_stops_before_another_upload(self):
        client = Mock()
        client.call.return_value = dict(data=[])
        assert_unuploaded(client, "app", VERSION)
        client.call.return_value = dict(data=[dict(id=DELIVERY)])
        with self.assertRaises(RuntimeError):
            assert_unuploaded(client, "app", VERSION)
        self.assertTrue(all(call.args[0] == "GET" for call in client.call.call_args_list))


if __name__ == "__main__":
    unittest.main()
