import { pathToFileURL } from 'node:url';

export const APT_LANDING_MARKER = 'data-nmem-apt-repository="1"';
export const APT_LANDING_PATHS = Object.freeze(['/apt', '/apt/']);
export const APT_LANDING_KEYS = Object.freeze(['apt', 'apt/']);

const CONTENT_TYPE = 'text/html; charset=utf-8';
const CACHE_CONTROL = 'public, max-age=300, no-transform';

function requireCondition(ok, message) {
  if (!ok) throw new Error(message);
}

function downloadOrigin(value = 'download-mem.nowledge.co') {
  const candidate = value.includes('://') ? value : `https://${value}`;
  const url = new URL(candidate);
  requireCondition(url.protocol === 'https:', 'APT download origin must use HTTPS');
  requireCondition(url.pathname === '/' && !url.search && !url.hash,
    'APT download origin must not include a path, query, or fragment');
  return url.origin;
}

export function aptLandingDocument() {
  return `<!doctype html>
<html lang="en" ${APT_LANDING_MARKER}>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Nowledge Mem APT Repository</title>
    <style>
      body { max-width: 48rem; margin: 4rem auto; padding: 0 1.5rem; font: 16px/1.6 system-ui, sans-serif; color: #1f2937; }
      code, pre { background: #f3f4f6; border-radius: 0.375rem; }
      pre { overflow-x: auto; padding: 1rem; }
      a { color: #2563eb; }
    </style>
  </head>
  <body>
    <main>
      <h1>Nowledge Mem APT Repository</h1>
      <p>This is the signed package repository for Nowledge Mem on Debian and Ubuntu (amd64).</p>
      <h2>Install</h2>
      <pre><code>curl -fsSL https://nowledge-co.github.io/community/apt/install.sh | sudo bash
sudo apt-get install nowledge-mem</code></pre>
      <h2>Upgrade</h2>
      <pre><code>sudo apt-get update
sudo apt-get install --only-upgrade nowledge-mem</code></pre>
      <p><a href="https://mem.nowledge.co/docs/installation">Installation documentation</a></p>
    </main>
  </body>
</html>
`;
}

export async function publishAptLanding({ put, purge, host }) {
  requireCondition(typeof put === 'function', 'APT landing put function is required');
  requireCondition(typeof purge === 'function', 'APT landing cache purge function is required');
  const origin = downloadOrigin(host);
  const body = Buffer.from(aptLandingDocument());

  for (const key of APT_LANDING_KEYS) {
    await put({
      key,
      body,
      contentType: CONTENT_TYPE,
      cacheControl: CACHE_CONTROL,
    });
  }

  const urls = APT_LANDING_PATHS.map((path) => `${origin}${path}`);
  await purge(urls);
  return { keys: [...APT_LANDING_KEYS], urls };
}

export async function verifyAptLanding({ fetcher = fetch, host }) {
  const origin = downloadOrigin(host);
  for (const path of APT_LANDING_PATHS) {
    const response = await fetcher(`${origin}${path}`, {
      headers: { 'User-Agent': 'Nowledge-Mem-Release-Verify/1' },
      redirect: 'manual',
    });
    requireCondition(response.status === 200,
      `APT landing ${path}: expected 200, got ${response.status}`);
    const contentType = response.headers.get('content-type') || '';
    requireCondition(contentType.toLowerCase().startsWith('text/html'),
      `APT landing ${path}: expected HTML, got ${contentType || '<missing>'}`);
    const body = await response.text();
    requireCondition(body.includes(APT_LANDING_MARKER),
      `APT landing ${path}: repository marker missing`);
  }
  console.log(`APT landing verified: ${origin}/apt and ${origin}/apt/`);
}

async function main() {
  const { S3Client, PutObjectCommand } = await import('@aws-sdk/client-s3');
  const required = [
    'CLOUDFLARE_ACCOUNT_ID',
    'R2_ACCESS_KEY_ID',
    'R2_SECRET_ACCESS_KEY',
    'R2_BUCKET_NAME',
    'CLOUDFLARE_ZONE_ID',
    'CLOUDFLARE_API_TOKEN',
  ];
  for (const name of required) {
    requireCondition(process.env[name], `${name} is required`);
  }

  const client = new S3Client({
    region: 'auto',
    endpoint: `https://${process.env.CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com`,
    credentials: {
      accessKeyId: process.env.R2_ACCESS_KEY_ID,
      secretAccessKey: process.env.R2_SECRET_ACCESS_KEY,
    },
  });
  const bucket = process.env.R2_BUCKET_NAME;
  const host = process.env.CUSTOM_DOMAIN || 'download-mem.nowledge.co';

  const result = await publishAptLanding({
    host,
    put: ({ key, body, contentType, cacheControl }) => client.send(new PutObjectCommand({
      Bucket: bucket,
      Key: key,
      Body: body,
      ContentLength: body.length,
      ContentType: contentType,
      CacheControl: cacheControl,
    })),
    purge: async (urls) => {
      const response = await fetch(
        `https://api.cloudflare.com/client/v4/zones/${process.env.CLOUDFLARE_ZONE_ID}/purge_cache`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${process.env.CLOUDFLARE_API_TOKEN}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ files: urls }),
        },
      );
      const payload = await response.json();
      requireCondition(response.ok && payload.success === true,
        `APT landing cache purge failed with HTTP ${response.status}`);
    },
  });

  let lastError;
  for (let attempt = 1; attempt <= 12; attempt += 1) {
    try {
      await verifyAptLanding({ host });
      console.log(`APT landing published: ${result.keys.join(', ')}`);
      return;
    } catch (error) {
      lastError = error;
      if (attempt === 12) break;
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
  }
  throw lastError;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
