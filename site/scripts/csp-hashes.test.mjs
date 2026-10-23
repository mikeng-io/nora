import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { TOKEN, collect, inspect, render } from './csp-hashes.mjs';

const hash = (body) => `'sha256-${createHash('sha256').update(body, 'utf8').digest('base64')}'`;

test('every inline executable script is hashed, byte for byte', () => {
  const body = 'window.__x = 1;';
  const { hashes } = inspect(`<html><script>${body}</script></html>`);
  assert.deepEqual([...hashes], [hash(body)]);
});

test('external scripts and data blocks are not hashed', () => {
  const html = [
    '<script src="/a.js"></script>',
    '<script type="application/ld+json">{"a":1}</script>',
    '<script type="importmap">{"imports":{}}</script>',
    '<script type="module">import "/m.js";</script>',
  ].join('');
  const { hashes } = inspect(html);
  assert.deepEqual([...hashes], [hash('import "/m.js";')]);
});

test('an inline event handler or a javascript: URL cannot be covered and is reported', () => {
  assert.equal(inspect('<button onclick="go()">x</button>').hazards.length, 1);
  assert.equal(inspect('<a href="javascript:void(0)">x</a>').hazards.length, 1);
  assert.equal(inspect('<script>const onclick = "x"; el.onclick = () => 1;</script>').hazards.length, 0);
});

test('the graph export keeps its own policy and is skipped', () => {
  const dir = mkdtempSync(join(tmpdir(), 'csp-'));
  mkdirSync(join(dir, 'graph'));
  writeFileSync(join(dir, 'index.html'), '<script>a()</script>');
  writeFileSync(join(dir, 'graph', 'index.html'), '<script>graphOnly()</script>');
  const { hashes } = collect([dir]);
  assert.deepEqual(hashes, [hash('a()')]);
});

test('the token is replaced everywhere and a missing token is an error', () => {
  const out = render(`script-src 'self' ${TOKEN}; x ${TOKEN}`, ["'sha256-a'", "'sha256-b'"]);
  assert.equal(out, "script-src 'self' 'sha256-a' 'sha256-b'; x 'sha256-a' 'sha256-b'");
  assert.throws(() => render("script-src 'self'", []), /no @@CSP_SCRIPT_HASHES@@/);
  assert.throws(() => render(`${TOKEN} @@OTHER@@`, []), /unreplaced/);
});
