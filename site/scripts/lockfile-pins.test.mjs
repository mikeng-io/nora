// Aikido round 2 (npm lockfile pins, 8764_80504): the installed es-module-lexer must be 3.0.0 or
// newer. Astro's own range is ^2, so this pin comes from the overrides entry in package.json.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const pkg = fileURLToPath(new URL('../node_modules/es-module-lexer/package.json', import.meta.url));

test('the installed es-module-lexer is major 3 or newer', () => {
  const { version } = JSON.parse(readFileSync(pkg, 'utf8'));
  assert.ok(Number(version.split('.')[0]) >= 3, `es-module-lexer ${version} is older than 3.0.0`);
});
