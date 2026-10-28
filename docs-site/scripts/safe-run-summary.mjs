import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// No native text fields leave the runner. This is a projection, not resumable
// state: the native .run.json remains the only checkpoint.
export async function safeRunSummary(root, outcome = 'unavailable', rc) {
  const summary = {
    generationOutcome: ['success', 'failure', 'cancelled', 'skipped'].includes(outcome) ? outcome : 'unavailable',
    generationRC: Number.isInteger(rc) && rc >= 0 && rc <= 255 ? rc : null,
    checkpointPresent: false, checkpointState: 'unavailable', planPresent: false,
    phase: 'unavailable', status: 'unavailable', queueState: 'unavailable',
    pageCount: null, pendingCount: null, completeCount: null, skippedCount: null,
  };
  try {
    const text = await readFile(path.join(root, '.run.json'), 'utf8');
    summary.checkpointPresent = true;
    const state = JSON.parse(text);
    if (state?.schemaVersion === 1 && ['planning', 'generating'].includes(state.phase)) {
      summary.checkpointState = 'ok';
      summary.phase = state.phase;
      summary.planPresent = state.plan !== undefined && state.plan !== null;
      const pages = state.plan?.pages;
      if (Array.isArray(pages) && pages.every(page => ['pending', 'complete', 'skipped'].includes(page?.status))) {
        summary.queueState = 'ok';
        summary.pageCount = pages.length;
        for (const status of ['pending', 'complete', 'skipped']) {
          summary[`${status}Count`] = pages.filter(page => page.status === status).length;
        }
      }
    }
  } catch (error) {
    // ENOENT is known absence; permission/parse/I/O failures stay unavailable.
    if (error.code !== 'ENOENT') summary.checkpointPresent = true;
  }
  try {
    const metadata = JSON.parse(await readFile(path.join(root, '.last-update.json'), 'utf8'));
    if (metadata?.status === 'interrupted') summary.status = 'interrupted';
    if (metadata?.status === 'complete' && !summary.checkpointPresent
        && summary.generationOutcome === 'success' && summary.generationRC === 0) summary.status = 'complete';
  } catch { /* Absent or unreadable metadata is never complete. */ }
  return summary;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const directory = path.join(process.env.RUNNER_TEMP, 'openwiki-safe-summary');
  const rcText = await readFile(path.join(directory, 'generation.rc'), 'utf8').catch(() => '');
  const rc = /^\d+\s*$/.test(rcText) ? Number(rcText) : undefined;
  const summary = await safeRunSummary('openwiki', process.env.OPENWIKI_GENERATION_OUTCOME, rc);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await writeFile(path.join(directory, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, { mode: 0o600 });
}
