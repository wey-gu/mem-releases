"""Install native acceptance dependencies from isolated, signed Ubuntu sources."""

import argparse
import os
from pathlib import Path
import platform
import subprocess
import tempfile

KEYRING = Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg')
PACKAGES = ('xvfb', 'dbus-x11', 'libwebkit2gtk-4.1-0',
            'libayatana-appindicator3-1', 'libfuse2')


def install_runtime(surface, package=None, *, release=None, keyring=KEYRING,
                    run=subprocess.run):
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

    with tempfile.TemporaryDirectory(prefix='mem-native-apt-') as temporary:
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
