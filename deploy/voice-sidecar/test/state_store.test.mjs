// Desired-state store + boot decision tests (§12.1): exit persists idle;
// boot joins ONLY when the file says connected, or the file is absent AND
// env CHANNEL_ID is set. `idle` wins over CHANNEL_ID. A corrupt or
// unreadable file is SURFACED (fail-closed), never silently first-booted.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, chmodSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { containedFile, createStateStore, guildsFromStateFiles } from '../src/state_store.mjs';
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
  // The image's Dockerfile creates + chowns /var/lib/audio-controller, and
  // the compose service mounts a NAMED volume there (B-1: a bind mount would
  // mask that chown on a fresh host) — so at runtime the parent is NOT
  // writable by the node user but the state dir IS. The store must not need
  // to create the directory itself.
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
  // The env channel matching the persisted one; the mismatch case is
  // covered in lifecycle.test.mjs (2026-09-16 audit C-2).
  assert.equal(bootChannel({ desired: 'connected', channel_id: 'c-file' }, 'c-file'), 'c-file');
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
  const before = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(before.body.state_write_ok, true);

  failWrites = true;
  await h.join(); // join persists desired connected -> write fails
  const after = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(after.body.state_write_ok, false);
  assert.equal(after.body.voice, 'connected'); // the join itself still succeeded

  failWrites = false;
  const { generation } = after.body;
  await h.post('/voice/exit', { guild_id: GUILD, generation }); // persist recovers
  const recovered = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(recovered.body.state_write_ok, true);
  await h.close();
});

// --- which state files name a guild (multi-guild, 2026-09-17) ----------------
// THE GAP THESE CLOSE: a falsification probe that removed the snowflake check
// reddened NOTHING — `main()`'s boot loop has no test, and the filter lived in
// service.mjs behind a discord.js import where nothing could reach it.

test('the pre-multi-guild state file is skipped, not adopted as a guild', () => {
  // WHAT HAPPENED: the old layout kept ONE `desired_state.json` on this same
  // volume. Globbing `*.json` adopted it as a guild literally named
  // "desired_state" — every boot called guilds.fetch("desired_state"), failed,
  // and left a junk core in the map and in /health, while the real guild's
  // connection (whose state lived under the old filename) was never restored.
  const { guilds, skipped } = guildsFromStateFiles([
    'desired_state.json',
    '100000000000000203.json',
  ]);

  assert.deepEqual(guilds, ['100000000000000203']);
  assert.deepEqual(skipped, ['desired_state.json']);
});

test('only a Discord snowflake names a guild', () => {
  const { guilds, skipped } = guildsFromStateFiles([
    '100000000000000203.json',  // 19 digits — a real snowflake
    '12345.json',                // too short
    '../evil.json',              // traversal
    'notes.txt',                 // not state at all
    '.json',                     // empty stem
  ]);

  assert.deepEqual(guilds, ['100000000000000203']);
  assert.deepEqual(skipped, ['12345.json', '../evil.json', '.json']);
});

test('an empty directory yields no guilds and no complaints', () => {
  assert.deepEqual(guildsFromStateFiles([]), { guilds: [], skipped: [] });
});

// --- a guild id names a file only inside the state directory (A3) ------------
// The guild id reaches the file name from the control plane, so the file a
// store reads or writes must stay inside STATE_DIR whatever the id is.

test('containedFile names a file inside the directory', () => {
  const dir = tmp();
  assert.equal(containedFile(dir, '100000000000000203.json'), path.join(dir, '100000000000000203.json'));
  // A name that merely starts with dots is a name, not a traversal.
  assert.equal(containedFile(dir, '..state.json'), path.join(dir, '..state.json'));
});

test('containedFile refuses traversal, separators, absolute and empty names', () => {
  const dir = tmp();
  for (const name of ['../evil.json', '../../etc/x', '..', '.', '', 'a/b.json', 'a\\b.json', '/etc/passwd']) {
    assert.throws(() => containedFile(dir, name), /refusing state file name/, JSON.stringify(name));
  }
  assert.throws(() => containedFile(dir, undefined), /refusing state file name/);
});

test('containedFile refuses a symlink planted under the name that points outside', () => {
  const dir = tmp();
  const outside = tmp();
  writeFileSync(path.join(outside, 'target.json'), '{}');
  symlinkSync(path.join(outside, 'target.json'), path.join(dir, '100000000000000203.json'));
  assert.throws(() => containedFile(dir, '100000000000000203.json'), /escapes its directory/);
});
