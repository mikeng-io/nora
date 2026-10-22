// Aikido round 2 (npm lockfile pins, 8764_80470): every installed es-module-lexer copy must be
// 3.0.0 or newer. Astro used to carry its own 2.3.2 copy under node_modules/astro/node_modules.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const copies = [
  'node_modules/es-module-lexer/package.json',
  'node_modules/astro/node_modules/es-module-lexer/package.json',
].map((rel) => fileURLToPath(new URL(`../${rel}`, import.meta.url)));

const major = (version) => Number(version.split('.')[0]);

test('every installed es-module-lexer copy is major 3 or newer', () => {
  const found = copies.filter(existsSync).map((file) => JSON.parse(readFileSync(file, 'utf8')).version);
  assert.ok(found.length > 0, 'es-module-lexer is not installed');
  for (const version of found) assert.ok(major(version) >= 3, `es-module-lexer ${version} is older than 3.0.0`);
});
