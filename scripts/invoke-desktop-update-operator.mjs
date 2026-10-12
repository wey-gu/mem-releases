#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';

const { values } = parseArgs({
  options: {
    input: { type: 'string' },
    output: { type: 'string' },
  },
});

try {
  if (!values.output) throw new Error('--output is required');
  const url = process.env.DESKTOP_UPDATE_OPERATOR_URL;
  const nonce = process.env.DESKTOP_UPDATE_INVOCATION_NONCE;
  if (!url || !nonce) throw new Error('Operator URL and invocation nonce are required');
  const request = values.input
    ? JSON.parse(await readFile(values.input, 'utf8'))
    : { action: 'status' };
  const response = await fetch(url, {
    method: 'POST',
    redirect: 'error',
    signal: AbortSignal.timeout(30_000),
    headers: {
      'Content-Type': 'application/json',
      'X-Invocation-Nonce': nonce,
    },
    body: JSON.stringify(request),
  });
  const result = await response.json();
  await writeFile(values.output, `${JSON.stringify(result, null, 2)}\n`);
  console.log(JSON.stringify(result, null, 2));
  if (!response.ok) process.exitCode = 1;
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Operator invocation failed');
  process.exitCode = 1;
}
