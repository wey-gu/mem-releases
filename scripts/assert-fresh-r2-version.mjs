// Normal GA promotion must not overwrite a version already partly public in R2.
// A partial publication needs the explicit recovery path and operator readback.
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const CORE_FILES = [
  'aarch64-apple-darwin.dmg',
  'x86_64-apple-darwin.dmg',
  'x86_64-pc-windows-msvc.exe',
  'x86_64-unknown-linux-gnu.deb',
  'x86_64-unknown-linux-gnu.AppImage',
];
const RPM_FILE = 'x86_64-unknown-linux-gnu.rpm';

export async function assertFreshR2Version({ version, includeRpm = false, head }) {
  if (!/^\d+\.\d+\.\d+$/.test(version || '')) {
    throw new Error('GA version must be clean semver');
  }
  const files = includeRpm ? [...CORE_FILES, RPM_FILE] : CORE_FILES;
  for (const file of files) {
    const key = `app/${version}/${file}`;
    try {
      await head(key);
    } catch (error) {
      if (error?.$metadata?.httpStatusCode === 404) continue;
      throw new Error(`${key}: R2 status unknown; refusing public mutation`, { cause: error });
    }
    throw new Error(`${key}: R2 object already exists; use the partial-release recovery path`);
  }
  console.log(`R2 version ${version} has no existing ${files.length} target objects`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [version, mode] = process.argv.slice(2);
  try {
    if (!['core', 'all'].includes(mode)) throw new Error('Usage: assert-fresh-r2-version.mjs VERSION core|all');
    const require = createRequire(resolve('mem-backbone/package.json'));
    const { S3Client, HeadObjectCommand } = require('@aws-sdk/client-s3');
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
    await assertFreshR2Version({ version, includeRpm: mode === 'all',
      head: (key) => client.send(new HeadObjectCommand({ Bucket: bucket, Key: key })) });
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
}
