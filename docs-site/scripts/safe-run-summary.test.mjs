import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { safeRunSummary } from './safe-run-summary.mjs';

test('safe summary exports only fixed fields and queue counters from synthetic state', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'openwiki-summary-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const hidden = 'private-summary-sentinel';
  await writeFile(path.join(root, '.run.json'), JSON.stringify({
    schemaVersion: 1, phase: 'generating', planningContext: hidden, wikiGoal: hidden,
    beforeContentSnapshot: hidden, runId: hidden, model: hidden,
    plan: { pages: ['pending', 'complete', 'skipped'].map(status => ({
      status, purpose: hidden, path: hidden, instructions: [hidden],
    })), extra: hidden },
  }));
  await writeFile(path.join(root, '.last-update.json'), JSON.stringify({ status: 'interrupted', model: hidden }));
  assert.deepEqual(await safeRunSummary(root, 'failure', 7), {
    generationOutcome: 'failure', generationRC: 7,
    checkpointPresent: true, checkpointState: 'ok', planPresent: true,
    phase: 'generating', status: 'interrupted', queueState: 'ok',
    pageCount: 3, pendingCount: 1, completeCount: 1, skippedCount: 1,
  });
  await writeFile(path.join(root, '.run.json'), JSON.stringify({ schemaVersion: 1, phase: 'planning' }));
  const planning = await safeRunSummary(root, hidden, 999);
  assert.equal(planning.phase, 'planning');
  assert.equal(planning.planPresent, false);
  assert.equal(planning.queueState, 'unavailable');
  assert.equal(planning.generationOutcome, 'unavailable');
  assert.equal(planning.generationRC, null);
  assert.ok(!JSON.stringify(planning).includes(hidden));
});

test('missing, corrupt and unreadable checkpoints stay unavailable; stale complete metadata cannot mask failure', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'openwiki-summary-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const corruption of ['missing', 'json', 'shape', 'unreadable']) {
    if (corruption === 'json') await writeFile(path.join(root, '.run.json'), '{');
    if (corruption === 'shape') await writeFile(path.join(root, '.run.json'), '{"phase":"complete"}');
    if (corruption === 'unreadable') {
      await rm(path.join(root, '.run.json'));
      await mkdir(path.join(root, '.run.json'));
    }
    const summary = await safeRunSummary(root);
    assert.equal(summary.checkpointState, 'unavailable', corruption);
    assert.equal(summary.phase, 'unavailable', corruption);
    assert.equal(summary.status, 'unavailable', corruption);
    assert.equal(summary.queueState, 'unavailable', corruption);
    assert.equal(summary.pageCount, null, corruption);
    assert.equal(summary.checkpointPresent, corruption !== 'missing');
  }
  await rm(path.join(root, '.run.json'), { recursive: true });
  await writeFile(path.join(root, '.last-update.json'), '{"status":"complete"}');
  assert.equal((await safeRunSummary(root, 'failure', 1)).status, 'unavailable');
  assert.equal((await safeRunSummary(root, 'success', 0)).status, 'complete');
});
