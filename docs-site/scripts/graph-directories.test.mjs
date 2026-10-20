import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

test('exported graph keeps wiki navigation and readable private source references', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'wiki-public-graph-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const file = path.join(root, 'graph.json');
  const node = (id, body = '') => ({ id, body, links: [], backlinks: [] });
  await writeFile(file, JSON.stringify({
    nodes: [node('quickstart', '[concepts](concepts/)'), node('concepts/index'),
      node('concepts/memory', '[`pipeline.py`](../../nora/ingest/pipeline.py#L1) [skills](../../.agents/skills/) [quickstart](../quickstart.md)')],
    edges: [],
  }));
  execFileSync(process.execPath, [fileURLToPath(new URL('./graph-directories.mjs', import.meta.url)), file]);
  const graph = JSON.parse(await readFile(file, 'utf8'));
  assert.equal(graph.nodes[0].body, '[concepts](concepts/index.md)');
  assert.equal(graph.nodes[2].body, '`pipeline.py` skills [quickstart](../quickstart.md)');
  assert.deepEqual(graph.nodes[0].links, ['concepts/index']);
  assert.deepEqual(graph.nodes[1].backlinks, ['quickstart']);
  assert.deepEqual(graph.edges, [{ source: 'quickstart', target: 'concepts/index' }]);
});
