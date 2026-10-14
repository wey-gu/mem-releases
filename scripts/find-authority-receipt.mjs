// Locate the authority publish receipt for an already-published candidate.
//
// A resumed promotion may find the authority already published for this GA
// while the Desktop pause routes remain. Route removal must be bound to the
// exact audited publish receipt, so this selects it from downloaded receipt
// artifacts laid out as <root>/<run-id>/<artifact-name>/operation-receipt.json.
// It never synthesizes a receipt: zero or several matches fail closed.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export function selectPublishReceipt({ root, ga, revision, stable }) {
  const matches = [];
  if (!existsSync(root)) throw new Error(`No downloaded receipts under ${root}`);
  for (const runId of readdirSync(root)) {
    if (!/^[1-9][0-9]*$/.test(runId)) continue;
    for (const name of readdirSync(join(root, runId))) {
      const operationId = name.replace(/^desktop-update-receipt-/, '');
      const path = join(root, runId, name, 'operation-receipt.json');
      if (!UUID.test(operationId) || !existsSync(path)) continue;
      const receipt = JSON.parse(readFileSync(path, 'utf8'));
      if (receipt.action === 'publish' && receipt.mode === 'published'
          && receipt.candidate_version === ga && receipt.revision === revision
          && receipt.stable_version === stable && receipt.operation_id === operationId
          && existsSync(join(root, runId, name, 'candidate-manifest.json'))) {
        matches.push({ runId, operationId, actor: receipt.actor, revision });
      }
    }
  }
  if (matches.length !== 1) {
    throw new Error(`Expected exactly one publish receipt for ${ga} at revision ${revision}, found ${matches.length}`);
  }
  return matches[0];
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, ga, revision, stable] = process.argv.slice(2);
  try {
    const found = selectPublishReceipt({ root, ga, revision: Number(revision), stable });
    console.log(`run_id=${found.runId}\noperation_id=${found.operationId}\nactor=${found.actor}`);
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
