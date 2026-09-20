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
//     slot {stream_url, title, duration_seconds, playback_id, generation,
//     resumable, pauseRequested} remembered as suspended; when the speech
//     queue drains a RESUMABLE track is revived FROM THE CUT POSITION
//     (§5 B7, phase 2): the position is the slot's start offset plus the
//     resource's playbackDuration at the cut, and the revived ffmpeg seeks
//     there (`-ss` before `-i`); position_policy is approximate_seek — the
//     player's consumed-frames count, not a sample-exact cursor. Measured
//     2026-09-17 before this: every TTS reply restarted the song from 0.
//     A NON-resumable (live) source gets NO resume: speech preempts and the
//     stream ENDS — emitted as track-finished so nora treats it as a
//     natural end (§5 "Live/non-seekable streams (radio) get NO resume").
//   - a late ffmpeg start whose slot/generation is no longer current is
//     ignored and the process killed (speech can never be cut by music).
//   - /music/stop during speech invalidates the suspended slot and does NOT
//     touch speech; nothing can resurrect music after stop.
//   - pause intent survives startup and suspension: a pause issued while
//     'starting'/'resuming' is recorded on the slot and applied the moment
//     playback begins; the no-progress stall watch is SUSPENDED while a
//     stream is intentionally paused (a paused pipe produces no bytes, so
//     the watchdog must not read that as a stall).
//
// ffmpeg ownership (§7 B13): at most one LIVE music process. Replacement
// kills with a SIGTERM -> (grace) -> SIGKILL escalation, reaped via 'close';
// a new spawn WAITS for every killed process's close before it starts, so
// the TERM grace never overlaps two live ffmpegs. A 30s no-bytes watch on
// stdout terminates the stream with stream-failed (reason stream-stall) —
// the stall KILLS, it does not just report.
//
// Byte conservation: the resource is connected to stdout at the first bytes
// and the chunk that triggered the transition is REPLAYED into the stream
// (readable-event path consumes nothing; data-event path unshifts) — the
// first PCM chunk is never dropped.
//
// Privacy: stream_urls are signed CDN locators; logs carry them redacted
// (query string + userinfo stripped), and captured ffmpeg stderr is
// URL-scrubbed before it may appear in a log line.

import { randomUUID } from 'node:crypto';
import { realClock } from './outbox.mjs';
import { redactUrl, redactText } from './redact.mjs';

export const FFMPEG_ARGS = (url, startOffsetMs = 0) => [
  '-reconnect', '1',
  '-reconnect_streamed', '1',
  '-reconnect_delay_max', '5',
  // Input-side seek (before -i): ffmpeg range-requests into the CDN object
  // instead of decoding from the top. Omitted at 0 so a fresh play's
  // command line is byte-identical to before.
  ...(startOffsetMs > 0 ? ['-ss', (startOffsetMs / 1000).toFixed(3)] : []),
  '-i', url,
  '-f', 's16le',
  '-ar', '48000',
  '-ac', '2',
  'pipe:1',
];

const STDERR_TAIL_BYTES = 2048;

//: The volume a fresh connection starts music at, as a percent of source.
//: `/music volume` overrides it for the life of that connection.
export const DEFAULT_VOLUME_PERCENT = 25;

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
  // MUSIC STARTS AT 25% (operator, 2026-09-17). Discord voice is loud and a
  // track at full scale buries the people talking over it; `/music volume`
  // raises it per session, and a fresh connection starts quiet again.
  //
  // NOT a restoration of an old default, though it is the number the operator
  // remembers: nothing ever defaulted music to 25%. The pre-controller player
  // started at 1.0 and this arbiter started at 100. The 0.25 in memory is
  // `[speech.tts] duck_gain` — how far music was SCALED DOWN while Nora spoke
  // — and design 07 replaced ducking with a hard cut, so that key is now read
  // by nothing. Same number, different mechanism; this is a new decision.
  let volumePercent = DEFAULT_VOLUME_PERCENT;
  let streaming = false;
  let lastError = null;
  // Close-promises of killed-but-not-yet-reaped ffmpegs; a new spawn waits
  // for these so the TERM grace never overlaps two live processes.
  const pendingReaps = new Set();

  const speechBusy = () => speechActive !== null || speechQueue.length > 0;

  // Where the track is: the offset this slot's ffmpeg was started at plus
  // what the player has consumed from it. A revived slot carries the cut
  // position as its offset, so a second cut compounds correctly.
  function positionMs(s) {
    if (!s) return 0;
    const played = s.resource ? Math.round(s.resource.playbackDuration ?? 0) : 0;
    return (s.startOffsetMs ?? 0) + played;
  }

  function newSlot({ streamUrl, title, durationSeconds, requestId, playbackId, resumable, pauseRequested, startOffsetMs }) {
    return {
      playbackId: playbackId ?? randomUUID(),
      streamUrl,
      title: title ?? null,
      durationSeconds: durationSeconds ?? null,
      requestId: requestId ?? null,
      resumable: resumable !== false, // live/non-seekable sources pass false
      pauseRequested: pauseRequested === true,
      startOffsetMs: startOffsetMs > 0 ? Math.round(startOffsetMs) : 0,
      generation: getGeneration(),
      requestedAt: clock.now(), // request arrival — play_latency_ms baseline
      proc: null,
      closed: null,
      stderrTail: '',
      resource: null,
      playingSince: null,
      stallTimer: null,
      killTimer: null,
      superseded: false,
      failed: false,
      killed: false,
    };
  }

  function suspendStallWatch(s) {
    if (s.stallTimer) {
      clock.clearTimeout(s.stallTimer);
      s.stallTimer = null;
    }
  }

  function killProc(s) {
    suspendStallWatch(s);
    const proc = s.proc;
    if (!proc || s.killed) return;
    s.killed = true;
    if (s.closed) {
      pendingReaps.add(s.closed);
      s.closed.then(() => pendingReaps.delete(s.closed));
    }
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
      ...(s.stderrTail ? { stderr: redactText(s.stderrTail, s.streamUrl) } : {}),
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

  function captureStderr(s) {
    const stderr = s.proc?.stderr;
    if (!stderr || typeof stderr.on !== 'function') return;
    stderr.on('data', (chunk) => {
      // Bounded tail only — diagnostics are captured, never inherited
      // verbatim into the process's own log stream.
      s.stderrTail = (s.stderrTail + chunk.toString('utf8')).slice(-STDERR_TAIL_BYTES);
    });
  }

  function startFfmpeg(s, phase) {
    state = phase; // 'starting' | 'resuming'
    const begin = () => {
      if (s.superseded || slot !== s || !player) return;
      let proc;
      try {
        proc = spawn(FFMPEG_ARGS(s.streamUrl, s.startOffsetMs));
      } catch (err) {
        log('error', { event: 'ffmpeg_spawn_failed', error: String(err?.message ?? err) });
        failStream(s, 'ffmpeg-spawn');
        return;
      }
      s.proc = proc;
      s.closed = new Promise((resolve) => proc.once('close', resolve));
      resetStallWatch(s);
      captureStderr(s);

      let firstBytes = false;
      const onFirstBytes = (chunk) => {
        if (firstBytes) return;
        firstBytes = true;
        // Late music-start guard: the slot, the connection generation, and
        // speech priority are all re-checked at the moment sound would begin.
        if (slot !== s || s.generation !== getGeneration() || !player || speechBusy()) {
          s.superseded = true;
          killProc(s);
          return;
        }
        // Byte conservation: the chunk that triggered this transition (when
        // it arrived via a consuming 'data' event) is replayed into the
        // stream before the resource attaches, so no PCM is ever dropped.
        if (chunk != null && typeof proc.stdout.unshift === 'function') {
          proc.stdout.unshift(chunk);
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
        if (s.pauseRequested) {
          // Pause intent issued during startup (or carried through a
          // suspension) applies the moment playback begins; the stall watch
          // is suspended while the pause is intentional.
          try {
            player.pause();
          } catch {
            /* player gone */
          }
          state = 'paused';
          suspendStallWatch(s);
        }
        player.once('playing', () => {
          if (slot === s && !s.superseded) {
            log('play', {
              playback_id: s.playbackId,
              request_id: s.requestId,
              play_latency_ms: clock.now() - s.requestedAt,
              resumed: phase === 'resuming',
            });
          }
        });
        if (phase === 'resuming') {
          log('music_resumed', {
            playback_id: s.playbackId,
            request_id: s.requestId,
            position_policy: 'approximate_seek',
            position_ms: s.startOffsetMs,
          });
        }
      };
      // 'readable' fires without consuming; 'data' hands us the chunk (which
      // onFirstBytes replays). Whichever the stream emits first wins.
      proc.stdout.once?.('readable', () => onFirstBytes(undefined));
      proc.stdout.on('data', (dataChunk) => {
        if (s.superseded) return;
        // Bytes arriving during an intentional pause are buffered pipe
        // drain, not liveness: re-arming the stall watch here would kill a
        // paused stream as a stream-stall (2026-09-16 audit CN-4).
        if (!s.pauseRequested) resetStallWatch(s);
        onFirstBytes(dataChunk);
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
          ...(code && s.stderrTail ? { stderr: redactText(s.stderrTail, s.streamUrl) } : {}),
        });
        if (s.superseded || slot !== s) return;
        if (code !== 0) failStream(s, `ffmpeg-exit-${code ?? 'null'}`);
        // code 0: natural EOF — the player drains its buffer and idles;
        // onPlayerIdle emits track-finished.
      });
    };
    // Never overlap two live ffmpegs: wait for killed processes to reap
    // before spawning the replacement.
    if (pendingReaps.size) {
      void Promise.all([...pendingReaps]).then(begin, begin);
    } else {
      begin();
    }
  }

  function suspendMusic() {
    if (slot) {
      const s = slot;
      slot = null;
      s.cutAtMs = positionMs(s); // read before the kill: the revive seeks here
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
    if (!s.resumable) {
      // Live/non-seekable source: NO resume — the stream ends here and nora
      // treats it as a natural end (§5 position policy).
      state = 'stopped';
      streaming = false;
      emitMusicEvent('track-finished', s, { duration_ms: positionMs(s) });
      log('track-finished', {
        playback_id: s.playbackId,
        request_id: s.requestId,
        duration_ms: positionMs(s),
        position_policy: 'none',
        reason: 'speech_preempted_live_source',
      });
      return;
    }
    // §5 B7: same playback_id, ffmpeg seeks to the cut position.
    const revived = newSlot({ ...s, startOffsetMs: s.cutAtMs ?? 0 });
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

  // AutoPaused has no other listener anywhere in this file: `.play()`
  // succeeding is not the same as Discord receiving audio, and a silent
  // AutoPause (no subscriber, or a subscription that stopped holding) fires
  // neither 'idle' nor 'error' — ffmpeg keeps decoding, the stall watch
  // keeps seeing bytes, and `state` here stays 'playing' while nothing is
  // audible. Logged at 'error' level so it doesn't hide next to routine
  // buffering/playing/paused transitions.
  //
  // subscribers/playable distinguish the two ways in: 0 subscribers means
  // nothing ever held the subscription (or it was dropped); subscribers > 0
  // with 0 playable means it's held but that connection isn't Ready. Without
  // these two counts the line says only THAT it autopaused, not why.
  function onPlayerStateChange(oldState, newState) {
    if (oldState.status === newState.status) return;
    const fields = {
      from: oldState.status,
      to: newState.status,
      // null during music (no active speech item to attribute the
      // transition to) rather than the previous music playback_id, which
      // suspendMusic() clears before this can fire for a speech preemption.
      playback_id: activeKind === 'music' ? (slot?.playbackId ?? null) : null,
      kind: activeKind,
      request_id: activeKind === 'speech' ? (speechActive?.requestId ?? null) : null,
    };
    if (newState.status === 'autopaused') {
      log('error', {
        event: 'player_autopaused',
        ...fields,
        subscribers: player?.subscribers?.length ?? null,
        playable: player?.playable?.length ?? null,
      });
    } else {
      log('player_state', fields);
    }
  }

  function attach(p) {
    if (player) detach();
    player = p;
    player.on('idle', onPlayerIdle);
    player.on('error', onPlayerError);
    player.on('stateChange', onPlayerStateChange);
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
      player.removeListener('stateChange', onPlayerStateChange);
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

  function playMusic({ streamUrl, title, durationSeconds, requestId, resumable }) {
    if (slot) supersedeSlot(slot);
    slot = null;
    if (suspendedSlot) {
      suspendedSlot.invalidated = true;
      killProc(suspendedSlot);
      suspendedSlot = null;
    }
    const s = newSlot({ streamUrl, title, durationSeconds, requestId, resumable });
    if (speechBusy()) {
      // Speech owns the channel; the new track waits as the suspended slot.
      suspendedSlot = s;
      state = 'suspended';
    } else {
      slot = s;
      startFfmpeg(s, 'starting');
    }
    // Named distinctly from the 'play' logged in onFirstBytes: this one
    // fires the instant the command is accepted, before ffmpeg even spawns.
    // It used to share the 'play' event name with the one that confirms the
    // AudioPlayer reached 'Playing' — collapsing "we tried" and "the decoder
    // produced bytes" into one line only a timestamp diff could tell apart.
    // NOTE: 'Playing' is still not proof Discord received audio — that
    // transition fires independent of subscribers; player_autopaused
    // (onPlayerStateChange, below) is the closer-to-ground-truth signal.
    log('play_requested', {
      playback_id: s.playbackId,
      request_id: s.requestId,
      url: redactUrl(s.streamUrl),
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
    // Speech owns the player: while it is still speaking the pipeline is
    // NOT idle — keep 'suspended' so /voice/status tells the truth
    // (2026-09-16 audit C-4). When speech drains with no suspended slot,
    // advanceSpeech settles the state to 'stopped'.
    if (!speechBusy() && state !== 'stopped') state = 'stopped';
    streaming = false;
    if (had) log('music_stop', {});
    return had;
  }

  function pauseMusic() {
    if (speechBusy()) return { status: 409, error: 'pause_during_speech' };
    if (state === 'playing' && player) {
      player.pause();
      state = 'paused';
      if (slot) {
        slot.pauseRequested = true;
        suspendStallWatch(slot); // an intentional pause is not a stall
      }
      return { status: 200 };
    }
    if (state === 'paused') return { status: 200 };
    if ((state === 'starting' || state === 'resuming') && slot) {
      // ffmpeg has produced no sound yet: record the intent on the slot, it
      // applies the moment playback begins.
      slot.pauseRequested = true;
      return { status: 200 };
    }
    return { status: 409, error: 'no_music_playing' };
  }

  function resumeMusic() {
    if (speechBusy()) return { status: 409, error: 'pause_during_speech' };
    if (state === 'paused' && player) {
      player.unpause();
      state = 'playing';
      if (slot) {
        slot.pauseRequested = false;
        if (slot.proc && !slot.killed) resetStallWatch(slot);
      }
      return { status: 200 };
    }
    if (state === 'playing') return { status: 200 };
    if ((state === 'starting' || state === 'resuming') && slot) {
      slot.pauseRequested = false;
      return { status: 200 };
    }
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
        // Coherent with the resume policy: while suspended this is the
        // position the track was cut at, and the revive seeks there; a live
        // source reports policy 'none'.
        position_ms: positionMs(current),
        position_policy: current ? (current.resumable ? 'approximate_seek' : 'none') : null,
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
