// Connection events over the outbox (design/voice-channel/audio.md
// «Connection lifecycle»): voice-death, and channel-emptied after its grace
// period.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { makeHarness, tick, until, GUILD, CHANNEL } from './helpers.mjs';

test('voice-death: destroyed connection tears down to idle and pushes the event', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  h.connection.emit('stateChange', { status: 'ready' }, { status: 'destroyed' });
  assert.ok(await until(() => h.core.lifecycle.voice() === 'idle'));

  const status = await h.status();
  assert.equal(status.payload.voice, 'idle');
  assert.equal(status.payload.generation, null);
  for (let i = 0; i < 50 && h.leaves() === 0; i += 1) await tick(); // teardown persists idle BEFORE leave()
  assert.equal(h.leaves(), 1);

  const deaths = h.delivered.filter((e) => e.type === 'voice-death');
  assert.equal(deaths.length, 1);
  assert.equal(deaths[0].generation, generation);
  assert.equal(deaths[0].guild_id, GUILD);

  const late = await h.op('leave', { guild_id: GUILD, generation });
  assert.equal(late.outcome, 'rejected');
  assert.equal(late.error, 'generation_conflict');
  await h.close();
});

test('voice-death after a failed disconnect recovery window', async () => {
  const h = await makeHarness({ recoveryFails: true });
  const { generation } = await h.join();
  h.connection.emit('stateChange', { status: 'ready' }, { status: 'disconnected' });
  await tick();
  await tick();
  assert.deepEqual(h.entersStateCalls.map((c) => c.status), ['ready']);
  const status = await h.status();
  assert.equal(status.payload.voice, 'idle');
  assert.equal(h.delivered.filter((e) => e.type === 'voice-death').length, 1);
  void generation;
  await h.close();
});

// design/voice-channel/audio.md «Connection lifecycle» (2026-09-16, Miles): the last human leaving no longer fires
// channel-emptied immediately — it arms a 1-minute grace timer, and the
// event (plus the controller's own teardown) only happens if nobody returns
// before the timer fires. h.clock is the fake clock threaded into
// createLifecycle; advance() synchronously fires due timers.
async function waitFor(cond, tries = 50) {
  for (let i = 0; i < tries && !cond(); i += 1) await tick();
  return cond();
}

test('channel-emptied fires 60s after the human count drops to zero', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const channel = { members: new Map() };
  h.client.channels.cache.set(CHANNEL, channel);

  channel.members.set('bot', { user: { bot: true } });
  channel.members.set('human', { user: { bot: false } });
  h.client.emit('voiceStateUpdate');
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0);

  channel.members.delete('human');
  h.client.emit('voiceStateUpdate');
  await tick();
  await tick();
  // The drop to zero only ARMS the grace timer — music keeps playing and
  // nothing is emitted yet.
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0);
  const midway = await h.status();
  assert.equal(midway.payload.voice, 'connected');

  h.clock.advance(60_000);
  await waitFor(() => h.delivered.some((e) => e.type === 'channel-emptied'));
  const emptied = h.delivered.filter((e) => e.type === 'channel-emptied');
  assert.equal(emptied.length, 1);
  assert.equal(emptied[0].generation, generation);
  assert.equal(emptied[0].channel_id, CHANNEL);
  // The timer firing tears the connection down itself (design/voice-channel/audio.md «Connection lifecycle»: "the
  // controller tears the connection down AND writes desired=idle").
  assert.ok(await until(() => h.core.lifecycle.voice() === 'idle'));
  assert.ok(await waitFor(() => h.leaves() > 0));
  const after = await h.status();
  assert.equal(after.payload.voice, 'idle');
  await h.close();
});

test('channel-emptied fires 60s after the FIRST update after join is the last human leaving', async () => {
  // 2026-09-16 audit CN-3: membership is seeded from the joined channel, so
  // a human present at join whose departure is the first voice-state update
  // is still a >0 -> 0 transition (previously unknown -> 0 fired nothing).
  const h = await makeHarness();
  const channel = { members: new Map() };
  channel.members.set('bot', { user: { bot: true } });
  channel.members.set('human', { user: { bot: false } });
  h.client.channels.cache.set(CHANNEL, channel); // populated BEFORE the join

  const { generation } = await h.join();
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0); // seeding fires nothing

  channel.members.delete('human'); // the first observed update IS the departure
  h.client.emit('voiceStateUpdate');
  await tick();
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0); // armed, not fired

  h.clock.advance(60_000);
  await waitFor(() => h.delivered.some((e) => e.type === 'channel-emptied'));
  const emptied = h.delivered.filter((e) => e.type === 'channel-emptied');
  assert.equal(emptied.length, 1);
  assert.equal(emptied[0].generation, generation);
  assert.equal(emptied[0].channel_id, CHANNEL);
  await h.close();
});

test('channel-emptied arms on join into an already-empty channel and fires after 60s', async () => {
  // design/voice-channel/audio.md «Connection lifecycle»: "if Nora is alone in the channel from the start... the timer
  // arms on join and applies the same policy" — this used to be the ONE
  // case that never fired at all (old comment: "the bot sitting alone since
  // join is not a drop"). That is now wrong; it arms just like any other
  // >0 -> 0 transition, just at join time instead of via voiceStateUpdate.
  const h = await makeHarness();
  const channel = { members: new Map() };
  channel.members.set('bot', { user: { bot: true } }); // the bot alone
  h.client.channels.cache.set(CHANNEL, channel);

  const { generation } = await h.join();
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0); // armed, not fired

  h.clock.advance(59_000); // still inside the grace period
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0);

  h.clock.advance(1_000); // crosses the 60s mark
  await waitFor(() => h.delivered.some((e) => e.type === 'channel-emptied'));
  const emptied = h.delivered.filter((e) => e.type === 'channel-emptied');
  assert.equal(emptied.length, 1);
  assert.equal(emptied[0].generation, generation);
  assert.equal(emptied[0].channel_id, CHANNEL);
  // Delivery precedes teardown's real fs write; fake time/ticks do not finish it.
  assert.ok(await until(() => h.core.lifecycle.voice() === 'idle'));
  assert.ok(await waitFor(() => h.leaves() > 0));
  await h.close();
});

test('a human returning before the empty-channel timer fires cancels it', async () => {
  const h = await makeHarness();
  const channel = { members: new Map() };
  h.client.channels.cache.set(CHANNEL, channel);
  channel.members.set('bot', { user: { bot: true } });
  channel.members.set('human', { user: { bot: false } });

  await h.join();
  h.client.emit('voiceStateUpdate'); // seed observation only, no transition
  await tick();

  channel.members.delete('human'); // >0 -> 0: arms the 60s grace timer
  h.client.emit('voiceStateUpdate');
  await tick();

  h.clock.advance(30_000); // partway through the grace period
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0);

  channel.members.set('human', { user: { bot: false } }); // a human returns: cancels the timer
  h.client.emit('voiceStateUpdate');
  await tick();

  h.clock.advance(30_001); // past the ORIGINAL 60s mark
  await tick();
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0);
  const status = await h.status();
  assert.equal(status.payload.voice, 'connected');
  assert.equal(h.leaves(), 0);
  await h.close();
});

test('a manual Discord-UI disconnect during the empty-channel grace period cancels the pending timer', async () => {
  // Operator addendum (2026-09-16, mandatory): a manual disconnect already
  // fires stateChange -> destroyed -> handleDeath (existing, correct path,
  // not retested here beyond confirming it still fires). What's new is
  // making sure it also cancels a pending design/voice-channel/audio.md «Connection lifecycle» timer so that timer doesn't
  // later fire into an already-idle connection with a second event/leave.
  const h = await makeHarness();
  const channel = { members: new Map() };
  channel.members.set('bot', { user: { bot: true } });
  channel.members.set('human', { user: { bot: false } });
  h.client.channels.cache.set(CHANNEL, channel);

  const { generation } = await h.join();
  await tick();

  channel.members.delete('human'); // arms the 60s grace timer
  h.client.emit('voiceStateUpdate');
  await tick();

  // Manual disconnect via Discord UI, well before the 60s mark.
  h.connection.emit('stateChange', { status: 'ready' }, { status: 'destroyed' });
  // The death event is emitted before the asynchronous teardown completes.
  assert.ok(await until(() => h.core.lifecycle.voice() === 'idle'));

  const status = await h.status();
  assert.equal(status.payload.voice, 'idle');
  assert.ok(await waitFor(() => h.leaves() > 0));
  assert.equal(h.leaves(), 1);

  const deaths = h.delivered.filter((e) => e.type === 'voice-death');
  assert.equal(deaths.length, 1);
  assert.equal(deaths[0].generation, generation);

  // Advance past when the original grace timer would have fired: no second
  // teardown, no channel-emptied, no extra leave() — the timer was
  // cancelled by handleDeath's teardown('death'), not left to fire later.
  h.clock.advance(60_000);
  await tick();
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'channel-emptied').length, 0);
  assert.equal(h.delivered.filter((e) => e.type === 'voice-death').length, 1);
  assert.equal(h.leaves(), 1);
  await h.close();
});
