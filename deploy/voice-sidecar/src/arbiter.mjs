// Arbiter (design/voice-channel/07-audio-owner-controller.md §5): ONE audio
// pipeline arbitrating exactly ONE current music slot and ONE bounded
// speech queue. The upcoming music queue lives in nora; this module owns no
// enqueue semantics — /music/play REPLACES the current slot.
//
// States (player.status in the snapshot): stopped(idle) | starting |
// playing | paused | suspended | resuming.
//
// Hard-cut arbitration, no ducking, no overlap ever:
//   - speech preempts music: the music ffmpeg is KILLED (not paused), its
//     slot {stream_url, title, duration_seconds, playback_id, generation}
//     remembered as suspended; when the speech queue drains the track is
//     revived from 0 (phase-1 position policy §5: position_ms is reported
//     from the resource's playbackDuration; resume is approximate_restart,
//     no seek).
//   - a late ffmpeg start whose slot/generation is no longer current is
//     ignored and the process killed (speech can never be cut by music).
//   - /music/stop during speech invalidates the suspended slot and does NOT
//     touch speech; nothing can resurrect music after stop.
//
// ffmpeg ownership (§7 B13): at most one music process; replacement kills
// with a SIGTERM -> (grace) -> SIGKILL escalation, reaped via 'close'.
// A 30s no-bytes watch on stdout terminates the stream with stream-failed
// (reason stream-stall) — the stall KILLS, it does not just report.

import { randomUUID } from 'node:crypto';
import { realClock } from './outbox.mjs';

export const FFMPEG_ARGS = (url) => [
  '-reconnect', '1',
  '-reconnect_streamed', '1',
  '-reconnect_delay_max', '5',
  '-i', url,
  '-f', 's16le',
  '-ar', '48000',
  '-ac', '2',
  'pipe:1',
];

export function createArbiter({
  spawn,
  createMusicResource,
  createSpeechResource,
  clock = realClock,
  emitEvent = () => {},
  getGeneration = () => null,
  log = () => {},
  stallMs = 30_000,
  killGraceMs = 2_000,
  maxSpeechQueue = 4,
}) {
  let player = null;
  let state = 'stopped'; // stopped | starting | playing | paused | suspended | resuming
  let slot = null; // current music slot (owned ffmpeg may be live)
  let suspendedSlot = null; // music hard-stopped by speech, remembered
  let activeKind = null; // 'music' | 'speech' — what the player is playing
  let speechActive = null; // { requestId }
  const speechQueue = [];
  let volumePercent = 100;
  let streaming = false;
  let lastError = null;

  const speechBusy = () => speechActive !== null || speechQueue.length > 0;

  function positionMs(s) {
    return s?.resource ? Math.round(s.resource.playbackDuration ?? 0) : 0;
  }

  function newSlot({ streamUrl, title, durationSeconds, requestId, playbackId }) {
    return {
      playbackId: playbackId ?? randomUUID(),
      streamUrl,
      title: title ?? null,
      durationSeconds: durationSeconds ?? null,
      requestId: requestId ?? null,
      generation: getGeneration(),
      proc: null,
      resource: null,
      playingSince: null,
      stallTimer: null,
      killTimer: null,
      superseded: false,
      failed: false,
      killed: false,
    };
  }

  function killProc(s) {
    if (s.stallTimer) {
      clock.clearTimeout(s.stallTimer);
      s.stallTimer = null;
    }
    const proc = s.proc;
    if (!proc || s.killed) return;
    s.killed = true;
    try {
      proc.kill('SIGTERM');
    } catch {
      /* already dead */
    }
    s.killTimer = clock.setTimeout(() => {
      s.killTimer = null;
      try {
        proc.kill('SIGKILL');
      } catch {
        /* already dead */
      }
    }, killGraceMs);
  }

  // Stop a slot permanently (replace / stop / exit). Never emits
  // track-finished for it.
  function supersedeSlot(s) {
    s.superseded = true;
    killProc(s);
    if (activeKind === 'music' && s.resource) {
      activeKind = null;
      try {
        player?.stop();
      } catch {
        /* player gone */
      }
    }
  }

  function emitMusicEvent(type, s, extra = {}) {
    emitEvent(type, {
      playback_id: s.playbackId,
      generation: s.generation,
      request_id: s.requestId,
      ...extra,
    });
  }

  function failStream(s, reason) {
    if (slot !== s || s.failed) {
      // A failure from a slot that is no longer current must not touch
      // state — but its process must still die.
      killProc(s);
      return;
    }
    s.failed = true;
    s.superseded = true;
    streaming = false;
    lastError = reason;
    killProc(s);
    if (activeKind === 'music') {
      activeKind = null;
      try {
        player?.stop();
      } catch {
        /* player gone */
      }
    }
    emitMusicEvent('stream-failed', s, { reason, duration_ms: positionMs(s) });
    log('stream-failed', {
      playback_id: s.playbackId,
      request_id: s.requestId,
      reason,
      duration_ms: positionMs(s),
    });
    slot = null;
    if (state === 'starting' || state === 'playing' || state === 'resuming' || state === 'paused') {
      state = 'stopped';
    }
  }

  function resetStallWatch(s) {
    if (s.stallTimer) clock.clearTimeout(s.stallTimer);
    s.stallTimer = clock.setTimeout(() => {
      s.stallTimer = null;
      failStream(s, 'stream-stall');
    }, stallMs);
  }

  function startFfmpeg(s, phase) {
    state = phase; // 'starting' | 'resuming'
    let proc;
    try {
      proc = spawn(FFMPEG_ARGS(s.streamUrl));
    } catch (err) {
      log('error', { event: 'ffmpeg_spawn_failed', error: String(err?.message ?? err) });
      failStream(s, 'ffmpeg-spawn');
      return;
    }
    s.proc = proc;
    resetStallWatch(s);

    let firstBytes = false;
    proc.stdout.on('data', (chunk) => {
      if (s.superseded) return;
      resetStallWatch(s);
      if (firstBytes) return;
      firstBytes = true;
      // Late music-start guard: the slot, the connection generation, and
      // speech priority are all re-checked at the moment sound would begin.
      if (slot !== s || s.generation !== getGeneration() || !player || speechBusy()) {
        s.superseded = true;
        killProc(s);
        return;
      }
      let resource;
      try {
        resource = createMusicResource(proc.stdout);
      } catch (err) {
        log('error', { event: 'music_resource_failed', error: String(err?.message ?? err) });
        failStream(s, 'ffmpeg-resource');
        return;
      }
      resource.volume?.setVolume?.(volumePercent / 100);
      s.resource = resource;
      s.playingSince = clock.now();
      activeKind = 'music';
      player.play(resource);
      state = 'playing';
      streaming = true;
      lastError = null;
      player.once('playing', () => {
        if (slot === s && !s.superseded) {
          log('play', {
            playback_id: s.playbackId,
            request_id: s.requestId,
            play_latency_ms: clock.now() - s.playingSince,
            resumed: phase === 'resuming',
          });
        }
      });
      if (phase === 'resuming') {
        log('music_resumed', {
          playback_id: s.playbackId,
          request_id: s.requestId,
          position_policy: 'approximate_restart',
          position_ms: 0,
        });
      }
    });
    proc.on('error', (err) => {
      log('error', { event: 'ffmpeg_error', error: String(err?.message ?? err) });
      failStream(s, 'ffmpeg-error');
    });
    proc.on('close', (code, signal) => {
      if (s.killTimer) {
        clock.clearTimeout(s.killTimer);
        s.killTimer = null;
      }
      log('ffmpeg_exit', {
        playback_id: s.playbackId,
        request_id: s.requestId,
        code,
        signal: signal ?? null,
      });
      if (s.superseded || slot !== s) return;
      if (code !== 0) failStream(s, `ffmpeg-exit-${code ?? 'null'}`);
      // code 0: natural EOF — the player drains its buffer and idles;
      // onPlayerIdle emits track-finished.
    });
  }

  function suspendMusic() {
    if (slot) {
      const s = slot;
      slot = null;
      s.superseded = true; // kills the current ffmpeg; the slot is revived later
      killProc(s);
      if (activeKind === 'music') {
        activeKind = null;
        try {
          player?.stop();
        } catch {
          /* player gone */
        }
      }
      suspendedSlot = s;
    }
    state = 'suspended';
  }

  function reviveSuspended() {
    const s = suspendedSlot;
    suspendedSlot = null;
    if (!s || s.invalidated) {
      state = 'stopped';
      return;
    }
    // Phase-1 position policy: restart from 0, same playback_id.
    const revived = newSlot(s);
    slot = revived;
    startFfmpeg(revived, 'resuming');
  }

  function advanceSpeech() {
    const item = speechQueue.shift();
    if (item && player) {
      speechActive = item;
      activeKind = 'speech';
      try {
        player.play(createSpeechResource(item.audio, item.mediaType));
      } catch (err) {
        log('error', {
          event: 'speech_play_failed',
          request_id: item.requestId,
          error: String(err?.message ?? err),
        });
        speechActive = null;
        activeKind = null;
        advanceSpeech();
      }
      return;
    }
    if (suspendedSlot) reviveSuspended();
    else if (state === 'suspended') state = 'stopped';
  }

  function onPlayerIdle() {
    if (activeKind === 'music') {
      const s = slot;
      activeKind = null;
      // Natural end ONLY: replace/stop/suspend mark the slot superseded
      // before stopping the player, so they never reach this branch.
      if (s && !s.superseded && !s.failed && state === 'playing') {
        const durationMs = positionMs(s) || Math.max(0, clock.now() - (s.playingSince ?? clock.now()));
        slot = null;
        state = 'stopped';
        streaming = false;
        emitMusicEvent('track-finished', s, { duration_ms: durationMs });
        log('track-finished', {
          playback_id: s.playbackId,
          request_id: s.requestId,
          duration_ms: durationMs,
        });
      }
      return;
    }
    if (activeKind === 'speech') {
      const item = speechActive;
      speechActive = null;
      activeKind = null;
      if (item) {
        emitEvent('speech-finished', { request_id: item.requestId ?? null });
        log('speech-finished', { request_id: item.requestId ?? null });
      }
      advanceSpeech();
    }
  }

  function onPlayerError(err) {
    log('error', { event: 'player_error', error: String(err?.message ?? err) });
    if (activeKind === 'music' && slot && !slot.superseded) {
      failStream(slot, 'player-error');
      return;
    }
    if (activeKind === 'speech') {
      speechActive = null;
      activeKind = null;
      advanceSpeech();
    }
  }

  function attach(p) {
    if (player) detach();
    player = p;
    player.on('idle', onPlayerIdle);
    player.on('error', onPlayerError);
  }

  // Terminal teardown (exit / voice-death): everything stops, no events.
  function detach() {
    if (slot) supersedeSlot(slot);
    slot = null;
    if (suspendedSlot) {
      suspendedSlot.invalidated = true;
      killProc(suspendedSlot);
      suspendedSlot = null;
    }
    speechQueue.length = 0;
    speechActive = null;
    activeKind = null;
    if (player) {
      player.removeListener('idle', onPlayerIdle);
      player.removeListener('error', onPlayerError);
      try {
        player.stop();
      } catch {
        /* already idle */
      }
    }
    player = null;
    state = 'stopped';
    streaming = false;
  }

  // --- commands (generation already validated by the controller) ---------

  function playMusic({ streamUrl, title, durationSeconds, requestId }) {
    if (slot) supersedeSlot(slot);
    slot = null;
    if (suspendedSlot) {
      suspendedSlot.invalidated = true;
      killProc(suspendedSlot);
      suspendedSlot = null;
    }
    const s = newSlot({ streamUrl, title, durationSeconds, requestId });
    if (speechBusy()) {
      // Speech owns the channel; the new track waits as the suspended slot.
      suspendedSlot = s;
      state = 'suspended';
    } else {
      slot = s;
      startFfmpeg(s, 'starting');
    }
    log('play', {
      playback_id: s.playbackId,
      request_id: s.requestId,
      url: s.streamUrl,
      deferred: speechBusy(),
    });
    return s.playbackId;
  }

  // Returns true when music was actually stopped (false = no-op).
  function stopMusic() {
    let had = false;
    if (slot) {
      supersedeSlot(slot);
      slot = null;
      had = true;
    }
    if (suspendedSlot) {
      // Stop during speech: invalidate the remembered resume, do NOT touch
      // speech itself.
      suspendedSlot.invalidated = true;
      killProc(suspendedSlot);
      suspendedSlot = null;
      had = true;
    }
    if (state !== 'stopped') state = 'stopped';
    streaming = false;
    if (had) log('music_stop', {});
    return had;
  }

  function pauseMusic() {
    if (speechBusy()) return { status: 409, error: 'pause_during_speech' };
    if (state === 'playing' && player) {
      player.pause();
      state = 'paused';
      return { status: 200 };
    }
    if (state === 'paused') return { status: 200 };
    return { status: 409, error: 'no_music_playing' };
  }

  function resumeMusic() {
    if (speechBusy()) return { status: 409, error: 'pause_during_speech' };
    if (state === 'paused' && player) {
      player.unpause();
      state = 'playing';
      return { status: 200 };
    }
    if (state === 'playing') return { status: 200 };
    return { status: 409, error: 'no_music_playing' };
  }

  function setVolume(percent) {
    volumePercent = percent;
    slot?.resource?.volume?.setVolume?.(percent / 100);
  }

  // Returns false when the bounded speech queue is full (<=1 active +
  // <=maxSpeechQueue queued).
  function enqueueSpeech({ audio, mediaType, requestId }) {
    if (!player) return false;
    if (speechActive && speechQueue.length >= maxSpeechQueue) return false;
    if (!speechBusy() && (state === 'playing' || state === 'starting' || state === 'resuming' || state === 'paused')) {
      suspendMusic();
    }
    speechQueue.push({ audio, mediaType, requestId: requestId ?? null });
    if (!speechActive) advanceSpeech();
    return true;
  }

  function statusSnapshot() {
    const current = slot ?? suspendedSlot;
    return {
      playback_id: current?.playbackId ?? null,
      player: {
        status: state === 'stopped' ? 'idle' : state,
        position_ms: positionMs(slot),
        current: current ? { title: current.title, url: current.streamUrl } : null,
      },
      speech_queue: speechQueue.length + (speechActive ? 1 : 0),
    };
  }

  return {
    attach,
    detach,
    playMusic,
    stopMusic,
    pauseMusic,
    resumeMusic,
    setVolume,
    enqueueSpeech,
    statusSnapshot,
    streaming: () => streaming,
    lastError: () => lastError,
    state: () => state,
  };
}
