// HTTP control plane (design/voice-channel/07-audio-owner-controller.md §3):
// the audio-controller is the SOLE owner of the Discord voice connection;
// nora decides everything and drives it through these routes.
//
// Every POST answers { ok: true, request_id, state } and every error
// { error, request_id, state } — nora codes against these shapes, do not
// deviate. Every state-changing command except /voice/join is
// generation-scoped: a stale generation gets 409 generation_conflict and
// nothing happens. Any body guild_id != the configured guild gets 400
// wrong_guild (single-guild hard-bound, §12.5).
//
// Request dedupe (§3.3): control POSTs may carry request_id; responses are
// memoized for 5 minutes and a retry returns the memo without re-acting.
// 5xx responses are NOT memoized (nothing acted, a retry may re-try).

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
  lifecycle,
  arbiter,
  outbox,
  getWakeStats = () => ({}),
  pumpInfo = () => ({}),
  clock = realClock,
  dedupeTtlMs = DEDUPE_TTL_MS,
  log = () => {},
}) {
  const dedupe = new Map(); // request_id -> { status, body, expiresAt }

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
      if (entry.expiresAt <= now) dedupe.delete(id);
    }
  }

  function readBody(req, limit) {
    return new Promise((resolve) => {
      let size = 0;
      const chunks = [];
      req.on('data', (chunk) => {
        size += chunk.length;
        if (size > limit) {
          req.destroy();
          resolve(null);
          return;
        }
        chunks.push(chunk);
      });
      req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
      req.on('error', () => resolve(null));
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
      arbiter.playMusic({
        streamUrl: body.stream_url,
        title: typeof body.title === 'string' ? body.title : null,
        durationSeconds: typeof body.duration_seconds === 'number' ? body.duration_seconds : null,
        requestId: body.request_id ?? null,
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

  async function handlePost(req, res, route) {
    const raw = await readBody(req, route === 'POST /stream' ? MAX_BODY_BYTES : MAX_CONTROL_BYTES);
    if (raw === null) {
      respondError(res, 400, 'body_too_large', null);
      return;
    }
    let body;
    try {
      body = JSON.parse(raw || '{}');
    } catch {
      respondError(res, 400, 'invalid_json', null);
      return;
    }
    const requestId = typeof body.request_id === 'string' ? body.request_id : null;
    if (requestId) {
      pruneDedupe();
      const hit = dedupe.get(requestId);
      if (hit) {
        respond(res, hit.status, hit.body);
        return;
      }
    }
    let status = 200;
    let payload;
    try {
      await handlers[route](body);
      payload = { ok: true, request_id: requestId, state: snapshot() };
    } catch (err) {
      if (isHttpError(err)) {
        status = err.status;
        payload = { error: err.error, request_id: requestId, state: snapshot() };
      } else {
        status = 500;
        payload = { error: 'internal_error', request_id: requestId, state: snapshot() };
        log('error', { event: 'handler_error', route, error: String(err?.message ?? err) });
      }
    }
    if (requestId && status < 500) {
      dedupe.set(requestId, { status, body: payload, expiresAt: clock.now() + dedupeTtlMs });
    }
    respond(res, status, payload);
  }

  function handleStatus(res) {
    respond(res, 200, {
      ...snapshot(),
      last_event_seq: outbox.lastEventSeq(),
      outbox_dropped: outbox.dropped(),
      streaming: arbiter.streaming(),
      last_error: arbiter.lastError(),
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
    if (handlers[route]) return void handlePost(req, res, route);
    respond(res, 404, { error: 'not_found', request_id: null, state: null });
  });

  return { server, snapshot };
}

// Wire the whole control plane with its seams. Every Discord/network/process
// touch is an injected dependency so tests never need a token, network, or
// real ffmpeg.
export function createCore({
  guildId,
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
