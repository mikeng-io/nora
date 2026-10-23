// Aikido round 2 (8764_82050 and low 8764_82017, 8764_82034): the installed undici must be 8.10.2 or
// newer. discord.js pins undici ^6, so this pin comes from the overrides entry in package.json.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const pkg = fileURLToPath(new URL('../node_modules/undici/package.json', import.meta.url));

test('the installed undici is 8.10.2 or newer', () => {
  const { version } = JSON.parse(readFileSync(pkg, 'utf8'));
  const [major, minor, patch] = version.split('.').map(Number);
  const atLeast = major > 8 || (major === 8 && (minor > 10 || (minor === 10 && patch >= 2)));
  assert.ok(atLeast, `undici ${version} is older than 8.10.2`);
});
