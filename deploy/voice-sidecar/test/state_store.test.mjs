// Desired-state store + boot decision tests (§12.1): exit persists idle;
// boot joins ONLY when the file says connected, or the file is absent AND
// env CHANNEL_ID is set. `idle` wins over CHANNEL_ID.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { mkdtempSync, writeFileSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { createStateStore } from '../src/state_store.mjs';
import { bootChannel } from '../src/lifecycle.mjs';
import { makeHarness, GUILD, CHANNEL } from './helpers.mjs';

const tmp = () => mkdtempSync(path.join(tmpdir(), 'voc-state-'));

test('state store round-trips and reads absent/corrupt files as null', async () => {
  const dir = tmp();
  const file = path.join(dir, 'desired_state.json');
  const store = createStateStore({ path: file });
  assert.equal(await store.read(), null); // absent
  await store.write({ desired: 'connected', channel_id: 'c1' });
  assert.deepEqual(await store.read(), { desired: 'connected', channel_id: 'c1' });
  await store.write({ desired: 'idle' });
  assert.deepEqual(await store.read(), { desired: 'idle' });
  // atomic write leaves no tmp files behind
  assert.deepEqual(readdirSync(dir), ['desired_state.json']);
  writeFileSync(file, '{not json');
  assert.equal(await store.read(), null); // corrupt
});

test('bootChannel: idle file wins over env CHANNEL_ID', () => {
  assert.equal(bootChannel({ desired: 'idle' }, 'c-env'), null);
});

test('bootChannel: absent file + env CHANNEL_ID joins (first boot)', () => {
  assert.equal(bootChannel(null, 'c-env'), 'c-env');
});

test('bootChannel: connected file joins its channel', () => {
  assert.equal(bootChannel({ desired: 'connected', channel_id: 'c-file' }, 'c-env'), 'c-file');
});

test('bootChannel: absent file + no env does not join', () => {
  assert.equal(bootChannel(null, undefined), null);
});

test('/voice/exit persists desired idle', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const res = await h.post('/voice/exit', { guild_id: GUILD, generation });
  assert.equal(res.status, 200);
  assert.deepEqual(JSON.parse(readFileSync(h.statePath, 'utf8')), { desired: 'idle' });
  await h.close();
});

test('successful join persists desired connected with the channel', async () => {
  const h = await makeHarness();
  await h.join();
  assert.deepEqual(JSON.parse(readFileSync(h.statePath, 'utf8')), {
    desired: 'connected',
    channel_id: CHANNEL,
  });
  await h.close();
});
