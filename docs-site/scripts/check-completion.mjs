import { readFile, access } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const root = process.argv[2] ?? fileURLToPath(new URL('../../openwiki/', import.meta.url));
const metadata = JSON.parse(await readFile(path.join(root, '.last-update.json'), 'utf8'));
assert.equal(metadata.status, 'complete', 'OpenWiki did not complete every planned page');
try {
  await access(path.join(root, '.run.json'));
  throw new Error('OpenWiki still has an active run');
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}
console.log('OpenWiki completion metadata verified.');
