import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { patchOpenWiki } from './patch-openwiki.mjs';

test('pinned native patch applies exactly, is idempotent and refuses drift before writing', async t => {
  const installed = new URL('../node_modules/openwiki/', import.meta.url);
  const patch = fileURLToPath(new URL('../patches/openwiki-0.7.2.patch', import.meta.url));
  const files = [...(await readFile(patch, 'utf8')).matchAll(/^--- a\/(.+)$/gm)].map(match => match[1]);
  assert.equal(files.length, 6);
  const root = await mkdtemp(path.join(tmpdir(), 'openwiki-patch-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(path.join(root, 'package.json'), JSON.stringify({ name: 'openwiki', version: '0.7.2' }));
  for (const file of files) {
    await mkdir(path.dirname(path.join(root, file)), { recursive: true });
    await copyFile(new URL(file, installed), path.join(root, file));
  }
  const snapshot = () => Promise.all(files.map(file => readFile(path.join(root, file), 'utf8')));
  const patched = await snapshot();
  assert.equal(await patchOpenWiki(root), 0);
  // Git independently interprets the committed unified diff to restore npm's
  // preimages; the implementation under test is Node-only.
  execFileSync('git', ['apply', '--reverse', patch], { cwd: root });
  const original = await snapshot();
  assert.equal(await patchOpenWiki(root), 6);
  assert.deepEqual(await snapshot(), patched);
  assert.equal(await patchOpenWiki(root), 0);
  await writeFile(path.join(root, files[0]), original[0]);
  assert.equal(await patchOpenWiki(root), 1, 'a mixed preimage/postimage install can be retried');
  assert.deepEqual(await snapshot(), patched);
  assert.equal(await patchOpenWiki(root), 0);

  await writeFile(path.join(root, 'package.json'), JSON.stringify({ name: 'openwiki', version: '0.7.3' }));
  await assert.rejects(patchOpenWiki(root), /before upgrading/);
  assert.deepEqual(await snapshot(), patched);
  await writeFile(path.join(root, 'package.json'), JSON.stringify({ name: 'openwiki', version: '0.7.2' }));
  for (let i = 0; i < files.length; i++) await writeFile(path.join(root, files[i]), original[i]);
  await writeFile(path.join(root, files.at(-1)), original.at(-1) + '// unexpected npm bytes\n');
  const corrupt = await snapshot();
  await assert.rejects(patchOpenWiki(root), /Unexpected OpenWiki input/);
  assert.deepEqual(await snapshot(), corrupt, 'no earlier file was partially patched');
});
