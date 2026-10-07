import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';

export function gaDate(release) {
  const version = /^v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$/.exec(release.tag_name)?.[1];
  if (!version || release.draft !== false || release.prerelease !== false) {
    throw new Error('A published GA Release is required.');
  }
  if (typeof release.published_at !== 'string') throw new Error('Missing GA publication time.');
  const published = new Date(release.published_at);
  if (Number.isNaN(published.valueOf())) throw new Error('Missing GA publication time.');
  return { version, date: published.toISOString().slice(0, 10) };
}

export function dateChangelog(source, release, kind = 'website') {
  const { version, date } = gaDate(release);
  const escaped = version.replaceAll('.', '\\.');
  let matches;
  if (kind === 'website') {
    const versions = [...source.matchAll(new RegExp(`\\bversion:\\s*["']${escaped}["']`, 'g'))];
    if (versions.length !== 1) throw new Error('Expected exactly one matching version.');
    const start = versions[0].index + versions[0][0].length;
    const rest = source.slice(start);
    const next = /\bversion\s*:/.exec(rest)?.index ?? rest.length;
    const field = /\bdate:\s*(["'])([^"']*)\1/.exec(rest.slice(0, next));
    if (!field) throw new Error('Missing date field.');
    matches = [{ value: field[2], index: start + field.index + field[0].lastIndexOf(field[2]) }];
  } else if (kind === 'engineering') {
    matches = [...source.matchAll(new RegExp(`^## \\[${escaped}\\] - ([^\\r\\n]+)`, 'gm'))]
      .map((match) => ({ value: match[1], index: match.index + match[0].length - match[1].length }));
  } else {
    throw new Error('Unknown changelog format.');
  }
  if (matches.length !== 1) throw new Error('Expected exactly one matching date.');
  const field = matches[0];
  if (field.value === date) return source;
  if (!/^\[?unreleased\]?$/i.test(field.value)) throw new Error('Existing date conflicts with GA publication.');
  return source.slice(0, field.index) + date + source.slice(field.index + field.value.length);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [version, memRoot] = process.argv.slice(2);
  if (!memRoot || !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)) {
    throw new Error('Usage: node scripts/date-ga-changelog.mjs <x.y.z> <mem-checkout>');
  }
  const release = JSON.parse(execFileSync('gh', ['api', '-X', 'GET', `repos/wey-gu/mem-releases/releases/tags/v${version}`], { encoding: 'utf8', timeout: 15000 }));
  const published = gaDate(release);
  if (published.version !== version) throw new Error('Release version mismatch.');
  // Validate both entries before writing, so a date conflict cannot leave one dated.
  const changes = [
    ['nowledge-labs-website/nowledge-mem/data/changelog.ts', 'website'],
    ['nowledge-graph/CHANGELOG.md', 'engineering'],
  ].map(([file, kind]) => {
    const path = join(memRoot, file);
    const source = readFileSync(path, 'utf8');
    return { path, source, dated: dateChangelog(source, release, kind) };
  });
  for (const { path, source, dated } of changes) {
    if (dated !== source) writeFileSync(path, dated);
    console.log(`${published.version} / ${published.date}: ${dated === source ? 'unchanged' : 'updated'} ${path}`);
  }
}
