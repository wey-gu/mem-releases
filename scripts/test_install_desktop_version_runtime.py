"""Check source isolation and fail-closed runtime installation boundaries."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from install_desktop_version_runtime import PACKAGES, install_runtime


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
                        keyring=self.keyring, run=kwargs.pop('run', self.record_command), **kwargs)

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


if __name__ == '__main__':
    unittest.main()
