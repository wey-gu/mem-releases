// Preserve an existing versioned RPM's bytes. A partial promotion can resume
// only when the R2 object matches the validated RC RPM exactly.
import { createHash } from 'node:crypto';
import { createReadStream, statSync } from 'node:fs';
import { basename } from 'node:path';
import { pathToFileURL } from 'node:url';
import { sha256File } from './upload-draft-release-assets.mjs';

const TARGET = 'x86_64-unknown-linux-gnu.rpm';

async function hashBody(body) {
  const hash = createHash('sha256');
  for await (const chunk of body) hash.update(chunk);
  return `sha256:${hash.digest('hex')}`;
}

export async function reconcileRpmR2({ version, file, head, get, put,
  digestFile = sha256File }) {
  if (!/^\d+\.\d+\.\d+$/.test(version || '') ||
      !file || !basename(file).toLowerCase().endsWith('.rpm')) {
    throw new Error('GA version and RPM file required');
  }
  const key = `app/${version}/${TARGET}`;
  const expected = await digestFile(file);
  let exists = false;
  try {
    await head(key);
    exists = true;
  } catch (error) {
    if (error?.$metadata?.httpStatusCode !== 404) {
      throw new Error(`${key}: R2 status unknown`, { cause: error });
    }
  }
  if (exists) {
    const actual = await hashBody(await get(key));
    if (actual !== expected) {
      throw new Error(`${key}: existing R2 SHA-256 differs (${actual} != ${expected})`);
    }
    console.log(`${key}: identical RPM already in R2; skipping`);
    return;
  }
  await put(key, file);
  const delivered = await hashBody(await get(key));
  if (delivered !== expected) {
    throw new Error(`${key}: uploaded R2 SHA-256 mismatch (${delivered} != ${expected})`);
  }
  console.log(`${key}: uploaded and verified in R2`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [version, file] = process.argv.slice(2);
  try {
    const { S3Client, HeadObjectCommand, GetObjectCommand, PutObjectCommand } =
      await import('@aws-sdk/client-s3');
    const bucket = process.env.R2_BUCKET_NAME;
    if (!bucket || !process.env.CLOUDFLARE_ACCOUNT_ID || !process.env.R2_ACCESS_KEY_ID
      || !process.env.R2_SECRET_ACCESS_KEY) throw new Error('R2 credentials and bucket required');
    const client = new S3Client({
      region: 'auto',
      endpoint: `https://${process.env.CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com`,
      credentials: {
        accessKeyId: process.env.R2_ACCESS_KEY_ID,
        secretAccessKey: process.env.R2_SECRET_ACCESS_KEY,
      },
    });
    const call = (command) => client.send(command);
    await reconcileRpmR2({ version, file,
      head: (key) => call(new HeadObjectCommand({ Bucket: bucket, Key: key })),
      get: async (key) => (await call(new GetObjectCommand({ Bucket: bucket, Key: key }))).Body,
      put: (key, path) => call(new PutObjectCommand({
        Bucket: bucket, Key: key,
        Body: createReadStream(path), ContentLength: statSync(path).size,
        ContentType: 'application/x-rpm',
        ContentDisposition: `attachment; filename="${basename(path).replace(/["\\]/g, '_')}"`,
        CacheControl: 'public, max-age=31536000, no-transform',
        Metadata: { version, platform: 'x86_64-unknown-linux-gnu',
          uploaded: new Date().toISOString(), size: String(statSync(path).size) },
      })),
    });
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
}
