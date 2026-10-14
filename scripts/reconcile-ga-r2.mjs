// Observe or verify the six versioned GA objects in R2 by SHA-256.
//
// observe: report each key as `absent` or the SHA-256 of its live bytes. The
//          promotion plan compares those digests with the RC Release assets.
// verify:  require each listed key to hold exactly the expected RC digest.
//
// A HEAD error other than 404 is never treated as absent: an unknown state
// must stop the promotion instead of authorizing an upload.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

async function hashBody(body) {
  const hash = createHash('sha256');
  for await (const chunk of body) hash.update(chunk);
  return `sha256:${hash.digest('hex')}`;
}

export async function observeR2({ version, targets, head, get }) {
  if (!/^\d+\.\d+\.\d+$/.test(version || '')) throw new Error('GA version must be clean semver');
  const state = {};
  for (const target of targets) {
    const key = `app/${version}/${target}`;
    try {
      await head(key);
    } catch (error) {
      if (error?.$metadata?.httpStatusCode === 404) {
        state[target] = 'absent';
        continue;
      }
      throw new Error(`${key}: R2 status unknown; refusing to plan a public mutation`, { cause: error });
    }
    state[target] = await hashBody(await get(key));
    console.error(`${key}: present (${state[target]})`);
  }
  return state;
}

export async function verifyR2({ version, expected, head, get }) {
  const observed = await observeR2({ version, targets: Object.keys(expected), head, get });
  for (const [target, digest] of Object.entries(expected)) {
    if (observed[target] !== digest) {
      throw new Error(`app/${version}/${target}: R2 holds ${observed[target]}, expected ${digest}`);
    }
  }
  console.error(`R2 holds the exact RC bytes for ${Object.keys(expected).length} GA objects`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [mode, version, selectedJson, ...only] = process.argv.slice(2);
  try {
    if (!['observe', 'verify'].includes(mode) || !selectedJson) {
      throw new Error('Usage: reconcile-ga-r2.mjs observe|verify VERSION SELECTED_JSON [TARGET...]');
    }
    const selected = JSON.parse(readFileSync(selectedJson, 'utf8'))
      .filter((item) => only.length === 0 || only.includes(item.target));
    const { S3Client, HeadObjectCommand, GetObjectCommand } = await import('@aws-sdk/client-s3');
    const bucket = process.env.R2_BUCKET_NAME;
    if (!bucket || !process.env.CLOUDFLARE_ACCOUNT_ID || !process.env.R2_ACCESS_KEY_ID
      || !process.env.R2_SECRET_ACCESS_KEY) throw new Error('R2 credentials and bucket are required');
    const client = new S3Client({
      region: 'auto',
      endpoint: `https://${process.env.CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com`,
      credentials: {
        accessKeyId: process.env.R2_ACCESS_KEY_ID,
        secretAccessKey: process.env.R2_SECRET_ACCESS_KEY,
      },
    });
    const io = {
      head: (key) => client.send(new HeadObjectCommand({ Bucket: bucket, Key: key })),
      get: async (key) => (await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }))).Body,
    };
    if (mode === 'observe') {
      const state = await observeR2({ version, targets: selected.map((item) => item.target), ...io });
      console.log(JSON.stringify(state, null, 2));
    } else {
      const expected = Object.fromEntries(selected.map((item) => [item.target, item.digest]));
      await verifyR2({ version, expected, ...io });
    }
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
}
