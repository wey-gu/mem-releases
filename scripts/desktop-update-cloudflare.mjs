import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { readFile, writeFile, mkdtemp, mkdir, copyFile, rm, open } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createServer } from 'node:net';
import { executePlan, validatePlan } from './desktop-update-operation.mjs';

export const accountId = 'd18f4946cabbb715aac97d4661f88b6e';
const browserUA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/135.0.0.0 Safari/537.36';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export function previewFiles(plan, environment) {
  validatePlan(plan);
  if (!['production', 'staging'].includes(environment)) throw new Error('Explicit production or staging environment required');
  const operationJSON = plan.request ? JSON.stringify(plan.request) : null;
  const digest = createHash('sha256').update(JSON.stringify(plan)).digest('hex');
  const module = `export const environment = ${JSON.stringify(environment)};\nexport const operationJSON = ${JSON.stringify(operationJSON)};\nexport const planDigest = ${JSON.stringify(digest)};\n`;
  const config = {
    name: 'mem-desktop-update-ci-preview', account_id: accountId,
    main: 'worker.mjs', compatibility_date: '2026-10-12', workers_dev: false,
    durable_objects: { bindings: [{ name: 'CONTROL', class_name: 'DesktopUpdateControl',
      script_name: environment === 'production' ? 'nowledge-mem-backbone-prod' : 'nowledge-mem-backbone-staging' }] },
  };
  return { module, config, digest };
}

export function bindingTransport(baseUrl, fetcher = fetch) {
  const base = new URL(baseUrl);
  if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1' || !base.port || base.pathname !== '/' || base.search || base.hash) {
    throw new Error('Preview transport must use a loopback-only HTTP origin');
  }
  return (url, init) => {
    const source = new URL(url);
    if (source.origin !== 'https://backbone-mem.nowledge.co') throw new Error('Unexpected authority origin');
    let path;
    if (init.method === 'GET' && source.pathname === '/admin/desktop-updates' && !source.search) path = '/status';
    else if (init.method === 'GET' && source.pathname === '/admin/desktop-updates/history' && source.search === '?limit=100') path = '/history';
    else if (init.method === 'POST' && source.pathname === '/admin/desktop-updates' && !source.search) path = '/operation';
    else throw new Error('Unexpected authority route or method');
    return fetcher(new URL(path, base), { ...init,
      headers: { 'Content-Type': 'application/json', 'User-Agent': browserUA }, redirect: 'error' });
  };
}

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

export async function withPreview(plan, environment, run, { allowLocalOAuth = false } = {}) {
  if (!process.env.CLOUDFLARE_API_TOKEN && !allowLocalOAuth) throw new Error('Existing CLOUDFLARE_API_TOKEN is missing; no preview or authority request made');
  if (process.env.CLOUDFLARE_ACCOUNT_ID && process.env.CLOUDFLARE_ACCOUNT_ID !== accountId) throw new Error('Unexpected Cloudflare account; no preview created');
  const files = previewFiles(plan, environment);
  const directory = await mkdtemp(join(tmpdir(), 'mem-update-ci-'));
  let child, exited, logfile;
  try {
    await writeFile(join(directory, 'wrangler.json'), JSON.stringify(files.config, null, 2));
    await writeFile(join(directory, 'approved-plan.mjs'), files.module);
    await copyFile(fileURLToPath(new URL('./desktop-update-binding-worker.mjs', import.meta.url)), join(directory, 'worker.mjs'));
    const port = await availablePort(), base = `http://127.0.0.1:${port}/`;
    const wrangler = fileURLToPath(new URL('../node_modules/wrangler/bin/wrangler.js', import.meta.url));
    logfile = await open(join(directory, 'private-preview.log'), 'w', 0o600);
    child = spawn(process.execPath, [wrangler, 'dev', '--config', join(directory, 'wrangler.json'), '--remote',
      '--ip', '127.0.0.1', '--port', String(port), '--inspector-port', '0', '--show-interactive-dev-session=false', '--log-level', 'error'],
    { cwd: directory, env: { ...process.env, WRANGLER_SEND_METRICS: 'false', WRANGLER_LOG_PATH: join(directory, 'private-wrangler.log') },
      detached: true, stdio: ['ignore', logfile.fd, logfile.fd] });
    exited = new Promise(resolve => { child.once('error', () => resolve(true)); child.once('exit', () => resolve(true)); });
    let ready = false;
    const deadline = Date.now() + 75_000;
    while (Date.now() < deadline) {
      if (await Promise.race([exited, sleep(0).then(() => false)])) throw new Error('Cloudflare preview startup failed; no authority mutation sent');
      try {
        const response = await fetch(new URL('/health', base), { headers: { 'User-Agent': browserUA }, redirect: 'error', signal: AbortSignal.timeout(2000) });
        if (response.status === 200) {
          const body = await response.json();
          if (body.environment !== environment || body.planDigest !== files.digest) throw new Error('Preview identity mismatch');
          ready = true; break;
        }
      } catch { /* Keep waiting; no authority operation has been sent. */ }
      await sleep(500);
    }
    if (!ready) throw new Error('Cloudflare preview did not become ready; no authority mutation sent');
    return await run(bindingTransport(base));
  } finally {
    if (child?.pid) {
      try { process.kill(-child.pid, 'SIGTERM'); } catch { /* already stopped */ }
      if (!await Promise.race([exited, sleep(3000).then(() => false)])) {
        try { process.kill(-child.pid, 'SIGKILL'); } catch { /* already stopped */ }
      }
    }
    await logfile?.close();
    await rm(directory, { recursive: true, force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const plan = JSON.parse(await readFile(resolve('desktop-update-plan.json'), 'utf8'));
    const environment = process.env.UPDATE_ENVIRONMENT || 'production';
    await mkdir('desktop-update-receipts', { recursive: true });
    const result = await withPreview(plan, environment, authorityFetch => executePlan(plan, {
      authorityFetch, record: (name, value) => writeFile(`desktop-update-receipts/${name}`, JSON.stringify(value, null, 2) + '\n'),
    }));
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(`Cloudflare Desktop operation failed: ${error.message}`);
    process.exitCode = 1;
  }
}
