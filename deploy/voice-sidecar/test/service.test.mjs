// Join-seam pump ownership tests (2026-09-16 audit N-5/CN-2): the pump's
// TCP socket must never leak — a failed join closes the pump it created,
// and a successful replacement closes the superseded pump only AFTER the
// new connection is fully up.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';

import { createJoinVoice } from '../src/service.mjs';

function makeSeams() {
  const pumps = [];
  const joined = [];
  const seams = { joinError: null, playerError: null, pumps, joined };
  seams.seam = createJoinVoice({
    joinImpl: async () => {
      if (seams.joinError) throw seams.joinError;
      const j = {
        connection: { subscribe: () => {} },
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
    log: () => {},
  });
  return seams;
}

test('a failed join closes the pump it created and leaves the previous pump alone', async () => {
  const s = makeSeams();
  await s.seam.joinVoice('c1');
  const working = s.seam.currentPump();
  assert.equal(working, s.pumps[0]);

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
