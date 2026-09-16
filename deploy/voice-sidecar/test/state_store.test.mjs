// Desired-state store + boot decision tests (§12.1): exit persists idle;
// boot joins ONLY when the file says connected, or the file is absent AND
// env CHANNEL_ID is set. `idle` wins over CHANNEL_ID. A corrupt or
// unreadable file is SURFACED (fail-closed), never silently first-booted.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { createStateStore } from '../src/state_store.mjs';
import { bootChannel } from '../src/lifecycle.mjs';
import { makeHarness, GUILD, CHANNEL } from './helpers.mjs';

const tmp = () => mkdtempSync(path.join(tmpdir(), 'voc-state-'));

test('state store round-trips; absent reads as status absent', async () => {
  const dir = tmp();
  const file = path.join(dir, 'desired_state.json');
  const store = createStateStore({ path: file });
  assert.deepEqual(await store.read(), { status: 'absent', state: null });
  await store.write({ desired: 'connected', channel_id: 'c1' });
  assert.deepEqual(await store.read(), { status: 'ok', state: { desired: 'connected', channel_id: 'c1' } });
  await store.write({ desired: 'idle' });
  assert.deepEqual(await store.read(), { status: 'ok', state: { desired: 'idle' } });
  // atomic write leaves no tmp files behind
  assert.deepEqual(readdirSync(dir), ['desired_state.json']);
});

test('a corrupt state file is surfaced as corrupt, NOT read as first-boot absent', async () => {
  const dir = tmp();
  const file = path.join(dir, 'desired_state.json');
  const store = createStateStore({ path: file });
  writeFileSync(file, '{not json');
  const res = await store.read();
  assert.equal(res.status, 'corrupt');
  assert.equal(res.state, null);
  assert.equal(typeof res.error, 'string');
  // And a JSON scalar is not a state object either.
  writeFileSync(file, '42');
  assert.equal((await store.read()).status, 'corrupt');
});

test('an unreadable state file is surfaced as error, NOT read as first-boot absent', async () => {
  const dir = tmp();
  const file = path.join(dir, 'desired_state.json');
  mkdirSync(file); // a directory at the state path: readFile fails with EISDIR
  const res = await createStateStore({ path: file }).read();
  assert.equal(res.status, 'error');
  assert.equal(res.state, null);
  assert.equal(typeof res.error, 'string');
});

test('state writes succeed as a non-root runtime user when the state dir exists', async (t) => {
  if (process.getuid?.() === 0) {
    t.skip('running as root: permission bits are not enforced');
    return;
  }
  // The Dockerfile creates + chowns /var/lib/audio-controller, so at runtime
  // the parent is NOT writable by the node user but the state dir IS. The
  // store must not need to create the directory itself.
  const dir = tmp();
  const stateDir = path.join(dir, 'audio-controller');
  mkdirSync(stateDir);
  chmodSync(dir, 0o555); // parent read-only: mkdir -p of an existing dir must still succeed
  try {
    const store = createStateStore({ path: path.join(stateDir, 'desired_state.json') });
    await store.write({ desired: 'connected', channel_id: 'c1' });
    assert.deepEqual(await store.read(), { status: 'ok', state: { desired: 'connected', channel_id: 'c1' } });
  } finally {
    chmodSync(dir, 0o755);
  }
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

test('simulated restart after /voice/exit does NOT rejoin (boot reads idle)', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  await h.post('/voice/exit', { guild_id: GUILD, generation });
  await h.close();

  // New process, same state file: the boot decision must say "stay idle"
  // even with CHANNEL_ID set in the environment.
  const stored = await createStateStore({ path: h.statePath }).read();
  assert.equal(stored.status, 'ok');
  assert.equal(bootChannel(stored.state, CHANNEL), null);

  const h2 = await makeHarness({ statePath: h.statePath });
  assert.equal(h2.joinCalls.length, 0);
  await h2.close();
});

test('exit intent is persisted BEFORE connection teardown begins (crash cannot boot-loop)', async () => {
  let stateAtTeardown = null;
  const h = await makeHarness({
    // The moment the connection's leave() runs, the state file must already
    // say idle — a crash between the two must not reboot into the channel.
    leaveGate: async () => {
      stateAtTeardown = JSON.parse(readFileSync(h.statePath, 'utf8'));
    },
  });
  const { generation } = await h.join();
  await h.post('/voice/exit', { guild_id: GUILD, generation });
  assert.deepEqual(stateAtTeardown, { desired: 'idle' });
  assert.equal(h.leaves(), 1);
  await h.close();
});

test('a state write failure is loud: state_write_ok flips false in /voice/status', async () => {
  const dir = tmp();
  const statePath = path.join(dir, 'desired_state.json');
  let failWrites = false;
  const stateStore = {
    read: async () => ({ status: 'absent', state: null }),
    write: async () => {
      if (failWrites) throw new Error('EACCES: permission denied');
    },
    path: statePath,
  };
  const h = await makeHarness({ stateStore });
  const before = await h.get('/voice/status');
  assert.equal(before.body.state_write_ok, true);

  failWrites = true;
  await h.join(); // join persists desired connected -> write fails
  const after = await h.get('/voice/status');
  assert.equal(after.body.state_write_ok, false);
  assert.equal(after.body.voice, 'connected'); // the join itself still succeeded

  failWrites = false;
  const { generation } = after.body;
  await h.post('/voice/exit', { guild_id: GUILD, generation }); // persist recovers
  const recovered = await h.get('/voice/status');
  assert.equal(recovered.body.state_write_ok, true);
  await h.close();
});
