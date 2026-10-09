import { basename } from 'node:path';
import { pathToFileURL } from 'node:url';

export function gaRpmFilename(rc, ga, sourceName) {
  if (!/^\d+\.\d+\.\d+-[a-z0-9.-]+$/i.test(rc || '') ||
      !/^\d+\.\d+\.\d+$/.test(ga || '') || rc.split('-')[0] !== ga) {
    throw new Error('RC and GA versions do not match');
  }
  const name = basename(sourceName || '');
  if (name !== sourceName || !name.toLowerCase().endsWith('.rpm')) {
    throw new Error('Expected a single RPM filename');
  }
  const containsVersion = (value) => {
    const at = name.indexOf(value);
    if (at < 0) return false;
    const before = name[at - 1] || '';
    const after = name[at + value.length] || '';
    return !/[\d.]/.test(before) && !/[\d.]/.test(after);
  };
  if (containsVersion(rc)) return name.replaceAll(rc, ga);
  // RPM package versions may already use the GA base plus a package release
  // (`Nowledge.Mem-0.10.94-1.x86_64.rpm`), even on an RC GitHub Release.
  if (containsVersion(ga)) return name;
  throw new Error(`RPM filename does not contain ${rc} or ${ga}: ${name}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    console.log(gaRpmFilename(...process.argv.slice(2)));
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
}
