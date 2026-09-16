// Boot-decision tests (§12.1 + 2026-09-16 audit C-2): the persisted
// desired-state channel is honored only while it matches env CHANNEL_ID.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { bootChannel } from '../src/lifecycle.mjs';

test('boot rejoins the persisted channel when it matches env CHANNEL_ID', () => {
  const errors = [];
  const log = (event, fields) => (event === 'error' ? errors.push(fields) : null);
  assert.equal(bootChannel({ desired: 'connected', channel_id: 'c1' }, 'c1', log), 'c1');
  assert.deepEqual(errors, []); // no mismatch logged for a matching pair
});

test('boot treats a persisted channel mismatching env CHANNEL_ID as absent: ERROR + rejoin the env channel', () => {
  const errors = [];
  const log = (event, fields) => (event === 'error' ? errors.push(fields) : null);
  // The operator changed the channel; boot-joining the OLD one would park
  // the controller where /voice/join refuses every command.
  assert.equal(bootChannel({ desired: 'connected', channel_id: 'c-old' }, 'c-new', log), 'c-new');
  assert.equal(errors.length, 1);
  assert.equal(errors[0].event, 'desired_state_channel_mismatch');
  assert.equal(errors[0].persisted_channel_id, 'c-old');
  assert.equal(errors[0].env_channel_id, 'c-new');
});

test('boot: idle wins over env; absent file joins env; absent both stays idle', () => {
  assert.equal(bootChannel({ desired: 'idle' }, 'c1'), null);
  assert.equal(bootChannel(null, 'c1'), 'c1');
  assert.equal(bootChannel(null, null), null);
  assert.equal(bootChannel({ desired: 'connected' }, 'c1'), 'c1'); // file without a channel falls back to env
});
