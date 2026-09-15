import copy
import unittest

from validate_desktop_recovery import BUILD_ARTIFACTS, validate_receipt, validate_runs


class DesktopRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "status": "completed",
            "conclusion": "cancelled",
            "path": ".github/workflows/release-desktop.yml",
            "event": "workflow_dispatch",
            "head_branch": "v0.10.82",
            "head_sha": "a" * 40,
        }
        self.jobs = [
            {"name": name, "conclusion": "success"} for name in BUILD_ARTIFACTS
        ]
        self.jobs.append({"name": "publish", "conclusion": "skipped", "steps": []})
        self.recovery = {
            "status": "completed",
            "conclusion": "success",
            "path": ".github/workflows/rpm-test.yml",
            "event": "workflow_dispatch",
            "head_sha": "b" * 40,
        }
        self.receipt = {
            "version": "0.10.82",
            "source_sha": "c" * 40,
            "source_run": 123,
            "tooling_sha": "b" * 40,
            "compression": {"type": "gzip", "level": 1},
            "rpm_verified": True,
            "deb_sha256": "d" * 64,
            "rpm_sha256": "e" * 64,
            "archive_sha256": "f" * 64,
        }

    def validate(self):
        validate_runs(
            self.original, self.jobs, self.recovery, "0.10.82", "a" * 40, "b" * 40
        )

    def test_accepts_terminal_original_and_successful_reviewed_recovery(self):
        self.validate()
        validate_receipt(self.receipt, "0.10.82", "c" * 40, 123, "b" * 40)

    def test_rejects_active_original_and_wrong_provenance(self):
        for key, value in [
            ("status", "in_progress"),
            ("conclusion", "success"),
            ("path", ".github/workflows/other.yml"),
            ("head_branch", "v0.10.81"),
            ("head_sha", "x" * 40),
        ]:
            with self.subTest(key=key):
                original = dict(self.original, **{key: value})
                with self.assertRaises(ValueError):
                    validate_runs(
                        original,
                        self.jobs,
                        self.recovery,
                        "0.10.82",
                        "a" * 40,
                        "b" * 40,
                    )

    def test_rejects_failed_build_or_started_publisher(self):
        for index, changes in [
            (0, {"conclusion": "failure"}),
            (0, {"name": "missing-build"}),
            (-1, {"steps": [{"name": "Upload to Cloudflare R2"}]}),
        ]:
            with self.subTest(index=index, changes=changes):
                jobs = copy.deepcopy(self.jobs)
                jobs[index].update(changes)
                with self.assertRaises(ValueError):
                    validate_runs(
                        self.original,
                        jobs,
                        self.recovery,
                        "0.10.82",
                        "a" * 40,
                        "b" * 40,
                    )

    def test_rejects_failed_or_unreviewed_recovery(self):
        for key, value in [
            ("conclusion", "failure"),
            ("status", "in_progress"),
            ("head_sha", "x" * 40),
        ]:
            with self.subTest(key=key):
                recovery = dict(self.recovery, **{key: value})
                with self.assertRaises(ValueError):
                    validate_runs(
                        self.original,
                        self.jobs,
                        recovery,
                        "0.10.82",
                        "a" * 40,
                        "b" * 40,
                    )

    def test_rejects_uncompressed_unverified_or_unrelated_receipts(self):
        for key, value in [
            ("compression", {"type": "none"}),
            ("rpm_verified", False),
            ("source_run", 999),
            ("source_sha", "x" * 40),
            ("tooling_sha", "x" * 40),
            ("archive_sha256", ""),
        ]:
            with self.subTest(key=key):
                receipt = dict(self.receipt, **{key: value})
                with self.assertRaises(ValueError):
                    validate_receipt(receipt, "0.10.82", "c" * 40, 123, "b" * 40)


if __name__ == "__main__":
    unittest.main()
