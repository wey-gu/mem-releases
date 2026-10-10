"""Protect strict archive verification across RPM 4.17's legacy exit status."""

import hashlib
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from verify_rpm_archive import verify_archive_digest


class ArchiveDigestTests(unittest.TestCase):
    def verify(
        self, status=0, size="(none)", payload=b"archive", expected=None, algorithm="8"
    ):
        if expected is None:
            expected = hashlib.sha256(b"archive").hexdigest()
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.BytesIO(payload)
        process.wait.return_value = status
        with (
            patch(
                "verify_rpm_archive.subprocess.check_output",
                return_value=f"{expected}\n{algorithm}\n{size}\n",
            ),
            patch("verify_rpm_archive.subprocess.Popen", return_value=process),
        ):
            return verify_archive_digest(Path("package.rpm"))

    def test_successful_converter(self):
        self.assertEqual(self.verify(), hashlib.sha256(b"archive").hexdigest())

    def test_legacy_exit_requires_matching_digest_and_absent_size(self):
        self.assertEqual(
            self.verify(status=1), hashlib.sha256(b"archive").hexdigest()
        )

    def test_legacy_exit_rejects_wrong_digest(self):
        with self.assertRaisesRegex(ValueError, "digest mismatch.*rpm2cpio exit 1"):
            self.verify(status=1, payload=b"corrupt")

    def test_successful_exit_rejects_truncated_payload(self):
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.verify(payload=b"arch")

    def test_nonzero_exit_with_declared_size_is_fatal(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.verify(status=1, size="7")

    def test_other_converter_errors_remain_fatal(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.verify(status=2)

    def test_missing_digest_is_fatal(self):
        with self.assertRaisesRegex(ValueError, "must declare a SHA-256"):
            self.verify(expected="(none)")

    def test_other_digest_algorithm_is_fatal(self):
        with self.assertRaisesRegex(ValueError, "must declare a SHA-256"):
            self.verify(algorithm="1")


if __name__ == "__main__":
    unittest.main()
