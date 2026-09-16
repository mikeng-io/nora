// Arbiter state-machine tests (design/voice-channel/07 §5): replace-not-
// enqueue, speech preemption with hard cut + revive-from-0, late-start
// guard, stop/pause rules, natural-end event, stall termination, kill
// escalation, bounded speech queue, remembered volume.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';

import { createArbiter } from '../src/arbiter.mjs';
import {
  createFakeClock,
  createFakePlayer,
  createFakeProc,
  createFakeResource,
  tick,
} from './helpers.mjs';

function makeArbiter(options = {}) {
  const clock = createFakeClock();
  const player = createFakePlayer();
  const procs = [];
  const spawn = (args) => {
    const p = createFakeProc();
    p.args = args;
    procs.push(p);
    return p;
  };
  const events = [];
  let generation = 'gen1';
  const arbiter = createArbiter({
    spawn,
    createMusicResource: (stream) => createFakeResource(stream),
    createSpeechResource: (audio, mediaType) => ({ audio, mediaType, playbackDuration: 0 }),
    clock,
    emitEvent: (type, fields) => events.push({ type, ...fields }),
    getGeneration: () => generation,
    log: () => {},
    ...options,
  });
  arbiter.attach(player);
  const playToPlaying = (url = 'https://cdn.test/a', title = 'A') => {
    const playbackId = arbiter.playMusic({ streamUrl: url, title });
    const proc = procs[procs.length - 1];
    proc.data();
    return { playbackId, proc, resource: player.current };
  };
  return { arbiter, clock, player, procs, events, setGeneration: (g) => (generation = g), playToPlaying };
}

test('replace-play: second play supersedes the first, new playback_id, no track-finished for the old', async () => {
  const { arbiter, player, procs, events, playToPlaying } = makeArbiter();
  const a = playToPlaying('https://cdn.test/a');
  assert.equal(arbiter.state(), 'playing');

  const bId = arbiter.playMusic({ streamUrl: 'https://cdn.test/b', title: 'B' });
  assert.notEqual(bId, a.playbackId);
  assert.deepEqual(a.proc.signals, ['SIGTERM']); // superseded ffmpeg is terminated
  await tick(); // the new spawn waits for the old process to reap (no overlap)
  const bProc = procs[1];
  bProc.data();
  assert.equal(player.current.stream, bProc.stdout);

  player.current.playbackDuration = 12_000;
  player.finish(); // B ends naturally
  await tick();

  const finished = events.filter((e) => e.type === 'track-finished');
  assert.equal(finished.length, 1);
  assert.equal(finished[0].playback_id, bId);
  assert.equal(finished[0].duration_ms, 12_000);
  assert.equal(finished[0].generation, 'gen1');
});

test('replacement never overlaps two live ffmpegs: the new spawn waits for the previous close', async () => {
  const { arbiter, procs, playToPlaying } = makeArbiter();
  playToPlaying('https://cdn.test/a');
  arbiter.playMusic({ streamUrl: 'https://cdn.test/b' });
  // The TERM grace is still open; the replacement must NOT be spawned yet.
  assert.equal(procs.length, 1);
  await tick(); // the killed process closes (fake mirrors SIGTERM death) ...
  await tick(); // ... and only then does the replacement spawn
  assert.equal(procs.length, 2);
});

test('kill escalation: SIGTERM, then SIGKILL after the grace period', () => {
  const { arbiter, clock, playToPlaying } = makeArbiter({ killGraceMs: 2_000 });
  const a = playToPlaying();
  arbiter.playMusic({ streamUrl: 'https://cdn.test/b' });
  assert.deepEqual(a.proc.signals, ['SIGTERM']);
  clock.advance(2_000);
  assert.deepEqual(a.proc.signals, ['SIGTERM', 'SIGKILL']);
});

test('speech during MUSIC-STARTING: the late ffmpeg start cannot kill speech', async () => {
  const { arbiter, player, procs, events, playToPlaying } = makeArbiter();
  arbiter.playMusic({ streamUrl: 'https://cdn.test/a' });
  const startingProc = procs[0];
  assert.equal(arbiter.state(), 'starting');

  const accepted = arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg', requestId: 'r1' });
  assert.equal(accepted, true);
  assert.equal(arbiter.state(), 'suspended');
  assert.deepEqual(startingProc.signals, ['SIGTERM']); // pending ffmpeg killed
  assert.equal(player.played.length, 1); // only speech has played

  // The late start arrives anyway: ignored, must not touch speech.
  startingProc.data();
  assert.equal(player.played.length, 1);
  assert.equal(arbiter.state(), 'suspended');

  player.finish(); // speech ends
  await tick();
  assert.deepEqual(
    events.filter((e) => e.type === 'speech-finished').map((e) => e.request_id),
    ['r1'],
  );
  // Suspended music revives from 0 with a fresh ffmpeg.
  assert.equal(procs.length, 2);
  assert.equal(arbiter.state(), 'resuming');
  procs[1].data();
  assert.equal(arbiter.state(), 'playing');
  assert.equal(player.played.length, 2);
});

test('late start from an old generation is ignored even without speech', async () => {
  const { arbiter, player, procs, setGeneration } = makeArbiter();
  arbiter.playMusic({ streamUrl: 'https://cdn.test/a' });
  const proc = procs[0];
  setGeneration('gen2'); // connection was replaced while ffmpeg was starting
  proc.data();
  assert.equal(player.played.length, 0);
  assert.deepEqual(proc.signals, ['SIGTERM']);
});

test('music/stop during speech invalidates the suspended slot and never resurrects music', async () => {
  const { arbiter, player, procs, clock, playToPlaying } = makeArbiter();
  playToPlaying();
  arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg', requestId: 'r1' });
  assert.equal(arbiter.state(), 'suspended');

  assert.equal(arbiter.stopMusic(), true);
  // Speech still owns the player: the pipeline is NOT idle (audit C-4) —
  // the state settles to 'stopped' only when speech drains.
  assert.equal(arbiter.state(), 'suspended');
  assert.equal(player.state.status, 'playing'); // speech is untouched

  player.finish(); // speech drains
  await tick();
  assert.equal(arbiter.state(), 'stopped');
  assert.equal(procs.length, 1); // no revive spawn
  clock.advance(60_000);
  assert.equal(procs.length, 1); // nothing resurrects later either
  assert.equal(arbiter.state(), 'stopped');
});

test('music/stop during speech never reports the player as idle mid-speech', async () => {
  const { arbiter, player, playToPlaying } = makeArbiter();
  playToPlaying();
  arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg', requestId: 'r1' });
  assert.equal(arbiter.stopMusic(), true);

  // The snapshot /voice/status serves: speech is audibly playing, so the
  // player status must not read idle (2026-09-16 audit C-4).
  assert.equal(arbiter.statusSnapshot().player.status, 'suspended');
  assert.equal(arbiter.statusSnapshot().playback_id, null); // the music slot is gone

  player.finish();
  await tick();
  assert.equal(arbiter.statusSnapshot().player.status, 'idle'); // truthful once speech drained
});

test('pause during speech is rejected; pause/resume cycle works while playing', () => {
  const { arbiter, player, playToPlaying } = makeArbiter();
  playToPlaying();
  arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg' });
  assert.deepEqual(arbiter.pauseMusic(), { status: 409, error: 'pause_during_speech' });
  assert.deepEqual(arbiter.resumeMusic(), { status: 409, error: 'pause_during_speech' });
});

test('pause/resume cycle while playing', () => {
  const { arbiter, player, playToPlaying } = makeArbiter();
  playToPlaying();
  assert.deepEqual(arbiter.pauseMusic(), { status: 200 });
  assert.equal(arbiter.state(), 'paused');
  assert.equal(player.paused, 1);
  assert.deepEqual(arbiter.resumeMusic(), { status: 200 });
  assert.equal(arbiter.state(), 'playing');
  assert.equal(player.unpaused, 1);
  assert.deepEqual(arbiter.stopMusic() && arbiter.pauseMusic(), { status: 409, error: 'no_music_playing' });
});

test('natural track end fires exactly one track-finished with the right playback_id', async () => {
  const { arbiter, player, events, playToPlaying } = makeArbiter();
  const a = playToPlaying();
  player.current.playbackDuration = 42_000;
  player.finish();
  await tick();
  const finished = events.filter((e) => e.type === 'track-finished');
  assert.equal(finished.length, 1);
  assert.equal(finished[0].playback_id, a.playbackId);
  assert.equal(finished[0].duration_ms, 42_000);
  assert.equal(arbiter.state(), 'stopped');
  assert.equal(arbiter.statusSnapshot().playback_id, null);
});

test('30s no-bytes stall terminates the stream with stream-failed (stream-stall)', () => {
  const { arbiter, clock, events, playToPlaying } = makeArbiter({ stallMs: 30_000 });
  const a = playToPlaying();
  assert.equal(arbiter.streaming(), true);
  clock.advance(30_000);
  assert.deepEqual(a.proc.signals, ['SIGTERM']);
  const failed = events.filter((e) => e.type === 'stream-failed');
  assert.equal(failed.length, 1);
  assert.equal(failed[0].reason, 'stream-stall');
  assert.equal(failed[0].playback_id, a.playbackId);
  assert.equal(arbiter.streaming(), false);
  assert.equal(arbiter.lastError(), 'stream-stall');
  assert.equal(arbiter.state(), 'stopped');
});

test('abnormal ffmpeg exit is stream-failed, never track-finished', () => {
  const { arbiter, events, playToPlaying } = makeArbiter();
  const a = playToPlaying();
  a.proc.close(1);
  const failed = events.filter((e) => e.type === 'stream-failed');
  assert.equal(failed.length, 1);
  assert.equal(failed[0].reason, 'ffmpeg-exit-1');
  assert.equal(events.filter((e) => e.type === 'track-finished').length, 0);
});

test('clean ffmpeg close followed by player idle is the natural end', async () => {
  const { arbiter, player, events, playToPlaying } = makeArbiter();
  const a = playToPlaying();
  a.proc.close(0);
  player.finish();
  await tick();
  assert.equal(events.filter((e) => e.type === 'track-finished').length, 1);
  assert.equal(events.filter((e) => e.type === 'stream-failed').length, 0);
});

test('speech queue is bounded at 1 active + 4 queued', () => {
  const { arbiter } = makeArbiter();
  for (let i = 0; i < 5; i += 1) {
    assert.equal(arbiter.enqueueSpeech({ audio: Buffer.from(`s${i}`), mediaType: 'audio/mpeg' }), true);
  }
  assert.equal(arbiter.enqueueSpeech({ audio: Buffer.from('s5'), mediaType: 'audio/mpeg' }), false);
  assert.equal(arbiter.statusSnapshot().speech_queue, 5);
});

test('queued speech drains in order and each finish emits speech-finished', async () => {
  const { arbiter, player, events } = makeArbiter();
  arbiter.enqueueSpeech({ audio: Buffer.from('s1'), mediaType: 'audio/mpeg', requestId: 'r1' });
  arbiter.enqueueSpeech({ audio: Buffer.from('s2'), mediaType: 'audio/mpeg', requestId: 'r2' });
  player.finish();
  await tick();
  player.finish();
  await tick();
  assert.deepEqual(
    events.filter((e) => e.type === 'speech-finished').map((e) => e.request_id),
    ['r1', 'r2'],
  );
});

test('volume applies to the current resource and is remembered for the next', () => {
  const { arbiter, player, playToPlaying } = makeArbiter();
  arbiter.setVolume(50);
  playToPlaying();
  assert.equal(player.current.volume.value, 0.5);
  arbiter.setVolume(150);
  assert.equal(player.current.volume.value, 1.5);
});

test('volume remembered across replace-play', async () => {
  const { arbiter, player, procs, playToPlaying } = makeArbiter();
  playToPlaying();
  arbiter.setVolume(150);
  arbiter.playMusic({ streamUrl: 'https://cdn.test/b' });
  await tick(); // replacement spawn waits for the superseded process to reap
  await tick();
  procs[1].data();
  assert.equal(player.current.volume.value, 1.5);
});

test('detach stops everything and emits nothing', () => {
  const { arbiter, player, events, playToPlaying } = makeArbiter();
  const a = playToPlaying();
  arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg' });
  arbiter.detach();
  assert.deepEqual(a.proc.signals, ['SIGTERM']);
  assert.equal(arbiter.state(), 'stopped');
  assert.equal(arbiter.statusSnapshot().speech_queue, 0);
  assert.equal(events.length, 0);
  assert.equal(player.listenerCount('idle'), 0);
});

test('pause during MUSIC-STARTING is preserved and applies the moment playback begins', () => {
  const { arbiter, player, procs } = makeArbiter();
  arbiter.playMusic({ streamUrl: 'https://cdn.test/a' });
  assert.equal(arbiter.state(), 'starting');

  assert.deepEqual(arbiter.pauseMusic(), { status: 200 }); // no longer lost as 409
  assert.equal(arbiter.state(), 'starting');

  procs[0].data(); // first bytes arrive -> playback begins paused
  assert.equal(player.played.length, 1);
  assert.equal(player.paused, 1);
  assert.equal(arbiter.state(), 'paused');

  assert.deepEqual(arbiter.resumeMusic(), { status: 200 });
  assert.equal(player.unpaused, 1);
  assert.equal(arbiter.state(), 'playing');
});

test('pause intent survives a speech suspension: the revive comes back paused', async () => {
  const { arbiter, player, procs, playToPlaying } = makeArbiter();
  playToPlaying();
  assert.deepEqual(arbiter.pauseMusic(), { status: 200 });
  assert.equal(arbiter.state(), 'paused');

  arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg' });
  assert.equal(arbiter.state(), 'suspended');
  player.finish(); // speech ends -> revive
  await tick();
  await tick();
  assert.equal(procs.length, 2);
  procs[1].data();
  assert.equal(arbiter.state(), 'paused'); // still intentionally paused, not playing
});

test('the no-progress watchdog does not kill an intentionally paused stream', () => {
  const { arbiter, clock, events, playToPlaying } = makeArbiter({ stallMs: 30_000 });
  const a = playToPlaying();
  assert.deepEqual(arbiter.pauseMusic(), { status: 200 });

  // Buffered PCM drains into the paused pipe (ffmpeg is still alive and
  // producing): those bytes are NOT liveness and must not re-arm the watch
  // (2026-09-16 audit CN-4/CN-5 — without the pause guard this chunk
  // restarts the watchdog and the advance below kills the paused stream).
  a.proc.data();

  clock.advance(120_000); // far past the stall window: nothing happens
  assert.deepEqual(a.proc.signals, []);
  assert.equal(arbiter.state(), 'paused');
  assert.equal(arbiter.streaming(), true);
  assert.equal(events.filter((e) => e.type === 'stream-failed').length, 0);

  assert.deepEqual(arbiter.resumeMusic(), { status: 200 }); // watch re-arms on resume
  clock.advance(30_000);
  assert.deepEqual(a.proc.signals, ['SIGTERM']);
  const failed = events.filter((e) => e.type === 'stream-failed');
  assert.equal(failed.length, 1);
  assert.equal(failed[0].reason, 'stream-stall');
});

test('a non-resumable (live) source ends on speech preemption instead of restarting from zero', async () => {
  const { arbiter, player, procs, events, playToPlaying } = makeArbiter();
  const a = playToPlaying('https://cdn.test/live');
  assert.equal(arbiter.state(), 'playing');
  void a;

  // Replace with a live, non-seekable source.
  const liveId = arbiter.playMusic({ streamUrl: 'https://cdn.test/radio', title: 'Radio', resumable: false });
  await tick();
  await tick();
  procs[1].data();
  assert.equal(arbiter.state(), 'playing');

  arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg' });
  assert.equal(arbiter.state(), 'suspended');
  player.finish(); // speech ends
  await tick();
  await tick();

  assert.equal(procs.length, 2); // NO revive spawn
  assert.equal(arbiter.state(), 'stopped');
  const finished = events.filter((e) => e.type === 'track-finished');
  assert.equal(finished.length, 1); // nora treats it as a natural end
  assert.equal(finished[0].playback_id, liveId);
});

test('playback conserves the first PCM chunk (real PassThrough, byte-exact)', async () => {
  const { PassThrough } = await import('node:stream');
  const clock = createFakeClock();
  const player = createFakePlayer();
  const received = [];
  const proc = new EventEmitter();
  proc.stdout = new PassThrough();
  proc.kill = () => true;
  const arbiter = createArbiter({
    spawn: () => proc,
    createMusicResource: (stream) => {
      stream.on('data', (chunk) => received.push(Buffer.from(chunk)));
      return createFakeResource(stream);
    },
    createSpeechResource: (audio, mediaType) => ({ audio, mediaType, playbackDuration: 0 }),
    clock,
    getGeneration: () => 'gen1',
  });
  arbiter.attach(player);
  arbiter.playMusic({ streamUrl: 'https://cdn.test/a' });

  proc.stdout.write(Buffer.from('FIRST'));
  proc.stdout.write(Buffer.from('SECOND'));
  await tick();
  await tick();

  assert.equal(player.played.length, 1);
  assert.equal(Buffer.concat(received).toString(), 'FIRSTSECOND');
});

test("the readable-first path attaches the resource without consuming or unshifting bytes (production ordering)", async () => {
  // 2026-09-16 audit FG-3: a real child stdout has the arbiter's 'readable'
  // listener registered, which pins flowing=false — so 'readable' fires
  // FIRST in production and the data-path unshift is dead code there. The
  // fake proc now mirrors that ordering; this pins what the readable path
  // must do: attach the resource, consume nothing, unshift nothing (every
  // byte stays in the stream for the resource to read).
  const { arbiter, player, procs } = makeArbiter();
  arbiter.playMusic({ streamUrl: 'https://cdn.test/a' });
  const proc = procs[0];
  let unshiftCalls = 0;
  const originalUnshift = proc.stdout.unshift;
  proc.stdout.unshift = (chunk) => {
    unshiftCalls += 1;
    originalUnshift(chunk);
  };

  proc.data();
  assert.equal(player.played.length, 1);
  assert.equal(player.played[0].stream, proc.stdout);
  assert.equal(arbiter.state(), 'playing');
  assert.equal(unshiftCalls, 0); // 'readable' consumed nothing — nothing to replay

  proc.data(); // steady state: 'data' only, watch re-arms, no double-attach
  assert.equal(player.played.length, 1);
  assert.equal(unshiftCalls, 0);
});

test('/voice/status position reporting is coherent with the resume policy', () => {
  const { arbiter, player, playToPlaying } = makeArbiter();
  playToPlaying();
  player.current.playbackDuration = 7_500;
  let snap = arbiter.statusSnapshot();
  assert.equal(snap.player.position_ms, 7_500);
  assert.equal(snap.player.position_policy, 'approximate_restart');

  arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg' });
  snap = arbiter.statusSnapshot();
  assert.equal(snap.player.status, 'suspended');
  assert.equal(snap.player.position_ms, 7_500); // the cut position, not 0
  assert.equal(snap.player.current.title, 'A'); // still names the suspended track
});

test('a suspended live source reports position_policy none', () => {
  const { arbiter, procs } = makeArbiter();
  arbiter.playMusic({ streamUrl: 'https://cdn.test/radio', resumable: false });
  procs[0].data();
  arbiter.enqueueSpeech({ audio: Buffer.from('tts'), mediaType: 'audio/mpeg' });
  const snap = arbiter.statusSnapshot();
  assert.equal(snap.player.position_policy, 'none');
});

test('music/stop during MUSIC-STARTING cancels the pending start: no late playback, no resurrection, no track-finished', async () => {
  const { arbiter, player, procs, events, clock } = makeArbiter();
  arbiter.playMusic({ streamUrl: 'https://cdn.test/a' });
  const startingProc = procs[0];
  assert.equal(arbiter.state(), 'starting');

  assert.equal(arbiter.stopMusic(), true);
  assert.equal(arbiter.state(), 'stopped');
  assert.deepEqual(startingProc.signals, ['SIGTERM']); // pending ffmpeg killed

  startingProc.data(); // the late start arrives anyway: ignored
  assert.equal(player.played.length, 0);
  assert.equal(arbiter.state(), 'stopped');

  clock.advance(60_000);
  assert.equal(procs.length, 1); // nothing resurrects later either
  assert.equal(events.filter((e) => e.type === 'track-finished').length, 0);
});

test('a second speech while MUSIC-RESUMING preempts again: the in-flight revive cannot kill it', async () => {
  const { arbiter, player, procs, events, playToPlaying } = makeArbiter();
  playToPlaying('https://cdn.test/a');
  arbiter.enqueueSpeech({ audio: Buffer.from('one'), mediaType: 'audio/mpeg', requestId: 'r1' });
  assert.equal(arbiter.state(), 'suspended');

  player.finish(); // first speech ends -> the revive begins (ffmpeg respawns)
  await tick();
  assert.equal(arbiter.state(), 'resuming');
  const reviveProc = procs[procs.length - 1];

  arbiter.enqueueSpeech({ audio: Buffer.from('two'), mediaType: 'audio/mpeg', requestId: 'r2' });
  assert.equal(arbiter.state(), 'suspended');
  assert.deepEqual(reviveProc.signals, ['SIGTERM']); // the in-flight revive is killed
  assert.equal(player.played.length, 3); // music, speech one, speech two

  reviveProc.data(); // late revive bytes arrive: must not touch the second speech
  assert.equal(player.played.length, 3);
  assert.equal(arbiter.state(), 'suspended');

  player.finish(); // second speech drains
  await tick();
  assert.deepEqual(
    events.filter((e) => e.type === 'speech-finished').map((e) => e.request_id),
    ['r1', 'r2'],
  );
  // Music revives once more, from 0, on a fresh ffmpeg.
  assert.equal(arbiter.state(), 'resuming');
  const secondRevive = procs[procs.length - 1];
  assert.notEqual(secondRevive, reviveProc);
  secondRevive.data();
  assert.equal(arbiter.state(), 'playing');
  assert.equal(player.played.length, 4);
});
