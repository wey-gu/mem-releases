"""Install native acceptance dependencies from isolated, signed Ubuntu sources."""

import argparse
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

KEYRING = Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg')
PACKAGES = ('xvfb', 'dbus-x11', 'libwebkit2gtk-4.1-0',
            'libayatana-appindicator3-1', 'libfuse2')


@contextmanager
def runtime_dns(*, run=subprocess.run, hosted=None):
    """Restore the hosted runner's link DNS even when APT or recovery fails."""
    def command(*args):
        return run(list(args), check=True, timeout=10,
                   capture_output=True, text=True).stdout

    def resolves():
        for hostname in ('archive.ubuntu.com', 'security.ubuntu.com'):
            try:
                command('getent', 'ahostsv4', hostname)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return False
        return True

    if resolves():
        yield
        return
    if hosted is None:
        hosted = (os.environ.get('GITHUB_ACTIONS') == 'true'
                  and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')
    if not hosted:
        raise RuntimeError('DNS recovery is limited to disposable GitHub-hosted runners')
    routes = json.loads(command('ip', '-json', 'route', 'show', 'default'))
    devices = {route['dev'] for route in routes if 'dev' in route}
    if len(devices) != 1:
        raise RuntimeError('Expected one default-route interface before DNS recovery')
    device = devices.pop()

    def nameservers():
        output = command('resolvectl', 'dns', device)
        prefix, separator, values = output.partition(':')
        if not separator or f'({device})' not in prefix:
            raise RuntimeError('Unrecognized per-link resolver response')
        servers = values.split()
        if not servers:
            raise RuntimeError('Cannot recover DNS without a retained original resolver')
        for server in servers:
            ipaddress.ip_address(server)
        return servers

    original = nameservers()
    print('Runner DNS failed; temporarily using public DNS for signed APT setup', flush=True)
    try:
        command('resolvectl', 'dns', device, '1.1.1.1', '1.0.0.1')
        command('resolvectl', 'flush-caches')
        if not resolves():
            raise RuntimeError('Ubuntu repository DNS still fails after bounded recovery')
        yield
    finally:
        command('resolvectl', 'dns', device, *original)
        command('resolvectl', 'flush-caches')
        if nameservers() != original:
            raise RuntimeError('Original runner resolver was not restored')
        print('Original runner DNS restored and read back', flush=True)


def install_runtime(surface, package=None, *, release=None, keyring=KEYRING,
                    run=subprocess.run, dns_context=runtime_dns):
    if surface not in {'linux-deb', 'linux-appimage'}:
        raise ValueError('Only the Ubuntu DEB and AppImage acceptance lanes are supported')
    release = platform.freedesktop_os_release() if release is None else release
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '22.04':
        raise ValueError('Native acceptance dependencies require Ubuntu 22.04')
    if not keyring.is_file():
        raise ValueError('The installed Ubuntu archive signing keyring is required')
    if surface == 'linux-deb':
        if package is None:
            raise ValueError('The original DEB package is required')
        package = Path(package).resolve(strict=True)
        if not package.is_file() or package.suffix != '.deb':
            raise ValueError('The original package must be a DEB file')

    with dns_context(), tempfile.TemporaryDirectory(prefix='mem-native-apt-') as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        lists = root / 'lists'
        (lists / 'partial').mkdir(parents=True)
        source = root / 'sources.list'
        components = 'main restricted universe multiverse'
        # Do not inherit the runner's Azure mirror or stale package lists.
        source.write_text(''.join(
            f'deb [signed-by={keyring}] {origin} {suite} {components}\n'
            for origin, suite in [
                ('https://archive.ubuntu.com/ubuntu', 'jammy'),
                ('https://archive.ubuntu.com/ubuntu', 'jammy-updates'),
                ('https://archive.ubuntu.com/ubuntu', 'jammy-backports'),
                ('https://security.ubuntu.com/ubuntu', 'jammy-security'),
            ]
        ))
        options = [
            '-o', f'Dir::Etc::sourcelist={source}',
            '-o', 'Dir::Etc::sourceparts=-',
            '-o', f'Dir::State::lists={lists}',
            '-o', 'Acquire::Retries=2',
            '-o', 'Acquire::http::Timeout=15',
            '-o', 'Acquire::https::Timeout=15',
            '-o', 'APT::Update::Error-Mode=any',
        ]

        def apt(*arguments):
            run(['apt-get', *options, *arguments], check=True, timeout=300,
                env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})

        apt('update', '-qq')
        apt('install', '-y', *PACKAGES)
        if surface == 'linux-deb':
            apt('install', '-y', str(package))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', required=True,
                        choices=['linux-deb', 'linux-appimage'])
    parser.add_argument('--package', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run this dependency installer as root on the disposable CI runner')
    install_runtime(args.platform, args.package)


if __name__ == '__main__':
    main()
