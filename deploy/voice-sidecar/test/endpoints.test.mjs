// Endpoint contract tests (design/voice-channel/07 §3): response shapes,
// generation scoping, single-guild binding, replace-play, stop-vs-exit,
// pause-during-speech, volume bounds, speech bounds, status/health,
// request_id dedupe, voice-death and channel-emptied events.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import net from 'node:net';

import { createFrontDoor } from '../src/controller.mjs';
import { makeHarness, playTrack, tick, until, GUILD, CHANNEL } from './helpers.mjs';

const PINNED_SNAPSHOT_KEYS = [
  'voice',
  'generation',
  'channel_id',
  'playback_id',
  'player',
  // "is sound actually moving" ground truth, alongside `player.position_ms`
  // (feat(voice): ask whether sound is moving, instead of assuming it is) —
  // added to `snapshot()` in controller.mjs but missed here, which is how
  // this pinning went stale without failing until the next full run.
  'autopaused',
  'subscribers',
  'playable',
  'pending_reaps',
  'speech_queue',
  'crossfade_seconds',
];

test('join returns the pinned state snapshot shape with a minted generation', async () => {
  const h = await makeHarness();
  const res = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id: 'req-1' });
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.request_id, 'req-1');
  assert.deepEqual(Object.keys(res.body.state).sort(), PINNED_SNAPSHOT_KEYS.sort());
  const s = res.body.state;
  assert.equal(s.voice, 'connected');
  assert.equal(typeof s.generation, 'string');
  assert.equal(s.channel_id, CHANNEL);
  assert.equal(s.playback_id, null);
  assert.deepEqual(s.player, { status: 'idle', position_ms: 0, position_policy: null, current: null });
  assert.equal(s.autopaused, false);
  assert.equal(s.subscribers, 0);
  assert.equal(s.playable, 0);
  assert.equal(s.pending_reaps, 0);
  assert.equal(s.speech_queue, 0);
  assert.equal(s.crossfade_seconds, 0);
  await h.close();
});

test('request_id echoes null when absent', async () => {
  const h = await makeHarness();
  const res = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL });
  assert.equal(res.status, 200);
  assert.equal(res.body.request_id, null);
  await h.close();
});

test('join is idempotent in the same channel and 409 connected_elsewhere in another', async () => {
  const h = await makeHarness();
  const first = await h.join();
  const again = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL });
  assert.equal(again.status, 200);
  assert.equal(again.body.state.generation, first.generation);
  assert.equal(h.joinCalls.length, 1); // no second join happened

  // §12.2 reversed 2026-09-17: a second channel is no longer refused for
  // being unconfigured (400 not_in_allowed_channel) — it is refused because
  // Discord allows ONE voice connection per bot per guild, which is the
  // lifecycle's 409 and not an allow-list at all.
  const elsewhere = await h.post('/voice/join', { guild_id: GUILD, channel_id: 'c2' });
  assert.equal(elsewhere.status, 409);
  assert.equal(elsewhere.body.error, 'connected_elsewhere');
  assert.equal(h.joinCalls.length, 1);
  await h.close();
});

test('join admits ANY channel in the guild when idle (§12.2, reversed 2026-09-17)', async () => {
  // The regression this guards: phase 1 answered 400 not_in_allowed_channel
  // for every channel but the configured one, so a member sitting in any
  // other voice channel could never be joined — which is the whole reason
  // the channel binding went. Idle, not connected, so no 409 is in play.
  const h = await makeHarness();
  const res = await h.post('/voice/join', { guild_id: GUILD, channel_id: 'some-other-channel' });
  assert.equal(res.status, 200);
  assert.equal(res.body.state.channel_id, 'some-other-channel');
  assert.equal(h.joinCalls.length, 1);
  assert.equal(h.joinCalls[0], 'some-other-channel');
  await h.close();
});

test('join with the wrong guild is 400 wrong_guild', async () => {
  const h = await makeHarness();
  const res = await h.post('/voice/join', { guild_id: 'g2', channel_id: CHANNEL });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'wrong_guild');
  assert.equal(h.joinCalls.length, 0);
  await h.close();
});

test('every generation-scoped endpoint rejects a stale generation with 409', async () => {
  const h = await makeHarness();
  await h.join();
  const scoped = [
    ['/voice/exit', {}],
    ['/music/play', { stream_url: 'https://cdn.test/a' }],
    ['/music/stop', {}],
    ['/music/pause', {}],
    ['/music/resume', {}],
    ['/music/volume', { percent: 100 }],
    ['/stream', { audio_b64: Buffer.from('tts').toString('base64'), media_type: 'audio/mpeg' }],
  ];
  for (const [path, extra] of scoped) {
    const res = await h.post(path, { guild_id: GUILD, generation: 'stale-gen', ...extra });
    assert.equal(res.status, 409, path);
    assert.equal(res.body.error, 'generation_conflict', path);
  }
  assert.equal(h.procs.length, 0); // nothing acted
  assert.equal(h.leaves(), 0);
  await h.close();
});

test('replace-play over HTTP: old playback emits no track-finished, new playback_id is minted', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const a = await playTrack(h, { generation, title: 'A' });
  const b = await h.post('/music/play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/b', title: 'B' });
  assert.equal(b.status, 200);
  const bId = b.body.state.playback_id;
  assert.notEqual(bId, a.playbackId);
  assert.deepEqual(a.proc.signals, ['SIGTERM']);

  await tick(); // the replacement spawn waits for the old process to reap
  h.procs[1].data();
  await tick();
  h.player.current.playbackDuration = 5_000;
  h.player.finish();
  await tick();
  await tick();

  const finished = h.delivered.filter((e) => e.type === 'track-finished');
  assert.equal(finished.length, 1);
  assert.equal(finished[0].playback_id, bId);
  assert.equal(finished[0].guild_id, GUILD);
  assert.equal(finished[0].generation, generation);
  assert.equal(typeof finished[0].event_id, 'string');
  assert.equal(finished[0].event_seq, 1);
  await h.close();
});

test('/music/stop stays connected: connection and wake pump survive, no-op when idle', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  await playTrack(h, { generation });

  const res = await h.post('/music/stop', { guild_id: GUILD, generation });
  assert.equal(res.status, 200);
  assert.equal(res.body.state.voice, 'connected');
  assert.equal(res.body.state.generation, generation);
  assert.equal(res.body.state.playback_id, null);
  assert.equal(h.leaves(), 0); // connection NOT destroyed
  assert.equal(h.pumpCloses(), 0); // wake pump NOT closed

  // No music playing/starting/suspended -> 200 no-op.
  const again = await h.post('/music/stop', { guild_id: GUILD, generation });
  assert.equal(again.status, 200);
  await h.close();
});

test('/voice/exit stops everything: music, speech, pump, connection; generation invalidated', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const a = await playTrack(h, { generation });
  await h.post('/stream', {
    guild_id: GUILD,
    generation,
    audio_b64: Buffer.from('tts').toString('base64'),
    media_type: 'audio/mpeg',
  });

  const res = await h.post('/voice/exit', { guild_id: GUILD, generation });
  assert.equal(res.status, 200);
  assert.equal(res.body.state.voice, 'idle');
  assert.equal(res.body.state.generation, null);
  assert.equal(h.leaves(), 1); // connection destroyed
  assert.equal(h.pumpCloses(), 1); // wake pump closed
  assert.deepEqual(a.proc.signals, ['SIGTERM']); // music ffmpeg terminated

  const late = await h.post('/music/play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/c' });
  assert.equal(late.status, 409); // old generation is dead
  assert.equal(h.procs.length, 1);
  await h.close();
});

test('any endpoint with a body guild_id != configured guild is 400 wrong_guild', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  for (const path of ['/music/play', '/music/stop', '/voice/exit', '/stream']) {
    const res = await h.post(path, { guild_id: 'g2', generation, stream_url: 'u', audio_b64: 'eA' });
    assert.equal(res.status, 400, path);
    assert.equal(res.body.error, 'wrong_guild', path);
  }
  await h.close();
});

test('/music/pause during speech is 409 pause_during_speech', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  await playTrack(h, { generation });
  const speech = await h.post('/stream', {
    guild_id: GUILD,
    generation,
    audio_b64: Buffer.from('tts').toString('base64'),
    media_type: 'audio/mpeg',
  });
  assert.equal(speech.status, 200);
  assert.equal(speech.body.state.player.status, 'suspended');

  const res = await h.post('/music/pause', { guild_id: GUILD, generation });
  assert.equal(res.status, 409);
  assert.equal(res.body.error, 'pause_during_speech');
  await h.close();
});

test('/music/volume accepts 0..200 and rejects the rest with 400 percent_out_of_range', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  await playTrack(h, { generation });
  for (const percent of [0, 100, 200]) {
    const res = await h.post('/music/volume', { guild_id: GUILD, generation, percent });
    assert.equal(res.status, 200, `percent=${percent}`);
  }
  assert.equal(h.player.current.volume.value, 2);
  for (const percent of [-1, 201, 'loud', null]) {
    const res = await h.post('/music/volume', { guild_id: GUILD, generation, percent });
    assert.equal(res.status, 400, `percent=${percent}`);
    assert.equal(res.body.error, 'percent_out_of_range');
  }
  await h.close();
});

test('/stream enforces the decoded 2MB body budget and the bounded speech queue', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();

  const tooBig = Buffer.alloc(2 * 1024 * 1024 + 1, 1);
  const big = await h.post('/stream', {
    guild_id: GUILD,
    generation,
    audio_b64: tooBig.toString('base64'),
    media_type: 'audio/mpeg',
  });
  assert.equal(big.status, 400);
  assert.equal(big.body.error, 'audio_too_large');

  for (let i = 0; i < 5; i += 1) {
    const res = await h.post('/stream', {
      guild_id: GUILD,
      generation,
      audio_b64: Buffer.from(`s${i}`).toString('base64'),
      media_type: 'audio/mpeg',
    });
    assert.equal(res.status, 200, `speech ${i}`);
  }
  const full = await h.post('/stream', {
    guild_id: GUILD,
    generation,
    audio_b64: Buffer.from('s5').toString('base64'),
    media_type: 'audio/mpeg',
  });
  assert.equal(full.status, 503);
  assert.equal(full.body.error, 'speech_queue_full');
  await h.close();
});

test('/voice/status carries the snapshot plus seq/streaming/error/wake/pump', async () => {
  const h = await makeHarness();
  const idle = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(idle.status, 200);
  assert.equal(idle.body.last_event_seq, 0);
  assert.equal(idle.body.streaming, false);
  assert.equal(idle.body.last_error, null);
  assert.deepEqual(idle.body.wake, { u1: { frames: 1, bytes: 3840 } });
  assert.deepEqual(idle.body.pump, {
    host: '127.0.0.1',
    port: 4599,
    drops: { backlog_dropped: 0, no_listener_dropped: 0 },
  });

  const { generation } = await h.join();
  const t = await playTrack(h, { generation });
  const playing = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(playing.body.streaming, true);
  assert.equal(playing.body.player.status, 'playing');
  assert.equal(playing.body.playback_id, t.playbackId);
  assert.deepEqual(playing.body.player.current, { title: 'A', url: 'https://cdn.test/a' });

  t.proc.close(1); // abnormal exit -> stream-failed surfaces in status
  await tick();
  await tick();
  const failed = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(failed.body.streaming, false);
  assert.equal(failed.body.last_error, 'ffmpeg-exit-1');
  assert.equal(failed.body.last_event_seq, 1);
  assert.equal(failed.body.outbox_dropped, 0);
  await h.close();
});

test('/health is 200 liveness even healthy-but-idle, and /play is gone', async () => {
  const h = await makeHarness();
  const health = await h.get('/health');
  assert.equal(health.status, 200);
  assert.equal(health.body.status, 'ok');
  // PROCESS liveness, not one guild's, since 2026-09-17: one process serves
  // several guilds, so `voice` (a single connection's state) could not be a
  // property of the whole. The compose HEALTHCHECK curls this and must get
  // 200 while the process holds no connection at all.
  assert.deepEqual(health.body.guilds, [GUILD]);

  const gone = await h.post('/play', { audio_b64: 'eA', media_type: 'audio/mpeg' });
  assert.equal(gone.status, 404);
  await h.close();
});

test('same request_id within 5 minutes returns the memoized response without re-acting', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const body = {
    guild_id: GUILD,
    generation,
    stream_url: 'https://cdn.test/a',
    request_id: 'req-dedupe',
  };
  const first = await h.post('/music/play', body);
  assert.equal(first.status, 200);
  assert.equal(h.procs.length, 1);

  const second = await h.post('/music/play', body);
  assert.equal(second.status, 200);
  assert.deepEqual(second.body, first.body); // memoized response, same playback_id
  assert.equal(h.procs.length, 1); // handler NOT re-executed

  h.clock.advance(5 * 60 * 1000 + 1); // past the dedupe window
  const third = await h.post('/music/play', body);
  assert.equal(third.status, 200);
  assert.equal(h.procs.length, 2); // re-executed after expiry
  await h.close();
});

test('voice-death: destroyed connection tears down to idle and pushes the event', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  h.connection.emit('stateChange', { status: 'ready' }, { status: 'destroyed' });
  assert.ok(await until(() => h.core.lifecycle.voice() === 'idle'));

  const status = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(status.body.voice, 'idle');
  assert.equal(status.body.generation, null);
  for (let i = 0; i < 50 && h.leaves() === 0; i += 1) await tick(); // teardown persists idle BEFORE leave()
  assert.equal(h.leaves(), 1);

  const deaths = h.delivered.filter((e) => e.type === 'voice-death');
  assert.equal(deaths.length, 1);
  assert.equal(deaths[0].generation, generation);
  assert.equal(deaths[0].guild_id, GUILD);

  const late = await h.post('/voice/exit', { guild_id: GUILD, generation });
  assert.equal(late.status, 409);
  await h.close();
});

test('voice-death after a failed disconnect recovery window', async () => {
  const h = await makeHarness({ recoveryFails: true });
  const { generation } = await h.join();
  h.connection.emit('stateChange', { status: 'ready' }, { status: 'disconnected' });
  await tick();
  await tick();
  assert.deepEqual(h.entersStateCalls.map((c) => c.status), ['ready']);
  const status = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(status.body.voice, 'idle');
  assert.equal(h.delivered.filter((e) => e.type === 'voice-death').length, 1);
  void generation;
  await h.close();
});

// §12.6 (2026-09-16, Miles): the last human leaving no longer fires
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
  const midway = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(midway.body.voice, 'connected');

  h.clock.advance(60_000);
  await waitFor(() => h.delivered.some((e) => e.type === 'channel-emptied'));
  const emptied = h.delivered.filter((e) => e.type === 'channel-emptied');
  assert.equal(emptied.length, 1);
  assert.equal(emptied[0].generation, generation);
  assert.equal(emptied[0].channel_id, CHANNEL);
  // The timer firing tears the connection down itself (§12.6: "the
  // controller tears the connection down AND writes desired=idle").
  assert.ok(await until(() => h.core.lifecycle.voice() === 'idle'));
  assert.ok(await waitFor(() => h.leaves() > 0));
  const after = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(after.body.voice, 'idle');
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
  // §12.6: "if Nora is alone in the channel from the start... the timer
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
  const status = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(status.body.voice, 'connected');
  assert.equal(h.leaves(), 0);
  await h.close();
});

test('a manual Discord-UI disconnect during the empty-channel grace period cancels the pending timer', async () => {
  // Operator addendum (2026-09-16, mandatory): a manual disconnect already
  // fires stateChange -> destroyed -> handleDeath (existing, correct path,
  // not retested here beyond confirming it still fires). What's new is
  // making sure it also cancels a pending §12.6 timer so that timer doesn't
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

  const status = await h.get(`/voice/status?guild_id=${GUILD}`);
  assert.equal(status.body.voice, 'idle');
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

test('error responses carry error, request_id and state', async () => {
  const h = await makeHarness();
  const res = await h.post('/music/stop', { guild_id: GUILD, generation: 'nope', request_id: 'r-err' });
  assert.equal(res.status, 409);
  assert.deepEqual(Object.keys(res.body).sort(), ['error', 'request_id', 'state']);
  assert.equal(res.body.request_id, 'r-err');
  assert.equal(res.body.state.voice, 'idle');
  await h.close();
});

test('an over-limit body is 413 body_too_large, distinct from a transport read failure', async () => {
  // 2026-09-16 audit C-5: over-limit and transport-error used to share one
  // 400 body_too_large. The design contract is silent on the status; the
  // split chosen is 413 for over-limit (400 stays for genuine bad requests)
  // — nora's client maps only 400/409 explicitly, so 413 takes its generic
  // fail-closed unavailable path and no Python-side mapping needs to change.
  const h = await makeHarness();
  const big = await h.post('/music/stop', { guild_id: GUILD, generation: 'x', pad: 'x'.repeat(70 * 1024) });
  assert.equal(big.status, 413);
  assert.equal(big.body.error, 'body_too_large');
  assert.equal(big.body.request_id, null);
  await h.close();
});

test('a body that fails mid-read is 400 body_read_failed, not body_too_large', async () => {
  const h = await makeHarness();
  const port = h.port;
  const raw = await new Promise((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port }, () => {
      // Promise 100 bytes, send 7, then FIN: the upload dies mid-body but
      // the half-open socket can still carry the answer.
      socket.write('POST /music/stop HTTP/1.1\r\nhost: x\r\ncontent-length: 100\r\nconnection: close\r\n\r\npartial');
      socket.end();
    });
    let data = '';
    socket.on('data', (chunk) => {
      data += chunk.toString();
    });
    socket.on('close', () => resolve(data));
    socket.on('error', reject);
  });
  assert.match(raw, /^HTTP\/1\.1 400 /);
  assert.match(raw, /body_read_failed/);

  // The server survived: a normal request still works.
  const { res } = await h.join();
  assert.equal(res.status, 200);
  await h.close();
});

test('two joins waiting on one exit produce exactly ONE subsequent joinVoice', async () => {
  let releaseLeave;
  const leaveGate = new Promise((resolve) => {
    releaseLeave = resolve;
  });
  const h = await makeHarness({ leaveGate: () => leaveGate });
  const { generation } = await h.join();
  assert.equal(h.joinCalls.length, 1);

  const exitP = h.post('/voice/exit', { guild_id: GUILD, generation });
  assert.equal(await until(() => h.leaves() === 1), true); // teardown parked inside leave()
  const j1 = h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL });
  const j2 = h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL });
  assert.equal(await until(() => h.core.lifecycle.voice() === 'destroying'), true);
  await tick();
  await tick();
  releaseLeave();

  const [exitRes, r1, r2] = await Promise.all([exitP, j1, j2]);
  assert.equal(exitRes.status, 200);
  assert.equal(r1.status, 200);
  assert.equal(r2.status, 200);
  assert.equal(h.joinCalls.length, 2); // exactly one re-join, not two
  assert.equal(r1.body.state.generation, r2.body.state.generation);
  assert.notEqual(r1.body.state.generation, generation);
  await h.close();
});

test('an intentional exit emits NO voice-death and invalidates the generation at teardown start', async () => {
  let releaseLeave;
  const leaveGate = new Promise((resolve) => {
    releaseLeave = resolve;
  });
  const h = await makeHarness({ leaveGate: () => leaveGate });
  const { generation } = await h.join();

  const exitP = h.post('/voice/exit', { guild_id: GUILD, generation });
  assert.equal(await until(() => h.leaves() === 1), true); // teardown parked inside leave(); generation already dead
  const late = await h.post('/music/play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/x' });
  assert.equal(late.status, 409);
  assert.equal(late.body.error, 'generation_conflict');

  // The deliberate destroy provokes the connection's own 'destroyed'
  // notification — it must read as stale, not as a voice-death.
  h.connection.emit('stateChange', { status: 'ready' }, { status: 'destroyed' });
  releaseLeave();
  await exitP;
  await tick();
  await tick();
  assert.equal(h.delivered.filter((e) => e.type === 'voice-death').length, 0);
  await h.close();
});

test('two concurrent POSTs with the same request_id execute the handler exactly once', async () => {
  // 2026-09-16 audit CN-6: this pins the CONTROLLER's request_id dedupe, so
  // the op must be one the lifecycle does NOT already coalesce — join/exit
  // ride joinPromise/destroyPromise and prove nothing about this layer.
  // Music replacement is non-idempotent: without dedupe, two plays would
  // mean two ffmpeg spawns and two different playback_ids.
  const h = await makeHarness();
  const { generation } = await h.join();
  const body = { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a', request_id: 'req-concurrent' };
  const p1 = h.post('/music/play', body);
  const p2 = h.post('/music/play', body); // fired inside the first request's window
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.status, 200);
  assert.equal(r2.status, 200);
  assert.equal(h.procs.length, 1); // the handler executed ONCE
  assert.deepEqual(r2.body, r1.body); // same playback_id, same snapshot
  await h.close();
});

test('/music/play accepts an optional boolean resumable; a non-boolean is 400 invalid_body', async () => {
  // 2026-09-16 audit CN-8: nora forwards the source's resumable capability
  // (seekable vs live) in the play POST; absent keeps the arbiter default.
  const h = await makeHarness();
  const { generation } = await h.join();

  for (const resumable of ['yes', 1, null]) {
    const bad = await h.post('/music/play', {
      guild_id: GUILD,
      generation,
      stream_url: 'https://cdn.test/x',
      resumable,
    });
    assert.equal(bad.status, 400, `resumable=${resumable}`);
    assert.equal(bad.body.error, 'invalid_body');
  }
  assert.equal(h.procs.length, 0); // nothing acted

  const live = await h.post('/music/play', {
    guild_id: GUILD,
    generation,
    stream_url: 'https://cdn.test/radio',
    resumable: false,
  });
  assert.equal(live.status, 200);
  assert.equal(live.body.state.player.position_policy, 'none'); // live source: no resume

  const regular = await h.post('/music/play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a' });
  assert.equal(regular.status, 200);
  assert.equal(regular.body.state.player.position_policy, 'approximate_seek'); // default unchanged
  await h.close();
});

test('a retried /voice/exit with the same request_id returns the memo without re-acting', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const body = { guild_id: GUILD, generation, request_id: 'req-exit-1' };
  const first = await h.post('/voice/exit', body);
  assert.equal(first.status, 200);
  assert.equal(h.leaves(), 1);

  // The generation is dead now; without dedupe this retry would be a 409
  // (or worse, a second teardown). The memo answers instead.
  const second = await h.post('/voice/exit', body);
  assert.equal(second.status, 200);
  assert.deepEqual(second.body, first.body);
  assert.equal(h.leaves(), 1);
  await h.close();
});

test('a JSON null body is 400 invalid_body and the server keeps serving', async () => {
  const h = await makeHarness();
  const raw = await fetch(`http://127.0.0.1:${h.port}/music/stop`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: 'null',
  });
  assert.equal(raw.status, 400);
  const parsed = await raw.json();
  assert.equal(parsed.error, 'invalid_body');

  const list = await fetch(`http://127.0.0.1:${h.port}/music/stop`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '[1,2]',
  });
  assert.equal(list.status, 400);

  // The server survived: a normal request still works.
  const { res } = await h.join();
  assert.equal(res.status, 200);
  await h.close();
});

// --- one process, several guilds (2026-09-17) --------------------------------
// The front door is the only thing that sees a request before its guild is
// known. These pin what it does with that: route it, or refuse it.

test('a POST for a guild with no core is refused, and mints nothing', async () => {
  // THE BOUND THIS GUARDS: cores are minted on JOIN only. Minting on any POST
  // would make an unbounded map keyed by whatever a caller puts in guild_id —
  // the same shape as the unknown-guild hole fixed on nora's side the same
  // day, and this control plane has no auth either.
  const h = await makeHarness();

  const res = await h.post('/music/stop', { guild_id: 'a-guild-with-no-core', generation: 'x' });

  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'wrong_guild');
  await h.close();
});

test('a POST with no guild_id at all is refused before any handler runs', async () => {
  const h = await makeHarness();

  const res = await h.post('/music/stop', { generation: 'x' });

  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'wrong_guild');
  await h.close();
});

test('/voice/status requires a guild and 404s an unknown one', async () => {
  // A GET carries no body, so the guild rides the query string. Defaulting to
  // "the only guild" would report one server's playback as another's the
  // moment a second one connected.
  const h = await makeHarness();

  const missing = await h.get('/voice/status');
  assert.equal(missing.status, 400);

  const unknown = await h.get('/voice/status?guild_id=someone-else');
  assert.equal(unknown.status, 404);
  assert.equal(unknown.body.error, 'unknown_guild');
  await h.close();
});

test('an error raised before a guild resolves carries the contract shape', async () => {
  // `{error, request_id, state}` on every error, always. Before a guild is
  // known there is no snapshot to attach, so `state` is null rather than
  // another guild's — nora's client reads these three keys unconditionally.
  const h = await makeHarness();

  const res = await h.post('/music/stop', { guild_id: 'nope' });

  assert.deepEqual(Object.keys(res.body).sort(), ['error', 'request_id', 'state']);
  assert.equal(res.body.state, null);
  await h.close();
});

test('the front door asks to CREATE only on a join', async () => {
  // THE GAP THIS CLOSES: the tests above drive `makeHarness`, whose
  // `controllerFor` ignores the `create` flag entirely — so "a POST for an
  // unknown guild mints nothing" was asserting the harness's behaviour, not
  // production's. Reverting the rule in controller.mjs left them all green.
  //
  // Cores are minted on JOIN only. Minting on any POST would build an
  // unbounded map keyed by whatever a caller puts in `guild_id`, on a control
  // plane that has no auth by design.
  const asked = [];
  const stub = {
    handleParsedPost: (res) => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{}');
    },
  };
  const server = createFrontDoor({
    controllerFor: (guildId, opts = {}) => {
      asked.push({ guildId, create: Boolean(opts.create) });
      return stub;
    },
    health: () => ({ status: 'ok' }),
    log: () => {},
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const send = (p) => fetch(base + p, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ guild_id: GUILD }),
  });

  await send('/voice/join');
  await send('/music/stop');
  await send('/stream');

  assert.deepEqual(asked, [
    { guildId: GUILD, create: true },   // join may create
    { guildId: GUILD, create: false },  // every other verb may not
    { guildId: GUILD, create: false },
  ]);
  await new Promise((r) => server.close(r));
});

test('a COLD BOOT with zero cores still accepts a join', async () => {
  // THE DEADLOCK THIS GUARDS, shipped to production 2026-09-17. The front door
  // asked a CORE whether a route was known, to answer before the guild was
  // known. With no cores yet — a fresh volume, or a restart after every guild
  // exited — that probe returned null and EVERY route 404'd, including the
  // join that would have created the first core. Voice could never be joined
  // for the life of the process, and /health stayed 200 throughout.
  //
  // Route knowledge is static. It must not depend on state that a join creates.
  const cores = new Map();
  const stub = {
    handleParsedPost: (res) => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{}');
    },
  };
  const server = createFrontDoor({
    controllerFor: (guildId, opts = {}) => {
      if (cores.has(guildId)) return stub;
      if (!opts.create) return null;
      cores.set(guildId, stub);
      return stub;
    },
    health: () => ({ status: 'ok', guilds: [...cores.keys()] }),
    log: () => {},
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;

  assert.equal(cores.size, 0, 'precondition: no cores, as on a cold boot');
  const res = await fetch(`${base}/voice/join`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ guild_id: '100000000000000203', channel_id: 'c1' }),
  });

  assert.equal(res.status, 200, 'a join on a cold boot must not 404');
  assert.equal(cores.size, 1);
  await new Promise((r) => server.close(r));
});

test('a guild_id that is not a snowflake is refused before it can name a file', async () => {
  // `guild_id` reaches `path.join(STATE_DIR, `${guildId}.json`)` and mints a
  // core, on a control plane with no auth by design. Production accepted
  // "../../etc/evil" and only Discord's own API refused it, by luck.
  const h = await makeHarness();

  for (const bad of ['../../etc/evil', 'desired_state', '', 'g1', '12345']) {
    const res = await h.post('/voice/join', { guild_id: bad, channel_id: 'c1' });
    assert.equal(res.status, 400, bad);
    assert.equal(res.body.error, 'wrong_guild', bad);
  }
  await h.close();
});

test('a guild the process holds no connection for is 404, not wrong_guild', async () => {
  // Same fact as GET /voice/status's 404: "I have no connection for this
  // guild" is the ordinary state after a restart into idle, and nora reads it
  // as "reconnect me". `wrong_guild` reads as "you are misconfigured".
  const h = await makeHarness();

  const res = await h.post('/music/stop', {
    guild_id: '222222222222222222', generation: 'g', request_id: 'r',
  });

  assert.equal(res.status, 404);
  assert.equal(res.body.error, 'unknown_guild');
  assert.equal(res.body.request_id, 'r', 'the envelope still echoes request_id');
  await h.close();
});
