// Join-seam pump ownership tests (2026-09-16 audit N-5/CN-2): the pump's
// TCP socket must never leak — a failed join closes the pump it created,
// and a successful replacement closes the superseded pump only AFTER the
// new connection is fully up.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createFrontDoor } from '../src/controller.mjs';
import { GUILD, TOKEN, AUTH } from './helpers.mjs';

import { createJoinVoice, loadConfiguredGuilds } from '../src/service.mjs';

function makeSeams() {
  const pumps = [];
  const joined = [];
  // [level, fields] — level is the POSITIONAL arg (what routes console.log
  // vs console.error in service.mjs's log()); fields.event, if present,
  // overrides the rendered event name. Kept as tuples, not a flattened
  // object, so a test can tell "logged at error level" apart from "the
  // rendered event name happens to be 'subscribe_failed'" — collapsing
  // those two was exactly the gap that let 5/6 mutants survive review.
  const logs = [];
  const seams = { joinError: null, playerError: null, pumps, joined, logs };
  seams.seam = createJoinVoice({
    joinImpl: async () => {
      if (seams.joinError) throw seams.joinError;
      const j = {
        // Truthy: a real PlayerSubscription. () => {} would make every join
        // here look like the subscribe_failed case to the new instrument.
        connection: { subscribe: () => ({}) },
        bus: new EventEmitter(),
        leaveCount: 0,
        leave: async () => {
          j.leaveCount += 1;
        },
      };
      joined.push(j);
      return j;
    },
    createPumpImpl: () => {
      const pump = () => {};
      pump.closeCount = 0;
      pump.close = () => {
        pump.closeCount += 1;
      };
      pumps.push(pump);
      return pump;
    },
    createPlayerImpl: () => {
      if (seams.playerError) throw seams.playerError;
      return new EventEmitter();
    },
    log: (level, fields) => logs.push([level, fields]),
  });
  return seams;
}

test('a failed join closes the pump it created and leaves the previous pump alone', async () => {
  const s = makeSeams();
  await s.seam.joinVoice('c1');
  const working = s.seam.currentPump();
  assert.equal(working, s.pumps[0]);
  // The success half of the subscribe instrument: its absence is what tells
  // an operator the call never even ran, so it needs its own coverage, not
  // just an assertion that the failure log stays silent on the happy path.
  assert.ok(
    s.logs.some(([level, fields]) => level === 'subscribe' && fields.ok === true && fields.channel_id === 'c1'),
  );

  s.joinError = new Error('discord down');
  await assert.rejects(s.seam.joinVoice('c1'), /discord down/);
  assert.equal(s.pumps[1].closeCount, 1); // the failed attempt's own pump
  assert.equal(working.closeCount, 0); // the working pump survives the failure
  assert.equal(s.seam.currentPump(), working); // still pointing at the live one
});

test('a successful replacement closes the superseded pump exactly once', async () => {
  const s = makeSeams();
  await s.seam.joinVoice('c1');
  assert.equal(s.pumps[0].closeCount, 0); // never closed while in use

  await s.seam.joinVoice('c1');
  assert.equal(s.pumps[0].closeCount, 1); // superseded pump closed
  assert.equal(s.pumps[1].closeCount, 0); // replacement is live
  assert.equal(s.seam.currentPump(), s.pumps[1]);
});

test('a failure after the discord join closes the pump AND tears down the half-built session', async () => {
  const s = makeSeams();
  s.playerError = new Error('player exploded');
  await assert.rejects(s.seam.joinVoice('c1'), /player exploded/);
  assert.equal(s.pumps[0].closeCount, 1); // pump closed
  assert.equal(s.joined[0].leaveCount, 1); // connection + client destroyed via leave()
  assert.equal(s.seam.currentPump(), null);
});

// Measured 2026-09-20: a subscribe() that doesn't take hold (with
// NoSubscriberBehavior.Pause) leaves ffmpeg decoding, the stall watch happy,
// and nothing reaching Discord — no error anywhere. `subscribe()` returning
// falsy is the one seam-visible signal of that failure; it must not be
// silently discarded the way the join itself still succeeds.
test('a subscribe() that returns falsy is logged as subscribe_failed, not swallowed', async () => {
  const logs = [];
  const seam = createJoinVoice({
    joinImpl: async () => ({
      connection: { subscribe: () => undefined },
      bus: new EventEmitter(),
      leave: async () => {},
    }),
    createPumpImpl: () => {
      const pump = () => {};
      pump.close = () => {};
      return pump;
    },
    createPlayerImpl: () => new EventEmitter(),
    log: (level, fields) => logs.push([level, fields]),
  });
  await seam.joinVoice('c1');
  // Both halves: the rendered name (what a reader filters logs by) AND the
  // positional level (what routes it to stderr, per service.mjs's log()).
  // Asserting only the rendered name let a mutant that renamed the level to
  // 'subscribe_failed' directly (silently dropping the 'error' routing)
  // pass; asserting only the level would miss a mutant that mislabeled the
  // rendered event.
  assert.ok(
    logs.some(([level, fields]) => level === 'error' && fields.event === 'subscribe_failed' && fields.channel_id === 'c1'),
  );
  assert.ok(!logs.some(([level]) => level === 'subscribe')); // success log did not also fire
});


test('configured guild admission refuses an unconfigured bot member before dispatch', async () => {
  // Exercise the exact callback wired by main, with only Discord replaced.
  const source = readFileSync(new URL('../src/service.mjs', import.meta.url), 'utf8');
  const begin = source.indexOf('authorizeGuild: async (guildId) => {') + 'authorizeGuild: '.length;
  const end = source.indexOf(',\n    controllerFor:', begin);
  const registry = { guildIds: () => [] };
  let member = true;
  let checks = 0;
  const authorizeGuild = new Function('registry', 'TOKEN', 'configuredGuilds', 'fetch',
    `return (${source.slice(begin, end)});`)(registry, 'synthetic-bot-token', new Set([GUILD]),
    async () => { checks++; return { ok: member, body: { cancel: async () => {} } }; });
  let dispatched = 0;
  const front = createFrontDoor({ authToken: TOKEN, authorizeGuild, health: () => ({}),
    controllerFor: () => { dispatched++; return { handleParsedPost: res => {
      res.writeHead(200); res.end('{}');
    } }; },
  });
  await new Promise(r => front.listen(0, '127.0.0.1', r));
  const post = guild_id => fetch(`http://127.0.0.1:${front.address().port}/voice/join`, {
    method: 'POST', headers: { ...AUTH, 'content-type': 'application/json' },
    body: JSON.stringify({ guild_id }),
  });
  try {
    const unconfigured = '200000000000000203';
    assert.equal((await post(unconfigured)).status, 403);
    registry.guildIds = () => [unconfigured];
    assert.equal((await post(unconfigured)).status, 403, 'existing cores cannot bypass configuration');
    assert.equal(checks, 0);
    assert.equal(dispatched, 0);
    assert.equal((await post(GUILD)).status, 200);
    member = false;
    assert.equal((await post(GUILD)).status, 403, 'configuration still requires Discord membership');
    assert.equal(dispatched, 1);
  } finally { front.closeAllConnections(); await new Promise(r => front.close(r)); }
});


test('guild configuration reads the existing TOML sidecars list and fails closed', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'voice-config-'));
  const file = path.join(dir, 'config.toml');
  try {
    writeFileSync(file, `[speech.tts]\nsidecars = [{ guild = "${GUILD}", endpoint = "http://voice-sidecar:4600" }]\n`);
    assert.deepEqual([...loadConfiguredGuilds(file)], [GUILD]);
    writeFileSync(file, '[speech.tts]\nsidecars = []\n');
    assert.equal(loadConfiguredGuilds(file).size, 0);
    for (const invalid of ['broken = [', '[speech.tts]\nsidecars = [{guild = "invalid"}]']) {
      writeFileSync(file, invalid);
      assert.throws(() => loadConfiguredGuilds(file), { message: 'Cannot load configured voice guilds; refusing to start' });
    }
    assert.throws(() => loadConfiguredGuilds(path.join(dir, 'absent.toml')),
      { message: 'Cannot load configured voice guilds; refusing to start' });
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
