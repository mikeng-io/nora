import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('./check-completion.mjs', import.meta.url));
test('only completed, finalized runs can reach the publisher', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'wiki-completion-'));
  const check = () => spawnSync(process.execPath, [script, root], { encoding: 'utf8' });
  try {
    assert.notEqual(check().status, 0, 'missing metadata');
    await writeFile(path.join(root, '.last-update.json'), '{');
    assert.notEqual(check().status, 0, 'invalid metadata');
    await writeFile(path.join(root, '.last-update.json'), JSON.stringify({ status: 'interrupted' }));
    assert.notEqual(check().status, 0, 'interrupted run, even without .run.json');
    await writeFile(path.join(root, '.last-update.json'), JSON.stringify({ status: 'complete' }));
    assert.equal(check().status, 0);
    await writeFile(path.join(root, '.run.json'), '{}');
    assert.notEqual(check().status, 0, 'unfinished active run');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
