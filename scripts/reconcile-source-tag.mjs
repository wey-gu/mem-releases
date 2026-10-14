// Observe and create release source tags at the exact RC-recorded commits.
//
// A GA source tag is release provenance: it must name the same commit the RC
// tag named. An existing tag at that commit is accepted (resume); a tag at
// any other commit is a conflict and is never moved.
import { pathToFileURL } from 'node:url';

export function githubApi({ token, fetcher = fetch }) {
  return async (method, path, body) => {
    const response = await fetcher(`https://api.github.com/${path}`, {
      method,
      redirect: 'error',
      headers: {
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const text = await response.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      json = null;
    }
    return { status: response.status, json };
  };
}

export async function peelTag({ api, repo, tag }) {
  const ref = await api('GET', `repos/${repo}/git/ref/tags/${encodeURIComponent(tag)}`);
  if (ref.status === 404) return null;
  if (ref.status !== 200 || !ref.json?.object) {
    throw new Error(`${repo} ${tag}: tag lookup failed with HTTP ${ref.status}`);
  }
  let { object } = ref.json;
  for (let depth = 0; object.type === 'tag'; depth += 1) {
    if (depth >= 4) throw new Error(`${repo} ${tag}: tag chain is too deep`);
    const annotated = await api('GET', `repos/${repo}/git/tags/${object.sha}`);
    if (annotated.status !== 200 || !annotated.json?.object) {
      throw new Error(`${repo} ${tag}: annotated tag lookup failed with HTTP ${annotated.status}`);
    }
    object = annotated.json.object;
  }
  if (object.type !== 'commit' || !/^[0-9a-f]{40}$/.test(object.sha || '')) {
    throw new Error(`${repo} ${tag}: does not name a commit`);
  }
  return object.sha;
}

export async function readGitlink({ api, repo, path, ref }) {
  const response = await api('GET', `repos/${repo}/contents/${path}?ref=${ref}`);
  if (response.status !== 200 || response.json?.type !== 'submodule'
      || !/^[0-9a-f]{40}$/.test(response.json?.sha || '')) {
    throw new Error(`${repo}@${ref}: ${path} is not a readable gitlink (HTTP ${response.status})`);
  }
  return response.json.sha;
}

export async function ensureTag({ api, repo, tag, sha, annotated, message }) {
  if (!/^[0-9a-f]{40}$/.test(sha || '')) throw new Error(`${repo} ${tag}: expected commit is invalid`);
  const live = await peelTag({ api, repo, tag });
  if (live === sha) {
    console.log(`${repo} ${tag}: already at ${sha}; skipping`);
    return 'identical';
  }
  if (live) throw new Error(`${repo} ${tag}: exists at ${live}, not the RC-recorded ${sha}; refusing to move it`);
  let target = sha;
  if (annotated) {
    const object = await api('POST', `repos/${repo}/git/tags`,
      { tag, message, object: sha, type: 'commit' });
    if (object.status !== 201 || !object.json?.sha) {
      throw new Error(`${repo} ${tag}: annotated tag creation failed with HTTP ${object.status}`);
    }
    target = object.json.sha;
  }
  const created = await api('POST', `repos/${repo}/git/refs`, { ref: `refs/tags/${tag}`, sha: target });
  // 422 means a concurrent writer created the ref; the readback decides.
  if (created.status !== 201 && created.status !== 422) {
    throw new Error(`${repo} ${tag}: ref creation failed with HTTP ${created.status}`);
  }
  const readback = await peelTag({ api, repo, tag });
  if (readback !== sha) throw new Error(`${repo} ${tag}: readback ${readback} != ${sha}`);
  console.log(`${repo} ${tag}: created at ${sha}`);
  return 'created';
}

export async function observeTags({ memApi, communityApi, rc, ga }) {
  const mem = 'nowledge-co/mem';
  const community = 'nowledge-co/community';
  const memRc = await peelTag({ api: memApi, repo: mem, tag: `v${rc}` });
  const observed = {
    mem: {
      rc: memRc,
      ga: await peelTag({ api: memApi, repo: mem, tag: `v${ga}` }),
      cli: await peelTag({ api: memApi, repo: mem, tag: `cli-publish-v${ga}` }),
    },
    community: {
      rc: await peelTag({ api: communityApi, repo: community, tag: `v${rc}` }),
      ga: await peelTag({ api: communityApi, repo: community, tag: `v${ga}` }),
    },
    communityGitlink: memRc
      ? await readGitlink({ api: memApi, repo: mem, path: 'community', ref: memRc })
      : null,
  };
  return observed;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [command, ...args] = process.argv.slice(2);
  const run = async () => {
    if (command === 'observe') {
      const [rc, ga] = args;
      if (!process.env.MEM_READ_TOKEN) throw new Error('MEM_READ_TOKEN is required');
      const observed = await observeTags({
        memApi: githubApi({ token: process.env.MEM_READ_TOKEN }),
        communityApi: githubApi({ token: process.env.COMMUNITY_READ_TOKEN }),
        rc,
        ga,
      });
      console.log(JSON.stringify(observed, null, 2));
    } else if (command === 'ensure') {
      const [repo, tag, sha, kind, message] = args;
      if (!process.env.TAG_WRITE_TOKEN) throw new Error('TAG_WRITE_TOKEN is required');
      if (!['annotated', 'lightweight'].includes(kind)) throw new Error('kind must be annotated|lightweight');
      await ensureTag({ api: githubApi({ token: process.env.TAG_WRITE_TOKEN }), repo, tag, sha,
        annotated: kind === 'annotated', message: message || tag });
    } else {
      throw new Error('Usage: reconcile-source-tag.mjs observe RC GA | ensure REPO TAG SHA KIND [MESSAGE]');
    }
  };
  run().catch((error) => {
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  });
}
