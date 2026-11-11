"""Check source isolation and fail-closed runtime installation boundaries."""

from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile
import unittest

from install_desktop_version_runtime import PACKAGES, install_runtime, runtime_dns


class RuntimeInstallation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.keyring = self.root / 'ubuntu.gpg'
        self.keyring.write_bytes(b'fixture-only; apt is mocked')
        self.package = self.root / 'original package.deb'
        self.package.write_bytes(b'original fixture')
        self.commands = []
        self.sources = []
        self.lists = []

    def record_command(self, command, **kwargs):
        self.commands.append(command)
        self.assertTrue(kwargs['check'])
        self.assertEqual(kwargs['timeout'], 300)
        self.assertEqual(kwargs['env']['DEBIAN_FRONTEND'], 'noninteractive')
        source = Path(next(x.split('=', 1)[1] for x in command
                           if x.startswith('Dir::Etc::sourcelist=')))
        self.sources.append(source.read_text())
        lists = Path(next(x.split('=', 1)[1] for x in command
                          if x.startswith('Dir::State::lists=')))
        self.lists.append(lists)
        self.assertEqual(list(lists.iterdir()), [lists / 'partial'])
        self.assertIn('Dir::Etc::sourceparts=-', command)
        self.assertIn('APT::Update::Error-Mode=any', command)
        self.assertIn('Acquire::Retries=2', command)
        self.assertIn('Acquire::https::Timeout=15', command)
        self.assertFalse(any('allow-unauthenticated' in x for x in command))

    def install(self, surface='linux-appimage', package=None, **kwargs):
        install_runtime(surface, package,
                        release=kwargs.pop('release', {'ID': 'ubuntu', 'VERSION_ID': '22.04'}),
                        keyring=self.keyring, run=kwargs.pop('run', self.record_command),
                        dns_context=nullcontext, **kwargs)

    def test_appimage_uses_signed_official_sources_and_fresh_lists(self):
        self.install()
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(self.commands[1][-len(PACKAGES):], list(PACKAGES))
        self.assertEqual(len(set(self.sources)), 1)
        for line in self.sources[0].splitlines():
            self.assertIn(f'signed-by={self.keyring}', line)
            self.assertRegex(line, r'https://(archive|security)\.ubuntu\.com/ubuntu')
        self.assertNotIn('azure', self.sources[0])
        self.assertNotIn('trusted=yes', self.sources[0])
        self.assertFalse(self.lists[0].exists())

    def test_deb_installs_exact_original_path_as_one_argument(self):
        self.install('linux-deb', self.package)
        self.assertEqual(len(self.commands), 3)
        self.assertEqual(self.commands[-1][-1], str(self.package.resolve()))
        self.assertEqual(self.package.read_bytes(), b'original fixture')

    def test_update_failure_never_installs_packages(self):
        def fail(command, **kwargs):
            self.record_command(command, **kwargs)
            raise subprocess.CalledProcessError(100, command)
        with self.assertRaises(subprocess.CalledProcessError):
            self.install(run=fail)
        self.assertEqual(len(self.commands), 1)
        self.assertFalse(self.lists[0].exists())

    def test_install_timeout_propagates(self):
        def timeout(command, **kwargs):
            self.record_command(command, **kwargs)
            if len(self.commands) == 2:
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        with self.assertRaises(subprocess.TimeoutExpired):
            self.install(run=timeout)
        self.assertEqual(len(self.commands), 2)
        self.assertFalse(self.lists[0].exists())

    def test_unknown_release_fails_before_apt(self):
        with self.assertRaises(ValueError):
            self.install(release={'ID': 'ubuntu', 'VERSION_ID': '24.04'})
        self.assertEqual(self.commands, [])

    def test_missing_original_deb_fails_before_apt(self):
        with self.assertRaises(ValueError):
            self.install('linux-deb')
        self.assertEqual(self.commands, [])

    def test_missing_keyring_fails_before_apt(self):
        self.keyring.unlink()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.commands, [])


class ResolverRecovery(unittest.TestCase):
    def setUp(self):
        self.commands = []
        self.servers = ['168.63.129.16']
        self.healthy = False
        self.fallback_works = True

    def command(self, command, **kwargs):
        self.commands.append(command)
        self.assertTrue(kwargs['check'])
        self.assertEqual(kwargs['timeout'], 10)
        output = ''
        if command[0] == 'getent':
            if not self.healthy and not (self.servers == ['1.1.1.1', '1.0.0.1']
                                        and self.fallback_works):
                raise subprocess.CalledProcessError(2, command)
        elif command[0] == 'ip':
            output = '[{"dev":"eth0"}]'
        elif command[:2] == ['resolvectl', 'dns']:
            if len(command) == 3:
                output = 'Link 2 (eth0): ' + ' '.join(self.servers)
            else:
                self.servers = command[3:]
        return subprocess.CompletedProcess(command, 0, stdout=output)

    def test_healthy_dns_never_changes_configuration(self):
        self.healthy = True
        with runtime_dns(run=self.command, hosted=True):
            pass
        self.assertTrue(all(c[0] == 'getent' for c in self.commands))

    def test_fallback_restores_original_after_body_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'APT failed'):
            with runtime_dns(run=self.command, hosted=True):
                self.assertEqual(self.servers, ['1.1.1.1', '1.0.0.1'])
                raise RuntimeError('APT failed')
        self.assertEqual(self.servers, ['168.63.129.16'])
        self.assertEqual(self.commands[-1], ['resolvectl', 'dns', 'eth0'])

    def test_failed_fallback_restores_without_entering_install(self):
        self.fallback_works = False
        entered = False
        with self.assertRaisesRegex(RuntimeError, 'still fails'):
            with runtime_dns(run=self.command, hosted=True):
                entered = True
        self.assertFalse(entered)
        self.assertEqual(self.servers, ['168.63.129.16'])

    def test_successful_fallback_restores_original(self):
        with runtime_dns(run=self.command, hosted=True):
            self.assertEqual(self.servers, ['1.1.1.1', '1.0.0.1'])
        self.assertEqual(self.servers, ['168.63.129.16'])

    def test_restoration_failure_is_not_success(self):
        def fail_restore(command, **kwargs):
            if command == ['resolvectl', 'dns', 'eth0', '168.63.129.16']:
                raise subprocess.CalledProcessError(1, command)
            return self.command(command, **kwargs)
        with self.assertRaises(subprocess.CalledProcessError):
            with runtime_dns(run=fail_restore, hosted=True):
                pass

    def test_fallback_denied_outside_hosted_ci(self):
        with self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
            with runtime_dns(run=self.command, hosted=False):
                self.fail('Must not enter installation')
        self.assertTrue(all(c[0] == 'getent' for c in self.commands))


if __name__ == '__main__':
    unittest.main()
