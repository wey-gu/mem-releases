"""Exercise payload identity with controlled metadata, without a package build."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rpm_repack


class PayloadIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rpm-payload-fixture-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.source = root / "source"
        self.payload = root / "payload"
        tauri = self.source / "nowledge-graph/src-tauri"
        scripts = tauri / "scripts"
        scripts.mkdir(parents=True)
        config = {
            "files": {
                "/usr/share/nowledge-mem/install-cli.sh": "scripts/install-cli.sh"
            },
            "postInstallScript": "scripts/post.sh",
            "preRemoveScript": "scripts/pre.sh",
            "postRemoveScript": "scripts/remove.sh",
        }
        self.script_contents = "#!/bin/sh\nexit 0\n"
        for name in ("install-cli.sh", "post.sh", "pre.sh", "remove.sh"):
            path = scripts / name
            path.write_text(self.script_contents)
            path.chmod(0o755)
        (tauri / "tauri.linux.conf.json").write_text(
            json.dumps({"bundle": {"linux": {"rpm": config}}})
        )
        binary = self.payload / "usr/bin/nowledge-mem"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"qualified executable bytes")
        binary.chmod(0o755)
        self.backend = self.payload / "usr/lib/Nowledge Mem/_up_/rust-backend"
        self.backend.mkdir(parents=True)
        server = self.backend / "nmem-server"
        server.write_bytes(b"qualified public backend bytes")
        server.chmod(0o755)
        web = self.backend / "web-dist"
        web.mkdir()
        (web / "index-web.html").write_text("Qualified Web entry point")
        self.installed_script = self.payload / "usr/share/nowledge-mem/install-cli.sh"
        self.installed_script.parent.mkdir(parents=True)
        self.installed_script.write_text(self.script_contents)
        self.installed_script.chmod(0o755)
        self.receipt = root / "receipt.json"
        self.receipt.write_text(
            json.dumps(
                {
                    "version": "0.10.99",
                    "executable": rpm_repack.inventory(binary.parent),
                    "backend": rpm_repack.inventory(self.backend),
                }
            )
        )
        self.rpm = root / "package.rpm"
        self.rpm.write_bytes(b"controlled package metadata fixture")
        for directory in self.payload.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)

    def verify(self):
        def query(args, **kwargs):
            if "%{VERSION}" in args[3]:
                return "0.10.99\nx86_64\ngzip\n1\n"
            return self.script_contents

        with (
            patch.object(rpm_repack.subprocess, "check_output", side_effect=query),
            patch.object(rpm_repack.subprocess, "run"),
            patch.object(rpm_repack, "verify_archive_digest", return_value="0" * 64),
        ):
            rpm_repack.verify(self.source, self.payload, self.rpm, self.receipt)

    def test_normal_payload_is_accepted(self):
        self.verify()
        self.assertTrue(json.loads(self.receipt.read_text())["rpm_verified"])

    def test_mapped_script_mode_tamper_is_rejected(self):
        self.installed_script.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "permissions differ"):
            self.verify()

    @unittest.skipUnless(
        hasattr(os, "mkfifo"), "POSIX payload validation requires mkfifo"
    )
    def test_unexpected_backend_fifo_is_rejected(self):
        os.mkfifo(self.backend / "unexpected-fifo")
        with self.assertRaisesRegex(ValueError, "Unsupported payload entry"):
            self.verify()

    def test_backend_symlink_is_rejected(self):
        (self.backend / "unexpected-link").symlink_to(self.backend / "nmem-server")
        with self.assertRaisesRegex(ValueError, "Unsupported payload entry"):
            self.verify()

    def test_private_backend_root_is_rejected(self):
        self.backend.chmod(0o700)
        with self.assertRaisesRegex(ValueError, "runtime directory"):
            self.verify()

    def test_private_backend_subdirectory_is_rejected(self):
        (self.backend / "web-dist").chmod(0o700)
        with self.assertRaisesRegex(ValueError, "runtime directory"):
            self.verify()

    def test_private_mapped_script_parent_is_rejected(self):
        self.installed_script.parent.chmod(0o700)
        with self.assertRaisesRegex(ValueError, "runtime directory"):
            self.verify()

    def test_private_executable_parent_is_rejected(self):
        (self.payload / "usr/bin").chmod(0o700)
        with self.assertRaisesRegex(ValueError, "runtime directory"):
            self.verify()

    def test_unused_empty_backend_directory_may_be_omitted(self):
        (self.backend / "unused-empty").mkdir()
        self.verify()
        self.assertTrue(json.loads(self.receipt.read_text())["rpm_verified"])


if __name__ == "__main__":
    unittest.main()
