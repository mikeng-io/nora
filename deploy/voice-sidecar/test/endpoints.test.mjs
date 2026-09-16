// Endpoint contract tests (design/voice-channel/07 §3): response shapes,
// generation scoping, single-guild binding, replace-play, stop-vs-exit,
// pause-during-speech, volume bounds, speech bounds, status/health,
// request_id dedupe, voice-death and channel-emptied events.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { makeHarness, playTrack, tick, until, GUILD, CHANNEL } from './helpers.mjs';

const PINNED_SNAPSHOT_KEYS = [
  'voice',
  'generation',
  'channel_id',
  'playback_id',
  'player',
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

test('join is idempotent on the configured channel and 400 not_in_allowed_channel on any other', async () => {
  const h = await makeHarness();
  const first = await h.join();
  const again = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL });
  assert.equal(again.status, 200);
  assert.equal(again.body.state.generation, first.generation);
  assert.equal(h.joinCalls.length, 1); // no second join happened

  // Phase 1: ONE immutable configured channel (§12.2, B15) — any other
  // channel is rejected before the lifecycle is consulted.
  const elsewhere = await h.post('/voice/join', { guild_id: GUILD, channel_id: 'c2' });
  assert.equal(elsewhere.status, 400);
  assert.equal(elsewhere.body.error, 'not_in_allowed_channel');
  assert.equal(h.joinCalls.length, 1);
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
  const idle = await h.get('/voice/status');
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
  const playing = await h.get('/voice/status');
  assert.equal(playing.body.streaming, true);
  assert.equal(playing.body.player.status, 'playing');
  assert.equal(playing.body.playback_id, t.playbackId);
  assert.deepEqual(playing.body.player.current, { title: 'A', url: 'https://cdn.test/a' });

  t.proc.close(1); // abnormal exit -> stream-failed surfaces in status
  await tick();
  await tick();
  const failed = await h.get('/voice/status');
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
  assert.equal(health.body.voice, 'idle');

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
  await tick();

  const status = await h.get('/voice/status');
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
  const status = await h.get('/voice/status');
  assert.equal(status.body.voice, 'idle');
  assert.equal(h.delivered.filter((e) => e.type === 'voice-death').length, 1);
  void generation;
  await h.close();
});

test('channel-emptied fires when the human count drops to zero', async () => {
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
  const emptied = h.delivered.filter((e) => e.type === 'channel-emptied');
  assert.equal(emptied.length, 1);
  assert.equal(emptied[0].generation, generation);
  assert.equal(emptied[0].channel_id, CHANNEL);
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
  let releaseJoin;
  const joinGate = new Promise((resolve) => {
    releaseJoin = resolve;
  });
  const h = await makeHarness({ joinGate: () => joinGate });
  const body = { guild_id: GUILD, channel_id: CHANNEL, request_id: 'req-concurrent' };
  const p1 = h.post('/voice/join', body);
  const p2 = h.post('/voice/join', body);
  await tick();
  releaseJoin();
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.status, 200);
  assert.equal(r2.status, 200);
  assert.equal(h.joinCalls.length, 1); // the duplicate awaited the in-flight execution
  assert.deepEqual(r2.body, r1.body);
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
  const raw = await fetch(`${h.core.server.address().address === '127.0.0.1' ? 'http://127.0.0.1' : ''}:${h.core.server.address().port}/music/stop`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: 'null',
  });
  assert.equal(raw.status, 400);
  const parsed = await raw.json();
  assert.equal(parsed.error, 'invalid_body');

  const list = await fetch(`http://127.0.0.1:${h.core.server.address().port}/music/stop`, {
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
