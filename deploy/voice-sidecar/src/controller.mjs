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
// /voice/join admits ANY channel in that guild (§12.2, reversed 2026-09-17).
// Phase 1 pinned one immutable CHANNEL_ID; a real server's voice channels are
// not a fixed list (Zone 42 has 10+ and gains more), so the channel half of
// the binding could not be kept true and the guild half — which the
// controller already enforces — is the one that was doing the work. The
// machinery was always here: lifecycle.join() has taken a target channel from
// the start, and desired_state has persisted `channel_id` from the start.
// CHANNEL_ID is now only the FIRST-BOOT channel (see bootChannel).
//
// One connection per guild is Discord's own limit, not a policy: joining a
// second channel while connected is still 409 connected_elsewhere.
//
// Every route requires the shared service bearer (NORA_BRIDGE_TOKEN). The check
// runs before any body is read, and a missing or wrong bearer is 401.
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
import { bearerMatches } from './auth.mjs';

const MAX_SPEECH_BYTES = 2 * 1024 * 1024; // decoded b64 budget per /stream
const MAX_BODY_BYTES = 4 * 1024 * 1024; // raw /stream body (b64 ~4/3 of decoded)
const MAX_CONTROL_BYTES = 64 * 1024;
const DEDUPE_TTL_MS = 5 * 60 * 1000;
// Dedupe budget, shared by in-flight and completed entries. A caller that
// never stops sending fresh request_ids must not grow this map without bound.
const DEDUPE_MAX_ENTRIES = 256;
const DEDUPE_MAX_BYTES = 512 * 1024;
const DEDUPE_SWEEP_MS = 30 * 1000;
// A request_id is a caller's correlation token: a short ASCII word, nothing
// that could be a path, a header or a log-injection payload.
const REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,64}$/;
// Whole-request read deadline (body included): a stalled upload is cut.
const BODY_TIMEOUT_MS = 15 * 1000;
const MAX_CONNECTIONS = 64;

// Resolves { ok: true, raw } | { ok: false, reason: 'too_large' | 'timeout' | 'read_error' }.
// An over-limit body is answered at the moment it crosses the limit, without
// reading the rest: the front door writes 413 and closes the socket
// (`closeUnread`). A body that is still arriving at the deadline is 'timeout'
// (408). A transport failure mid-body ('error', or 'close' before 'end') is a
// read error, NOT an over-limit body — the two used to share one 400
// body_too_large (2026-09-16 audit C-5).
function readBody(req, limit, timeoutMs) {
  return new Promise((resolve) => {
    let size = 0;
    let settled = false;
    const done = (result) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(result);
      }
    };
    // Explicit rather than Node's requestTimeout: that one does not fire while
    // a body is being consumed (measured on Node 26), so a slow-drip upload
    // would hold its socket until the peer gave up.
    const timer = setTimeout(() => done({ ok: false, reason: 'timeout' }), timeoutMs);
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (settled) return;
      if (size > limit) {
        req.pause();
        return done({ ok: false, reason: 'too_large' });
      }
      chunks.push(chunk);
    });
    req.on('end', () => done({ ok: true, raw: Buffer.concat(chunks).toString('utf8') }));
    req.on('error', () => done({ ok: false, reason: 'read_error' }));
    req.on('close', () => done({ ok: false, reason: 'read_error' }));
  });
}

// Answers, then closes WITHOUT reading the rest of the request. Used for every
// refusal that can leave a body unread (401, 413): draining would let a caller
// stream unbounded bytes into a listener that holds them only to discard them.
function closeUnread(req, res, status, error) {
  res.writeHead(status, { 'content-type': 'application/json', connection: 'close' });
  req.pause();
  res.once('finish', () => req.socket.destroySoon());
  res.end(JSON.stringify({ error, request_id: null, state: null }));
}

//: The control-plane routes, as a fact about the PROTOCOL rather than about
//: any one guild's controller. The front door has to answer "is this a route
//: we serve" BEFORE it knows the guild, and asking a core for that was a
//: cold-boot deadlock: with no core yet, every route — including the join
//: that would create the first one — answered 404. Voice could never be
//: joined for the life of the process.
export const CONTROL_ROUTES = new Set([
  'POST /voice/join',
  'POST /voice/exit',
  'POST /music/play',
  'POST /music/stop',
  'POST /music/pause',
  'POST /music/resume',
  'POST /music/volume',
  'POST /stream',
]);

export function createController({
  guildId,
  lifecycle,
  arbiter,
  outbox,
  getWakeStats = () => ({}),
  pumpInfo = () => ({}),
  clock = realClock,
  dedupeTtlMs = DEDUPE_TTL_MS,
  dedupeMaxEntries = DEDUPE_MAX_ENTRIES,
  dedupeMaxBytes = DEDUPE_MAX_BYTES,
  dedupeSweepMs = DEDUPE_SWEEP_MS,
  log = () => {},
}) {
  // request_id -> in-flight { promise, bytes } | completed { status, body, expiresAt, bytes }
  // Bounded by dedupeMaxEntries and dedupeMaxBytes, counting both kinds. Only
  // completed entries are evictable: an in-flight entry is the promise a
  // duplicate is waiting on.
  const dedupe = new Map();
  let dedupeBytes = 0;

  function snapshot() {
    const a = arbiter.statusSnapshot();
    const v = lifecycle.voice();
    return {
      voice: v === 'destroying' ? 'idle' : v,
      generation: lifecycle.generation(),
      channel_id: lifecycle.channelId(),
      playback_id: a.playback_id,
      player: a.player,
      // Ground truth for "is sound actually moving", alongside
      // `player.position_ms`: see the arbiter's snapshot for why status
      // alone cannot answer it.
      autopaused: a.autopaused,
      subscribers: a.subscribers,
      playable: a.playable,
      pending_reaps: a.pending_reaps,
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

  function forget(id) {
    const entry = dedupe.get(id);
    if (!entry) return;
    dedupe.delete(id);
    dedupeBytes -= entry.bytes;
  }

  function pruneDedupe() {
    const now = clock.now();
    for (const [id, entry] of dedupe) {
      if (entry.expiresAt !== undefined && entry.expiresAt <= now) forget(id);
    }
  }

  // Room for one more entry of `bytes`, evicting COMPLETED entries oldest-first
  // only as far as needed. False when everything held is in flight: the caller
  // is refused (503 dedupe_full) rather than the map growing past its budget.
  function makeRoom(bytes) {
    const fits = () => dedupe.size < dedupeMaxEntries && dedupeBytes + bytes <= dedupeMaxBytes;
    for (const [id, entry] of dedupe) {
      if (fits()) return true;
      if (entry.expiresAt !== undefined) forget(id);
    }
    return fits();
  }

  // Expiry does not wait for traffic: a completed memo past its TTL is dropped
  // by this timer, so an idle controller releases what an attacker stored.
  // unref() so the timer alone never keeps the process alive.
  setInterval(pruneDedupe, dedupeSweepMs).unref();

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
      // Any channel in this guild (§12.2, reversed 2026-09-17). The guild
      // check above is the whole admission; nora's own join gate has already
      // required the caller to be a member physically sitting in the channel
      // it is naming.
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

  // The body arrives ALREADY read, parsed and shape-checked: reading it is
  // what tells the front door which guild's controller to hand it to, so it
  // cannot happen in here any more (multi-guild, 2026-09-17). Everything from
  // the dedupe down is unchanged and still per-guild — the dedupe map, the
  // 5-minute memo and `snapshot()` all belong to ONE connection.
  async function handleParsedPost(res, route, body) {
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
    // Only the id and promise live in the dedupe map, never the upload.
    // Reserve a small fixed allowance for the promise/entry metadata; completed
    // entries below account for their actual serialized outcome instead.
    const retainedBytes = Buffer.byteLength(requestId) + 128;
    if (!makeRoom(retainedBytes)) {
      // Every slot is an in-flight request: refuse, do not queue behind them.
      respond(res, 503, { error: 'dedupe_full', request_id: requestId, state: snapshot() });
      return;
    }
    const entry = { promise: execute(route, body, requestId), bytes: retainedBytes };
    dedupe.set(requestId, entry); // reserved BEFORE the handler settles
    dedupeBytes += entry.bytes;
    const result = await entry.promise;
    forget(requestId);
    if (result.status < 500) {
      // The memo holds a whole response body, so it can overflow the byte
      // budget even though the in-flight slot it replaces was tiny. It makes
      // its own room; if none can be made (everything else is in flight) the
      // answer is served but not remembered, so a retry re-executes — the one
      // place this map trades replay protection for its bound.
      const memoBytes = Buffer.byteLength(requestId) + Buffer.byteLength(JSON.stringify(result.body));
      if (makeRoom(memoBytes)) {
        dedupe.set(requestId, { status: result.status, body: result.body, expiresAt: clock.now() + dedupeTtlMs, bytes: memoBytes });
        dedupeBytes += memoBytes;
      }
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

  // NO SERVER HERE any more. One process owns several controllers (one per
  // guild) behind ONE port, so the socket belongs to the front door below.
  // What a controller exposes is the handling of an already-parsed request
  // for ITS guild.
  return {
    snapshot,
    handleParsedPost,
    handleStatus,
    handleHealth,
    knowsRoute: (route) => Boolean(handlers[route]),
    dedupeSize: () => dedupe.size,
    dedupeBytes: () => dedupeBytes,
  };
}

// The per-guild core map and the mint-vs-reuse decision every request makes
// before it can be handled (#345). Pulled out of service.mjs so it is
// testable without discord.js: this module already has none of those seams,
// only the ones `makeCore` itself needs.
//
// A join that fails leaves its core in `voice: error` with no `session`
// (lifecycle.join's catch sets `voice = 'error'` before `session` is ever
// assigned — see lifecycle.mjs). Left in the map, that core answers every
// later `/health` as a guild Nora holds a connection for and never did — and
// re-joining that guild was ALREADY harmless without any of this: lifecycle's
// own `join()` treats `voice === 'error'` like idle and retries on the SAME
// core (it falls through the `for(;;)` guard because 'error' matches none of
// 'connected'/joinPromise/destroyPromise). So the actual fix for #345's
// symptom is `discardIfFailed`, called the moment a join fails — not this
// module's mint-time prune alone, which only ever runs when someone mints
// AGAIN for the same guild, and does nothing for the guild that fails once
// and is never retried (the exact case #345 reports live).
//
// `discardIfFailed(guildId, core)` takes an IDENTITY check first: only
// deletes `cores.get(guildId)` when it is STILL exactly `core`. The call
// arrives from inside the join's own async continuation (lifecycle.mjs),
// which resumes on its own schedule — by the time it runs, a concurrent
// second request may already have pruned-and-reminted (via `controllerFor`
// below) or otherwise replaced this guild's entry, and the identity check is
// what stops this call from deleting THAT (possibly live) core out from
// under it.
export function createCoreRegistry({ makeCore, canCreate = () => true, log = () => {} }) {
  const cores = new Map();

  function mint(guildId) {
    const core = makeCore(guildId);
    cores.set(guildId, core);
    log('core_created', { guild_id: guildId });
    return core;
  }

  // The condition (`voice === 'error' && !session`) is what a core in this
  // state always looks like today — lifecycle.join's catch is the only place
  // that ever sets `voice = 'error'`, and it does so before `session` is ever
  // assigned. Checked anyway, alongside identity, rather than trusting the
  // caller: it mirrors lifecycle.teardown's own guard for "nothing here worth
  // tearing down", read the other way round for "nothing here worth keeping".
  function discardIfFailed(guildId, core) {
    if (cores.get(guildId) !== core) return false; // superseded already
    if (core.lifecycle.voice() !== 'error' || core.lifecycle.session()) return false;
    cores.delete(guildId);
    log('core_pruned', { guild_id: guildId, reason: 'join_failed' });
    return true;
  }

  function controllerFor(guildId, { create = false, any = false } = {}) {
    // `any` answers "is this even a route we serve", before a guild is
    // known. Route knowledge is identical across cores, so any core answers
    // it; when there are none yet, a throwaway is not built — the front door
    // falls through to 404, which is the right answer for an unknown route
    // anyway.
    if (any) {
      const first = cores.values().next();
      return first.done ? null : first.value.controller;
    }
    // Backstop, not the fix: `discardIfFailed` (above) is what actually
    // clears a stale error core, the moment its join fails. This only
    // matters if some future core ever reaches `voice: error` without going
    // through that hook — keeps a caller from being handed a core no join
    // could ever succeed on.
    let existing = cores.get(guildId);
    if (existing && create && existing.lifecycle.voice() === 'error' && !existing.lifecycle.session()) {
      cores.delete(guildId);
      log('core_pruned', { guild_id: guildId, reason: 'stale_error_core_on_mint' });
      existing = undefined;
    }
    if (existing) return existing.controller;
    // Minted ONLY on a join (or a caller-supplied `canCreate`, e.g. a
    // configured bot token). Every other verb addresses a connection that
    // must already exist, so creating one for them would make an unbounded
    // map keyed by whatever a caller put in `guild_id`.
    if (!create || !canCreate()) return null;
    return mint(guildId).controller;
  }

  return {
    mint,
    controllerFor,
    discardIfFailed,
    guildIds: () => [...cores.keys()],
    entries: () => [...cores.entries()],
    values: () => [...cores.values()],
  };
}

// The forward-ref + `onJoinFailed` wiring every `makeCore` needs to use
// `discardIfFailed` (#345): `onJoinFailed` has to be passed into `createCore`
// before the core it will act on exists, but it can never fire before this
// function returns (a join cannot fail before its own `createCore` call has
// finished), so the forward reference is always resolved by the time the
// hook is actually called.
//
// Pulled out of service.mjs and imported by BOTH it and
// test/core_registry.test.mjs's end-to-end test — a re-review of the first
// cut of this fix found that test built its OWN copy of this wiring, so a
// service.mjs regression (service.mjs dropping the forward-ref assignment,
// or forgetting to pass `onJoinFailed` at all) would leave `discardIfFailed`
// silently returning `false` in production while every test stayed green.
// Sharing this function closes that gap: the test now exercises the exact
// code service.mjs runs, and service.mjs's own remaining surface — calling
// this function with the right `registry`/`guildId` — is small enough that a
// mistake there would be a one-line, hard-to-miss diff.
export function buildCore(registry, guildId, coreOptions) {
  let thisCore;
  const core = createCore({
    ...coreOptions,
    guildId,
    onJoinFailed: () => registry.discardIfFailed(guildId, thisCore),
  });
  thisCore = core;
  return core;
}

// The ONE socket, in front of N per-guild controllers (multi-guild,
// 2026-09-17). It owns everything that must happen BEFORE a guild is known —
// reading the body is how the guild becomes known at all — and nothing else.
//
// Body limits, the 413/400 split and the clientError shape are moved here
// VERBATIM from the controller that used to own the socket; every one of them
// is pinned by `endpoints.test.mjs`, which is what made the move safe.
export function createFrontDoor({
  controllerFor,
  health,
  log = () => {},
  authToken,
  authorizeGuild = async () => true,
  bodyTimeoutMs = BODY_TIMEOUT_MS,
  maxConnections = MAX_CONNECTIONS,
}) {
  // Refuses to exist without a token: every route here moves audio or holds
  // a voice connection, and the bridges already refuse to start unauthenticated.
  if (!authToken) throw new Error('createFrontDoor needs an authToken (NORA_BRIDGE_TOKEN)');

  function respond(res, status, body) {
    res.writeHead(status, { 'content-type': 'application/json' });
    res.end(JSON.stringify(body));
  }

  // No controller resolved, so there is no `state` to attach — the shape
  // stays the contract's three keys with `state: null`. The `request_id` IS
  // echoed whenever the body was parsed far enough to carry one: the module
  // header promises `{error, request_id, state}` and dropping the echo made a
  // retried command indistinguishable from a fresh one in a log.
  function bareError(res, status, error, requestId = null) {
    respond(res, status, { error, request_id: requestId, state: null });
  }

  function requestIdOf(body) {
    return typeof body?.request_id === 'string' ? body.request_id : null;
  }

  const server = http.createServer((req, res) => {
    // Before the route, before any body is read: an unauthenticated caller
    // gets the same 401 whatever it asked for, and holds no socket open.
    if (!bearerMatches(req.headers.authorization, authToken)) {
      return closeUnread(req, res, 401, 'unauthorized');
    }
    const [path, query] = String(req.url).split('?');
    const route = `${req.method} ${path}`;
    if (route === 'GET /health') return respond(res, 200, health());
    (async () => {
    if (route === 'GET /voice/status') {
      // A GET carries no body, so the guild rides the query string. Absent is
      // an error rather than a default: defaulting would report one guild's
      // playback as another's the moment a second one connected.
      const guildId = new URLSearchParams(query ?? '').get('guild_id');
      if (!guildId) return bareError(res, 400, 'guild_id is required');
      if (!await authorizeGuild(guildId)) return bareError(res, 403, 'forbidden_guild');
      const c = controllerFor(guildId, { create: false });
      if (!c) return bareError(res, 404, 'unknown_guild');
      return c.handleStatus(res);
    }
      // Static: see CONTROL_ROUTES. Asking a core whether it knows the route
      // deadlocked a cold boot.
      if (!CONTROL_ROUTES.has(route)) {
        return respond(res, 404, { error: 'not_found', request_id: null, state: null });
      }
      const read = await readBody(req, route === 'POST /stream' ? MAX_BODY_BYTES : MAX_CONTROL_BYTES, bodyTimeoutMs);
      if (!read.ok) {
        // 413 for over-limit, 408 for a body that did not finish in time, 400
        // for a transport-level read failure (2026-09-16 audit C-5). nora's
        // client maps only 400/409 explicitly, so 408 and 413 land on its
        // generic fail-closed unavailable path. Over-limit and timed-out
        // requests close without reading any more of the body.
        if (read.reason === 'too_large') return closeUnread(req, res, 413, 'body_too_large');
        if (read.reason === 'timeout') return closeUnread(req, res, 408, 'body_timeout');
        return bareError(res, 400, 'body_read_failed');
      }
      let body;
      try {
        body = JSON.parse(read.raw || '{}');
      } catch {
        return bareError(res, 400, 'invalid_json');
      }
      // `null` (and arrays/scalars) parse fine but are not command bodies —
      // reject before any field access.
      if (body === null || typeof body !== 'object' || Array.isArray(body)) {
        return bareError(res, 400, 'invalid_body');
      }
      const guildId = typeof body.guild_id === 'string' ? body.guild_id : null;
      if (!guildId) return bareError(res, 400, 'wrong_guild', requestIdOf(body));
      // A Discord snowflake, and nothing else. `guild_id` reaches
      // `path.join(STATE_DIR, ...)` and mints a core, on the authenticated control plane — so "../../etc/evil" must die here, not at the
      // Discord API by luck.
      if (!/^[0-9]{15,25}$/.test(guildId)) {
        return bareError(res, 400, 'wrong_guild', requestIdOf(body));
      }
      // `request_id` is the dedupe key, so its shape is bounded here, before a
      // dedupe slot can be spent on it. Absent or null is the un-deduped path.
      const rid = body.request_id;
      if (rid !== undefined && rid !== null && !(typeof rid === 'string' && REQUEST_ID_RE.test(rid))) {
        return bareError(res, 400, 'invalid_request_id');
      }
      // `create` ONLY on a join: every other verb addresses a connection that
      // must already exist.
      if (!await authorizeGuild(guildId)) return bareError(res, 403, 'forbidden_guild');
      const controller = controllerFor(guildId, { create: route === 'POST /voice/join' });
      if (!controller) {
        // NOT `wrong_guild`. "I hold no connection for this guild" is the
        // same fact `GET /voice/status` answers 404 `unknown_guild` for, and
        // after a restart into idle it is the ordinary state — nora reads it
        // as "reconnect me", where `wrong_guild` reads as "you are
        // misconfigured".
        return bareError(res, 404, 'unknown_guild', requestIdOf(body));
      }
      return controller.handleParsedPost(res, route, body);
    })().catch((err) => {
      log('error', { event: 'request_error', route, error: String(err?.message ?? err) });
      try {
        bareError(res, 500, 'internal_error');
      } catch {
        /* response already gone */
      }
    });
  });

  // A request that dies at the transport level (FIN/reset mid-body, garbage
  // bytes) never reaches the router above, and Node's default answer is a
  // bare 400 with no body. Answer with the contract's error shape instead.
  server.on('clientError', (err, socket) => {
    log('error', { event: 'client_error', error: String(err?.code ?? err?.message ?? err) });
    const body = JSON.stringify({ error: 'body_read_failed', request_id: null, state: null });
    if (socket.writable) {
      socket.end(`HTTP/1.1 400 Bad Request\r\ncontent-type: application/json\r\nconnection: close\r\ncontent-length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
    } else {
      socket.destroy();
    }
  });

  // The socket count is capped for this listener. The body deadline is in
  // readBody; Node's own request timeout does not cover a body being read.
  server.maxConnections = maxConnections;

  return server;
}

// Wire the whole control plane with its seams. Every Discord/network/process
// touch is an injected dependency so tests never need a token, network, or
// real ffmpeg.
// ONE GUILD's whole audio stack: its connection lifecycle, its playback
// arbiter, its event outbox, its control-plane handlers. Called once per guild
// the process serves (multi-guild, 2026-09-17) — nothing in here is shared or
// keyed by guild, because a core IS a guild.
export function createCore({
  guildId,
  // No CHANNEL_ID here (§12.2, reversed 2026-09-17): the control plane admits
  // any channel in the guild, and a restart's channel comes from this guild's
  // own desired-state file.
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
  // (guildId) => void, fired the moment a join fails (#345) — passed straight
  // through to lifecycle, which is the one place that decides it.
  onJoinFailed = () => {},
  arbiterOptions = {},
  outboxOptions = {},
  controllerOptions = {},
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
    onJoinFailed: () => onJoinFailed(guildId),
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
    ...controllerOptions,
  });
  // No `server`: one process owns several cores behind ONE front door
  // (multi-guild, 2026-09-17), and the socket is `createFrontDoor`'s.
  return { lifecycle, arbiter, outbox, controller };
}
