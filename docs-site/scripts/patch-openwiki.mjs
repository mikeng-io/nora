import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, rename } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = fileURLToPath(new URL('../node_modules/openwiki/', import.meta.url));
const patchFile = new URL('../patches/openwiki-0.7.2.patch', import.meta.url);
const sha256 = value => createHash('sha256').update(value).digest('hex');

// Exact preimage/postimage hashes make this deliberately narrower than a general
// patch utility: unexpected npm bytes fail before any installed file is changed.
export async function patchOpenWiki(root = packageRoot) {
  const metadata = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
  assert.equal(metadata.name, 'openwiki', 'Unexpected package');
  assert.equal(metadata.version, '0.7.2', 'Recheck the native fix before upgrading OpenWiki');
  const patch = await readFile(patchFile, 'utf8');
  const sections = patch.split('diff --git ').slice(1);
  assert.ok(sections.length, 'Empty native patch');
  const changes = [];
  for (const section of sections) {
    const [, relative] = /^a\/(\S+) b\/\1\n/.exec(section) ?? [];
    assert.ok(relative?.startsWith('dist/') && !relative.includes('..'), 'Invalid patch path');
    const [, beforeHash, afterHash] = /\nindex ([a-f0-9]{64})\.\.([a-f0-9]{64})\n/.exec(section) ?? [];
    assert.ok(beforeHash && afterHash, 'Patch requires exact SHA-256 identities');
    const file = path.join(root, relative);
    const input = await readFile(file);
    if (sha256(input) === afterHash) continue;
    assert.equal(sha256(input), beforeHash, `Unexpected OpenWiki input: ${relative}`);
    let source = input.toString('utf8');
    const hunks = section.split(/^@@[^\n]*@@\n/m).slice(1);
    assert.ok(hunks.length, `Missing hunks: ${relative}`);
    for (const hunk of hunks) {
      const lines = hunk.split('\n').slice(0, -1);
      assert.ok(lines.every(line => /^[ +\-]/.test(line)), 'Unsupported patch line');
      const before = lines.filter(line => line[0] !== '+').map(line => line.slice(1) + '\n').join('');
      const after = lines.filter(line => line[0] !== '-').map(line => line.slice(1) + '\n').join('');
      assert.equal(source.split(before).length, 2, `Non-unique patch context: ${relative}`);
      source = source.replace(before, () => after);
    }
    assert.equal(sha256(source), afterHash, `Unexpected patched output: ${relative}`);
    changes.push({ file, source });
  }
  // Validate every target before applying even the first file.
  for (const { file, source } of changes) {
    const temporary = `${file}.${process.pid}.patched`;
    await writeFile(temporary, source, 'utf8');
    await rename(temporary, file);
  }
  return changes.length;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const changed = await patchOpenWiki();
  console.log(`OpenWiki 0.7.2 native resume/submission patch verified (${changed} files applied).`);
}
