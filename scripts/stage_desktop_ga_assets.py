"""Stage original RC desktop packages under their GA filenames."""

from fnmatch import fnmatchcase
import os
from pathlib import Path
import sys


VULKAN_SUFFIXES = ('_x64-vulkan-setup.exe', '_amd64-vulkan.deb', '_amd64-vulkan.AppImage')


def stage(directory, rc, ga):
    files = [path for path in directory.iterdir() if path.is_file()]
    vulkan = [path for path in files if '-vulkan' in path.name.lower()]
    standard = [path for path in files if path not in vulkan]
    if vulkan and (
        len(vulkan) != len(VULKAN_SUFFIXES)
        or any(sum(path.name.endswith(rc + suffix) for path in vulkan) != 1
               for suffix in VULKAN_SUFFIXES)
    ):
        raise ValueError('Expected the complete RC Vulkan set: NSIS, DEB and AppImage')

    patterns = {
        'dmg_arm': ('*aarch64*.dmg',),
        'dmg_x64': ('*x86_64*.dmg', '*x64*.dmg'),
        'exe_win': ('*.exe',),
        'deb': ('*.deb',),
        'appimg': ('*.appimage',),
    }
    outputs = {}
    for output, globs in patterns.items():
        matches = [path for path in standard
                   if any(fnmatchcase(path.name.lower(), pattern) for pattern in globs)]
        if len(matches) != 1:
            raise ValueError(f'Expected one standard {output} package, got {matches}')
        outputs[output] = matches[0]

    variant_dir = directory / 'vulkan'
    targets = {
        path: (variant_dir if path in vulkan else directory) / path.name.replace(rc, ga)
        for path in [*outputs.values(), *vulkan]
    }
    if len(set(targets.values())) != len(targets) or any(
        target != source and target.exists() for source, target in targets.items()
    ):
        raise ValueError('GA package filenames collide with existing assets')
    variant_dir.mkdir()
    for source, target in targets.items():
        if source != target:
            source.rename(target)
    return {name: targets[source].resolve() for name, source in outputs.items()}


if __name__ == '__main__':
    directory, rc, ga = sys.argv[1:]
    staged = stage(Path(directory), rc, ga)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for name, path in staged.items():
            print(f'{name}={path}', file=output)
