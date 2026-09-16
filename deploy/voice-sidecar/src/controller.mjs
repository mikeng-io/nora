// HTTP control plane (design/voice-channel/07-audio-owner-controller.md §3):
// the audio-controller is the SOLE owner of the Discord voice connection;
// nora decides everything and drives it through these routes.
//
// Every POST answers { ok: true, request_id, state } and every error
// { error, request_id, state } — nora codes against these shapes, do not
// deviate. Every state-changing command except /voice/join is
// generation-scoped: a stale generation gets 409 generation_conflict and
// nothing happens. Any body guild_id != the configured guild gets 400
// wrong_guild (single-guild hard-bound, §12.5), and /voice/join with a
// channel_id != the ONE configured channel gets 400 not_in_allowed_channel
// (phase-1 immutable channel, §12.2 / B15).
//
// Request dedupe (§3.3): control POSTs may carry request_id; the FIRST
// occurrence reserves an in-flight promise BEFORE the handler runs, so
// concurrent duplicates await the same execution and get the same response;
// once completed, the response is memoized for 5 minutes and a retry
// returns the memo without re-acting. 5xx responses are NOT memoized
// (nothing acted, a retry may re-try).

import http from 'node:http';
import { isHttpError, httpError } from './errors.mjs';
import { createArbiter } from './arbiter.mjs';
import { createLifecycle } from './lifecycle.mjs';
import { createOutbox, realClock } from './outbox.mjs';

const MAX_SPEECH_BYTES = 2 * 1024 * 1024; // decoded b64 budget per /stream
const MAX_BODY_BYTES = 4 * 1024 * 1024; // raw /stream body (b64 ~4/3 of decoded)
const MAX_CONTROL_BYTES = 64 * 1024;
const DEDUPE_TTL_MS = 5 * 60 * 1000;

export function createController({
  guildId,
  channelId, // the ONE configured voice channel (phase 1, immutable)
  lifecycle,
  arbiter,
  outbox,
  getWakeStats = () => ({}),
  pumpInfo = () => ({}),
  clock = realClock,
  dedupeTtlMs = DEDUPE_TTL_MS,
  log = () => {},
}) {
  // request_id -> in-flight { promise } | completed { status, body, expiresAt }
  const dedupe = new Map();

  function snapshot() {
    const a = arbiter.statusSnapshot();
    const v = lifecycle.voice();
    return {
      voice: v === 'destroying' ? 'idle' : v,
      generation: lifecycle.generation(),
      channel_id: lifecycle.channelId(),
      playback_id: a.playback_id,
      player: a.player,
      speech_queue: a.speech_queue,
      crossfade_seconds: 0,
    };
  }

  function respond(res, status, body) {
    res.writeHead(status, { 'content-type': 'application/json' });
    res.end(JSON.stringify(body));
  }

  function respondError(res, status, error, requestId) {
    respond(res, status, { error, request_id: requestId ?? null, state: snapshot() });
  }

  function pruneDedupe() {
    const now = clock.now();
    for (const [id, entry] of dedupe) {
      if (entry.expiresAt !== undefined && entry.expiresAt <= now) dedupe.delete(id);
    }
  }

  // Resolves { ok: true, raw } | { ok: false, reason: 'too_large' | 'read_error' }.
  // An over-limit body keeps draining (discarded) so the client gets a real
  // 413 answer; destroying the request would kill the socket before the
  // response could be written. A transport failure mid-body ('error', or
  // 'close' before 'end') is a read error, NOT an over-limit body — the two
  // used to share one 400 body_too_large (2026-09-16 audit C-5).
  function readBody(req, limit) {
    return new Promise((resolve) => {
      let size = 0;
      let tooLarge = false;
      let settled = false;
      const done = (result) => {
        if (!settled) {
          settled = true;
          resolve(result);
        }
      };
      const chunks = [];
      req.on('data', (chunk) => {
        size += chunk.length;
        if (size > limit) {
          tooLarge = true;
          return; // drain and discard; the answer comes at 'end'
        }
        if (!tooLarge) chunks.push(chunk);
      });
      req.on('end', () =>
        done(tooLarge ? { ok: false, reason: 'too_large' } : { ok: true, raw: Buffer.concat(chunks).toString('utf8') }),
      );
      req.on('error', () => done({ ok: false, reason: 'read_error' }));
      req.on('close', () => done({ ok: false, reason: 'read_error' }));
    });
  }

  function checkGuild(body) {
    if (body.guild_id !== undefined && body.guild_id !== guildId) {
      throw httpError(400, 'wrong_guild');
    }
  }

  function checkGeneration(body) {
    if (!body.generation || body.generation !== lifecycle.generation()) {
      throw httpError(409, 'generation_conflict');
    }
  }

  // Each handler returns nothing; it throws httpError on failure.
  const handlers = {
    'POST /voice/join': async (body) => {
      if (body.guild_id !== guildId) throw httpError(400, 'wrong_guild');
      if (typeof body.channel_id !== 'string' || !body.channel_id) {
        throw httpError(400, 'invalid_body', 'channel_id is required');
      }
      // Phase 1: ONE immutable configured channel (§12.2, B15).
      if (!channelId || body.channel_id !== channelId) {
        throw httpError(400, 'not_in_allowed_channel');
      }
      await lifecycle.join(body.channel_id);
    },
    'POST /voice/exit': async (body) => {
      checkGuild(body);
      checkGeneration(body);
      await lifecycle.exit();
    },
    'POST /music/play': async (body) => {
      checkGuild(body);
      checkGeneration(body);
      if (typeof body.stream_url !== 'string' || !body.stream_url) {
        throw httpError(400, 'invalid_body', 'stream_url is required');
      }
      // Optional source capability (§5 position policy): false marks a
      // live/non-seekable stream; absent leaves the arbiter's default.
      if (body.resumable !== undefined && typeof body.resumable !== 'boolean') {
        throw httpError(400, 'invalid_body', 'resumable must be a boolean when present');
      }
      arbiter.playMusic({
        streamUrl: body.stream_url,
        title: typeof body.title === 'string' ? body.title : null,
        durationSeconds: typeof body.duration_seconds === 'number' ? body.duration_seconds : null,
        requestId: body.request_id ?? null,
        resumable: body.resumable,
      });
    },
    'POST /music/stop': async (body) => {
      checkGuild(body);
      checkGeneration(body);
      arbiter.stopMusic(); // no-op (200) when nothing is playing/suspended
    },
    'POST /music/pause': async (body) => {
      checkGuild(body);
      checkGeneration(body);
      const r = arbiter.pauseMusic();
      if (r.status !== 200) throw httpError(r.status, r.error);
    },
    'POST /music/resume': async (body) => {
      checkGuild(body);
      checkGeneration(body);
      const r = arbiter.resumeMusic();
      if (r.status !== 200) throw httpError(r.status, r.error);
    },
    'POST /music/volume': async (body) => {
      checkGuild(body);
      checkGeneration(body);
      const percent = body.percent;
      if (typeof percent !== 'number' || !Number.isFinite(percent) || percent < 0 || percent > 200) {
        throw httpError(400, 'percent_out_of_range');
      }
      arbiter.setVolume(percent);
    },
    'POST /stream': async (body) => {
      checkGuild(body);
      checkGeneration(body);
      if (typeof body.audio_b64 !== 'string' || !body.audio_b64) {
        throw httpError(400, 'invalid_body', 'audio_b64 (base64) is required');
      }
      const audio = Buffer.from(body.audio_b64, 'base64');
      if (!audio.length) throw httpError(400, 'invalid_body', 'audio_b64 decoded to empty bytes');
      if (audio.length > MAX_SPEECH_BYTES) throw httpError(400, 'audio_too_large');
      const accepted = arbiter.enqueueSpeech({
        audio,
        mediaType: typeof body.media_type === 'string' ? body.media_type : 'audio/mpeg',
        requestId: body.request_id ?? null,
      });
      if (!accepted) throw httpError(503, 'speech_queue_full');
    },
  };

  // Runs a handler and shapes its outcome; never rejects.
  async function execute(route, body, requestId) {
    try {
      await handlers[route](body);
      return { status: 200, body: { ok: true, request_id: requestId, state: snapshot() } };
    } catch (err) {
      if (isHttpError(err)) {
        return { status: err.status, body: { error: err.error, request_id: requestId, state: snapshot() } };
      }
      log('error', { event: 'handler_error', route, error: String(err?.message ?? err) });
      return { status: 500, body: { error: 'internal_error', request_id: requestId, state: snapshot() } };
    }
  }

  async function handlePost(req, res, route) {
    const read = await readBody(req, route === 'POST /stream' ? MAX_BODY_BYTES : MAX_CONTROL_BYTES);
    if (!read.ok) {
      // The design contract is silent on the status for these; the split is
      // 413 for a body that exceeds the limit, 400 for a transport-level
      // read failure (2026-09-16 audit C-5). nora's client maps only 400/409
      // explicitly, so 413 lands on its generic fail-closed unavailable
      // path — no Python-side change needed.
      if (read.reason === 'too_large') {
        respondError(res, 413, 'body_too_large', null);
      } else {
        respondError(res, 400, 'body_read_failed', null);
      }
      return;
    }
    let body;
    try {
      body = JSON.parse(read.raw || '{}');
    } catch {
      respondError(res, 400, 'invalid_json', null);
      return;
    }
    // `null` (and arrays/scalars) parse fine but are not command bodies —
    // reject before any field access.
    if (body === null || typeof body !== 'object' || Array.isArray(body)) {
      respondError(res, 400, 'invalid_body', null);
      return;
    }
    const requestId = typeof body.request_id === 'string' ? body.request_id : null;
    if (!requestId) {
      const result = await execute(route, body, null);
      respond(res, result.status, result.body);
      return;
    }
    pruneDedupe();
    const existing = dedupe.get(requestId);
    if (existing) {
      // Completed memo OR an in-flight duplicate: same response, no
      // re-execution.
      const result = existing.promise ? await existing.promise : existing;
      respond(res, result.status, result.body);
      return;
    }
    const entry = { promise: execute(route, body, requestId) };
    dedupe.set(requestId, entry); // reserved BEFORE the handler settles
    const result = await entry.promise;
    if (result.status < 500) {
      dedupe.set(requestId, { status: result.status, body: result.body, expiresAt: clock.now() + dedupeTtlMs });
    } else {
      dedupe.delete(requestId);
    }
    respond(res, result.status, result.body);
  }

  function handleStatus(res) {
    respond(res, 200, {
      ...snapshot(),
      last_event_seq: outbox.lastEventSeq(),
      outbox_dropped: outbox.dropped(),
      streaming: arbiter.streaming(),
      last_error: arbiter.lastError(),
      state_write_ok: lifecycle.stateWriteOk ? lifecycle.stateWriteOk() : true,
      wake: getWakeStats(),
      pump: pumpInfo(),
    });
  }

  function handleHealth(res) {
    // LIVENESS, deliberately 200 while healthy-but-idle — the Dockerfile
    // HEALTHCHECK and the compose healthcheck curl this.
    const a = arbiter.statusSnapshot();
    respond(res, 200, {
      status: 'ok',
      voice: lifecycle.voice(),
      player: { playing: a.player.status === 'playing', queued: a.speech_queue },
      ...(arbiter.lastError() ? { last_error: arbiter.lastError() } : {}),
      pump: pumpInfo(),
      users: getWakeStats(),
    });
  }

  const server = http.createServer((req, res) => {
    const route = `${req.method} ${req.url}`;
    if (route === 'GET /health') return handleHealth(res);
    if (route === 'GET /voice/status') return handleStatus(res);
    if (handlers[route]) {
      // The whole request-handling promise is caught: no rejection can
      // escape into the event loop as unhandled.
      handlePost(req, res, route).catch((err) => {
        log('error', { event: 'request_error', route, error: String(err?.message ?? err) });
        try {
          respondError(res, 500, 'internal_error', null);
        } catch {
          /* response already gone */
        }
      });
      return;
    }
    respond(res, 404, { error: 'not_found', request_id: null, state: null });
  });

  // A request that dies at the transport level (FIN/reset mid-body, garbage
  // bytes) is a parser failure: it never reaches the router above, and
  // Node's default answer is a bare 400 with no body. Answer with the
  // contract's error shape instead (2026-09-16 audit C-5 — distinct from
  // 413 body_too_large, which only the router can see).
  server.on('clientError', (err, socket) => {
    log('error', { event: 'client_error', error: String(err?.code ?? err?.message ?? err) });
    const body = JSON.stringify({ error: 'body_read_failed', request_id: null, state: null });
    if (socket.writable) {
      socket.end(`HTTP/1.1 400 Bad Request\r\ncontent-type: application/json\r\nconnection: close\r\ncontent-length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
    } else {
      socket.destroy();
    }
  });

  return { server, snapshot };
}

// Wire the whole control plane with its seams. Every Discord/network/process
// touch is an injected dependency so tests never need a token, network, or
// real ffmpeg.
export function createCore({
  guildId,
  channelId,
  joinVoice,
  entersStateFn,
  stateStore,
  spawn,
  createMusicResource,
  createSpeechResource,
  fetchImpl,
  eventUrl,
  clock = realClock,
  getWakeStats,
  pumpInfo,
  log = () => {},
  arbiterOptions = {},
  outboxOptions = {},
}) {
  let lifecycle; // forward reference for the arbiter's generation check
  const outbox = createOutbox({ url: eventUrl, fetchImpl, clock, log, ...outboxOptions });
  const emitEvent = (type, fields) => outbox.push(type, { guild_id: guildId, ...fields });
  const arbiter = createArbiter({
    spawn,
    createMusicResource,
    createSpeechResource,
    clock,
    emitEvent,
    getGeneration: () => lifecycle.generation(),
    log,
    ...arbiterOptions,
  });
  lifecycle = createLifecycle({
    guildId,
    joinVoice,
    entersStateFn,
    stateStore,
    clock,
    emitEvent,
    onJoined: (session) => arbiter.attach(session.player),
    onTeardown: () => arbiter.detach(),
    log,
  });
  const controller = createController({
    guildId,
    channelId,
    lifecycle,
    arbiter,
    outbox,
    getWakeStats,
    pumpInfo,
    clock,
    log,
  });
  return { lifecycle, arbiter, outbox, controller, server: controller.server };
}
