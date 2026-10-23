// Aikido round 2 (8764_81482): the installed pino must be 10.1.1 or newer. Baileys asks for
// pino ^9, so this pin comes from the overrides entry in package.json.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const pkg = fileURLToPath(new URL('./node_modules/pino/package.json', import.meta.url));

test('the installed pino is 10.1.1 or newer', () => {
  const { version } = JSON.parse(readFileSync(pkg, 'utf8'));
  const [major, minor, patch] = version.split('.').map(Number);
  const atLeast = major > 10 || (major === 10 && (minor > 1 || (minor === 1 && patch >= 1)));
  assert.ok(atLeast, `pino ${version} is older than 10.1.1`);
});
