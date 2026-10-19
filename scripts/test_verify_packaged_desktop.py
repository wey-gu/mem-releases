"""Fail-closed parser, execution and aggregate guards; not native package proof."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
import unittest
from types import SimpleNamespace

from desktop_version_receipts import accept, validate_build_identity
from verify_packaged_desktop import PLATFORM_ARTIFACTS, PLATFORMS, command, exact_version, installed_cli, runtime_is_ready, unique
from verify_packaged_desktop import validate_server_output, verify
from verify_packaged_desktop import cli_command
from verify_packaged_desktop import validate_windows_package_record, windows_package_registration


class PackagedVersionContract(unittest.TestCase):
    def test_windows_installer_and_executable_must_share_identity(self):
        app = r'C:\Users\fixture\Nowledge Mem\nowledge-mem.exe'
        good = {'DisplayName':'Nowledge Mem', 'DisplayVersion':'0.10.104',
                'InstallLocation':r'"C:\Users\fixture\Nowledge Mem"', 'MainBinaryName':'nowledge-mem.exe'}
        validate_windows_package_record(good, app, '0.10.104')
        for field, bad in (('DisplayVersion','0.10.103'), ('DisplayVersion','0.0.0'),
                           ('InstallLocation',r'C:\other\Nowledge Mem'), ('MainBinaryName','other.exe')):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                validate_windows_package_record(dict(good, **{field:bad}), app, '0.10.104')

    def test_server_marker_must_be_unique_and_correct(self):
        sha = 'a' * 40
        good = f'nmem-server 0.10.104 (aaaaaaaaaaaa, built 1)\nnmem-build-sha:{sha}'
        validate_server_output(good, '0.10.104', sha)
        for bad in ('', 'nmem-server 0.10.103', good + '\nnmem-build-sha:' + 'b' * 40,
                    good + f'\nnmem-build-sha:{sha}'):
            with self.subTest(output=bad), self.assertRaises(RuntimeError):
                validate_server_output(bad, '0.10.104', sha)

    def test_public_liveness_does_not_replace_detailed_readiness(self):
        healthy = {"status": "ok", "database_connected": True, "services_ready": True}
        self.assertTrue(runtime_is_ready(healthy))
        for field, bad in (("status", "error"), ("database_connected", False),
                           ("services_ready", False), ("services_ready", "true")):
            with self.subTest(field=field, bad=bad):
                self.assertFalse(runtime_is_ready(dict(healthy, **{field: bad})))
        self.assertFalse(runtime_is_ready({"status": "ok"}))

    def test_same_source_other_rc_run_is_rejected(self):
        run = {"path": ".github/workflows/release-desktop.yml", "status": "completed", "conclusion": "success",
               "event": "push", "head_branch": "v0.10.104-rc2", "head_sha": "c" * 40, "run_attempt": 1}
        validate_build_identity(run, "v0.10.104-rc2")
        with self.assertRaises(RuntimeError):
            validate_build_identity(dict(run, head_branch="v0.10.104-rc1"), "v0.10.104-rc2")
        with self.assertRaises(RuntimeError):
            validate_build_identity(dict(run, event="workflow_dispatch"), "v0.10.104-rc2")

    def test_existing_wrapper_without_terminal_path_registration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / ".local/bin/nmem"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("#!/bin/sh\nexit 0\n")
            wrapper.chmod(0o755)
            env = {"HOME": directory, "PATH": str(wrapper.parent)}
            with patch("verify_packaged_desktop.terminal_path", return_value="/usr/bin"):
                self.assertIsNone(installed_cli(env))
            with patch("verify_packaged_desktop.terminal_path", return_value=str(wrapper.parent)):
                self.assertEqual(installed_cli(env), wrapper)

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
        identity = {"release_tag": "v0.10.104-rc2", "build_workflow_sha": "c" * 40, "build_attempt": "1",
                    "artifacts": {name: str(i + 1) for i, name in enumerate(set(PLATFORM_ARTIFACTS.values()))}}
        template = {
            "schema": 1, "passed": True, "version": "0.10.104", "source_sha": sha,
            "build_run_id": "123", "package_sha256": "b" * 64,
            "health_details": {"version": "0.10.104", "build_sha": sha, "status": "ok",
                               "database_connected": True, "services_ready": True},
            "cli_output": "nmem 0.10.104",
            "server_output": f"nmem-server 0.10.104 (aaaaaaaaaaaa, built 123)\nnmem-build-sha:{sha}\nnmem-storage-build:public",
            "release_tag": identity["release_tag"], "build_workflow_sha": identity["build_workflow_sha"],
            "build_attempt": identity["build_attempt"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            originals = {}
            for platform in PLATFORMS:
                receipt = dict(template, platform=platform, app_output=f"Nowledge Mem 0.10.104 ({platform})",
                               artifact_id=identity["artifacts"][PLATFORM_ARTIFACTS[platform]])
                if platform == 'win':
                    receipt['app_command'] = [r'C:\fixture\Nowledge Mem\nowledge-mem.exe']
                    receipt['windows_package_registration'] = {
                        'DisplayName':'Nowledge Mem', 'DisplayVersion':'0.10.104',
                        'InstallLocation':r'"C:\fixture\Nowledge Mem"', 'MainBinaryName':'nowledge-mem.exe'}
                path = root / f"{platform}.json"
                originals[path] = receipt
                path.write_text(json.dumps(receipt))
            self.assertEqual(accept(root, "0.10.104", sha, "123", identity), set(PLATFORMS))
            windows = root / 'win.json'
            mutant = copy.deepcopy(originals[windows])
            mutant['windows_package_registration']['DisplayVersion'] = '0.10.103'
            windows.write_text(json.dumps(mutant))
            with self.assertRaisesRegex(RuntimeError, 'version'):
                accept(root, "0.10.104", sha, "123", identity)
            windows.write_text(json.dumps(originals[windows]))
            first = next(iter(originals))
            for field, bad in (("passed", False), ("version", "0.0.0"), ("source_sha", "c" * 40),
                               ("build_run_id", "122"), ("package_sha256", ""),
                               ("release_tag", "v0.10.104-rc1"), ("artifact_id", "999"),
                               ("build_workflow_sha", "d" * 40), ("build_attempt", "2"),
                               ("server_output", ""), ("server_output", "nmem-server 0.10.103"),
                               ("platform", "win"), ("app_output", "Nowledge Mem 0 (mac)"),
                               ("cli_output", "nmem 0.10.103"),
                               ("health_details", {"version": "0.10.104", "build_sha": "unknown"})):
                mutant = copy.deepcopy(originals[first])
                mutant[field] = bad
                first.write_text(json.dumps(mutant))
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    accept(root, "0.10.104", sha, "123", identity)
            mutant = copy.deepcopy(originals[first])
            mutant['health_details'].update(status='error', database_connected=False, services_ready=False)
            first.write_text(json.dumps(mutant))
            with self.assertRaises(RuntimeError):
                accept(root, "0.10.104", sha, "123", identity)
            first.unlink()
            with self.assertRaises(RuntimeError):
                accept(root, "0.10.104", sha, "123", identity)


@unittest.skipUnless(os.name == 'nt', 'Requires real Windows cmd.exe')
class WindowsCommandContract(unittest.TestCase):
    def test_real_registry_readback_requires_installed_version(self):
        import uuid
        import winreg

        key = 'Software\\NowledgeMemReleaseAcceptance-' + str(uuid.uuid4())
        app = r'C:\fixture\Nowledge Mem\nowledge-mem.exe'
        handle = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY)
        try:
            with handle:
                for name, value in {'DisplayName':'Nowledge Mem', 'DisplayVersion':'0.10.104',
                                    'InstallLocation':r'"C:\fixture\Nowledge Mem"',
                                    'MainBinaryName':'nowledge-mem.exe'}.items():
                    winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)
                self.assertEqual(windows_package_registration(app, '0.10.104', key)['DisplayVersion'], '0.10.104')
                winreg.SetValueEx(handle, 'DisplayVersion', 0, winreg.REG_SZ, '0.10.103')
                with self.assertRaises(RuntimeError):
                    windows_package_registration(app, '0.10.104', key)
        finally:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, key, winreg.KEY_WOW64_64KEY)

    def test_spaced_wrapper_path_stdout_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory(prefix='nmem CLI path ') as directory:
            wrapper = Path(directory) / 'nmem.cmd'
            wrapper.write_text('@echo off\necho nmem 0.10.104\nexit /b 0\n')
            self.assertEqual(command(cli_command(wrapper)), 'nmem 0.10.104')
            wrapper.write_text('@echo off\nexit /b 17\n')
            with self.assertRaises(subprocess.CalledProcessError):
                command(cli_command(wrapper))


class ProducerFailureContract(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        root = Path(self.scratch.name)
        package = root / 'candidate.dmg'
        package.write_bytes(b'package identity fixture')
        self.sha = 'a' * 40
        self.args = SimpleNamespace(
            package=package, app=None, platform='mac', version='0.10.104', source_sha=self.sha,
            release_tag='v0.10.104-rc2', build_run_id='123', build_workflow_sha='c' * 40,
            build_attempt='1', artifact_id='1', work=root/'work', receipt=root/'receipt.json',
        )
        self.details = {'status': 'ok', 'database_connected': True, 'services_ready': True,
                        'version': '0.10.104', 'build_sha': self.sha}
        self.outputs = ['Nowledge Mem 0.10.104 (mac)',
                        f'nmem-server 0.10.104 (aaaaaaaaaaaa, built 1)\nnmem-build-sha:{self.sha}',
                        'nmem 0.10.104']
        patches = {
            'installed_app': {'return_value': ([root/'App'], root, {})},
            'bundled_server': {'return_value': root/'server'},
            'installed_cli': {'return_value': root/'nmem'},
            'command': {'side_effect': self.outputs},
            'get_json': {'side_effect': lambda port, route: {'status':'ok'} if route == '/health' else self.details},
            'time.monotonic': {'side_effect': [0, 0, 181]},
            'time.sleep': {}, 'os.killpg': {}, 'subprocess.Popen': {}, 'socket.socket': {},
        }
        self.mocks = {}
        for name, options in patches.items():
            patcher = patch('verify_packaged_desktop.' + name, **options)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks['socket.socket'].return_value.__enter__.return_value.connect_ex.return_value = 1
        proc = self.mocks['subprocess.Popen'].return_value
        proc.poll.return_value = None
        proc.wait.return_value = 0
        patcher = patch.dict(os.environ, {'GITHUB_ACTIONS':'true'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_healthy_control_writes_candidate_bound_receipt(self):
        verify(self.args)
        receipt = json.loads(self.args.receipt.read_text())
        self.assertTrue(receipt['passed'])
        self.assertEqual(receipt['source_sha'], self.sha)

    def test_windows_registration_mismatch_never_writes_success(self):
        self.args.platform = 'win'
        app = r'C:\fixture\Nowledge Mem\nowledge-mem.exe'
        self.mocks['installed_app'].return_value = ([app], self.args.package.parent, {})
        self.mocks['command'].side_effect = ['Nowledge Mem 0.10.104 (win)', *self.outputs[1:]]
        record = {'DisplayName':'Nowledge Mem', 'DisplayVersion':'0.10.103',
                  'InstallLocation':r'"C:\fixture\Nowledge Mem"', 'MainBinaryName':'nowledge-mem.exe'}

        def read_registration(executable, version):
            validate_windows_package_record(record, executable, version)
            return record

        with patch('verify_packaged_desktop.windows_package_registration', side_effect=read_registration) as read:
            with self.assertRaisesRegex(RuntimeError, 'version'):
                verify(self.args)
            read.assert_called_once_with(app, '0.10.104')
        self.assertFalse(self.args.receipt.exists())

    def test_not_ready_same_version_and_sha_never_writes_success(self):
        self.details.update(status='error', database_connected=False, services_ready=False)
        with self.assertRaisesRegex(RuntimeError, 'Readiness timeout'):
            verify(self.args)
        self.assertFalse(self.args.receipt.exists())
        self.mocks['installed_cli'].assert_not_called()

    def test_app_exit_never_writes_success(self):
        self.mocks['subprocess.Popen'].return_value.poll.return_value = 17
        with self.assertRaisesRegex(RuntimeError, 'App exited'):
            verify(self.args)
        self.assertFalse(self.args.receipt.exists())

    def test_probe_timeout_never_writes_success(self):
        self.mocks['command'].side_effect = subprocess.TimeoutExpired('App --version', 20)
        with self.assertRaises(subprocess.TimeoutExpired):
            verify(self.args)
        self.assertFalse(self.args.receipt.exists())

    def test_wrong_installed_cli_version_never_writes_success(self):
        self.mocks['command'].side_effect = self.outputs[:2] + ['nmem 0.10.103']
        with self.assertRaisesRegex(RuntimeError, 'version'):
            verify(self.args)
        self.assertFalse(self.args.receipt.exists())


if __name__ == "__main__":
    unittest.main()
