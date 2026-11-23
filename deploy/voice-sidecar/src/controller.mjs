// HTTP control plane (design/voice-channel/audio.md; the `voice` contract,
// design/service-api/contracts.md «Voice»): the audio-controller is the SOLE
// owner of the Discord voice connection; nora decides everything and drives it
// through `POST /v1/voice/operations`, reads it through `POST /v1/voice/queries`
// and reads an operation back through `GET /v1/voice/operations/{operation_id}`.
//
// Every answer on those routes is an envelope written by contract.mjs, except
// the refusals that carry none (400, 401, 404, 408, 409, 413, 422). Every
// connection operation except `join` is generation-scoped: a stale generation
// is `rejected` with `generation_conflict` and the current generation in the
// payload's `state`, and nothing happens.
//
// `join` admits ANY channel in a configured guild (design/voice-channel/
// audio.md «Connection lifecycle»). One connection per guild is Discord's own
// limit: joining a second channel while connected is `rejected` with
// `connected_elsewhere`.
//
// Every route requires the shared service bearer (NORA_BRIDGE_TOKEN). The check
// runs before any body is read, and a missing or wrong bearer is 401.
//
// The operation ledger (ledger.mjs) is process-wide and lives at the front
// door: the same `operation_id` with the same kind and effect is answered from
// it without running again; with another kind or effect it is 409.

import http from 'node:http';
import { isHttpError, httpError } from './errors.mjs';
import { createArbiter } from './arbiter.mjs';
import { createLifecycle } from './lifecycle.mjs';
import { createOutbox, realClock } from './outbox.mjs';
import { bearerMatches } from './auth.mjs';
import { operationEnvelope, readEnvelope, sendEnvelope, sendJson, sendRefusal, traceOf } from './contract.mjs';
import { createLedger, fingerprint } from './ledger.mjs';

const MAX_SPEECH_BYTES = 2 * 1024 * 1024; // decoded b64 budget per `speak`
// Raw operations body: every kind shares the route, so the cap on arrival is
// speech's (b64 is ~4/3 of decoded); a non-speak body over the control bound
// is refused once parsed.
// ponytail: a request may buffer up to 4 MiB before its kind is known, so the
// read buffers are at most 4 MiB × the 64-connection listener limit, as speech's are.
const MAX_BODY_BYTES = 4 * 1024 * 1024;
// A query body (on arrival), and every operation but `speak` (once parsed).
const MAX_CONTROL_BYTES = 64 * 1024;
// An operation_id is the ledger key: a short ASCII word, nothing that could be
// a path, a header or a log-injection payload.
const OPERATION_ID_RE = /^[A-Za-z0-9._:-]{1,64}$/;
// A Discord snowflake: `guild_id` names a state file and mints a core.
const SNOWFLAKE_RE = /^[0-9]{15,25}$/;
// Whole-request read deadline (body included): a stalled upload is cut.
const BODY_TIMEOUT_MS = 15 * 1000;
const MAX_CONNECTIONS = 64;

/** The `voice` operation kinds, closed (nora/domain/service_api_voice.py). */
export const VOICE_KINDS = Object.freeze([
  'join', 'leave', 'play', 'stop', 'pause', 'resume', 'set_volume', 'speak', 'report',
]);

// Resolves { ok: true, raw, bytes } | { ok: false, reason: 'too_large' | 'timeout' | 'read_error' }.
// An over-limit body is answered at the moment it crosses the limit, without
// reading the rest: the front door writes 413 and closes the socket
// (`closeUnread`). A body that is still arriving at the deadline is 'timeout'
// (408). A transport failure mid-body ('error', or 'close' before 'end') is a
// read error, not an over-limit body.
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
    req.on('end', () => done({ ok: true, raw: Buffer.concat(chunks).toString('utf8'), bytes: size }));
    req.on('error', () => done({ ok: false, reason: 'read_error' }));
    req.on('close', () => done({ ok: false, reason: 'read_error' }));
  });
}

// Answers, then closes WITHOUT reading the rest of the request. Used for every
// refusal that can leave a body unread (401, 408, 413): draining would let a
// caller stream unbounded bytes into a listener that holds them only to discard them.
function closeUnread(req, res, status, error) {
  req.pause();
  res.once('finish', () => req.socket.destroySoon());
  sendJson(res, status, { error }, { connection: 'close' });
}

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

// What ffmpeg may be handed: an http(s) URL with no whitespace (leading and
// trailing included, which URL parsing would trim) and no control characters.
const UNSAFE_CHARS = /[\s\u0000-\u001f\u007f-\u009f]/u;
function isStreamUrl(value) {
  if (typeof value !== 'string' || !value || UNSAFE_CHARS.test(value)) return false;
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

export function createController({
  guildId,
  lifecycle,
  arbiter,
  outbox,
  getWakeStats = () => ({}),
  pumpInfo = () => ({}),
  log = () => {},
}) {
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

  function checkGuild(effect) {
    if (effect.guild_id !== undefined && effect.guild_id !== guildId) {
      throw httpError(400, 'wrong_guild');
    }
  }

  function checkGeneration(effect) {
    if (!effect.generation || effect.generation !== lifecycle.generation()) {
      throw httpError(409, 'generation_conflict');
    }
  }

  // Each handler returns nothing; it throws httpError on failure.
  const handlers = {
    join: async (effect) => {
      if (effect.guild_id !== guildId) throw httpError(400, 'wrong_guild');
      if (typeof effect.channel_id !== 'string' || !effect.channel_id) {
        throw httpError(400, 'invalid_body', 'channel_id is required');
      }
      // Any channel in this guild (design/voice-channel/audio.md «Connection
      // lifecycle»). The guild check above is the whole admission; nora's own
      // join gate has already required the caller to be a member physically
      // sitting in the channel it is naming.
      await lifecycle.join(effect.channel_id);
    },
    leave: async (effect) => {
      checkGuild(effect);
      checkGeneration(effect);
      await lifecycle.exit();
    },
    play: async (effect, operationId) => {
      checkGuild(effect);
      checkGeneration(effect);
      if (!isStreamUrl(effect.stream_url)) {
        throw httpError(400, 'invalid_body', 'stream_url must be an http(s) URL');
      }
      // Optional source capability (design/voice-channel/audio.md «Speech and
      // music arbitration»): false marks a live/non-seekable stream; absent
      // or null leaves the arbiter's default.
      if (effect.resumable != null && typeof effect.resumable !== 'boolean') {
        throw httpError(400, 'invalid_body', 'resumable must be a boolean when present');
      }
      const playbackId = arbiter.playMusic({
        streamUrl: effect.stream_url,
        title: typeof effect.title === 'string' ? effect.title : null,
        durationSeconds: typeof effect.duration_seconds === 'number' ? effect.duration_seconds : null,
        requestId: operationId,
        resumable: effect.resumable ?? undefined,
      });
      // A start that failed at once (the arbiter has already reported
      // stream-failed and cleared the slot) is not a playback: never answer
      // `complete` without the one this play created.
      if (arbiter.statusSnapshot().playback_id !== playbackId) throw httpError(503, 'stream_failed');
    },
    stop: async (effect) => {
      checkGuild(effect);
      checkGeneration(effect);
      arbiter.stopMusic(); // complete, a no-op, when nothing is playing/suspended
    },
    pause: async (effect) => {
      checkGuild(effect);
      checkGeneration(effect);
      const r = arbiter.pauseMusic();
      if (r.status !== 200) throw httpError(r.status, r.error);
    },
    resume: async (effect) => {
      checkGuild(effect);
      checkGeneration(effect);
      const r = arbiter.resumeMusic();
      if (r.status !== 200) throw httpError(r.status, r.error);
    },
    set_volume: async (effect) => {
      checkGuild(effect);
      checkGeneration(effect);
      const percent = effect.percent;
      if (typeof percent !== 'number' || !Number.isFinite(percent) || percent < 0 || percent > 200) {
        throw httpError(400, 'percent_out_of_range');
      }
      arbiter.setVolume(percent);
    },
    speak: async (effect, operationId) => {
      checkGuild(effect);
      checkGeneration(effect);
      if (typeof effect.audio_b64 !== 'string' || !effect.audio_b64) {
        throw httpError(400, 'invalid_body', 'audio_b64 (base64) is required');
      }
      const audio = Buffer.from(effect.audio_b64, 'base64');
      if (!audio.length) throw httpError(400, 'invalid_body', 'audio_b64 decoded to empty bytes');
      if (audio.length > MAX_SPEECH_BYTES) throw httpError(400, 'audio_too_large');
      const accepted = arbiter.enqueueSpeech({
        audio,
        mediaType: typeof effect.media_type === 'string' ? effect.media_type : 'audio/mpeg',
        requestId: operationId,
      });
      if (!accepted) throw httpError(503, 'speech_queue_full');
    },
  };

  // Runs a connection operation and classifies it («Outcomes on the
  // controller»); never rejects. A 400 is a refusal with no envelope; every
  // other refusal is an outcome carrying the current snapshot.
  async function execute(kind, effect, operationId) {
    try {
      await handlers[kind](effect, operationId);
      return { outcome: 'complete', state: snapshot() };
    } catch (err) {
      if (isHttpError(err)) {
        if (err.status === 400) return { refusal: { status: 400, error: err.error } };
        return {
          outcome: err.status === 503 ? 'unavailable' : 'rejected',
          state: snapshot(),
          // The kind again, never the detail: a join's carries upstream text.
          error: { kind: err.error, message: err.error },
        };
      }
      log('error', { event: 'handler_error', kind, error: String(err?.message ?? err) });
      return {
        outcome: 'uncertain',
        state: snapshot(),
        error: { kind: 'internal_error', message: 'controller operation failed' },
      };
    }
  }

  // The status query's `ok` payload.
  function status() {
    return {
      ...snapshot(),
      last_event_seq: outbox.lastEventSeq(),
      outbox_dropped: outbox.dropped(),
      streaming: arbiter.streaming(),
      last_error: arbiter.lastError(),
      state_write_ok: lifecycle.stateWriteOk ? lifecycle.stateWriteOk() : true,
      wake: getWakeStats(),
      pump: pumpInfo(),
    };
  }

  // NO SERVER HERE. One process owns several controllers (one per guild)
  // behind ONE port, so the socket and the ledger belong to the front door.
  return { snapshot, execute, status };
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

// The ONE socket, in front of N per-guild controllers. It owns everything that
// must happen BEFORE a guild is known (reading the body is how the guild
// becomes known at all) and the process-wide operation ledger.
//
// The route table is static and also does the routing: route knowledge never
// depends on state a join creates (a cold boot must accept its first join).
const ROUTES = [
  ['POST', '/v1/voice/operations', 'operation', 'submit'],
  ['GET', '/v1/voice/operations/{operation_id}', 'read', 'readOperation'],
  ['POST', '/v1/voice/queries', 'read', 'query'],
  ['GET', '/health', 'probe', 'health'],
  ['GET', '/ready', 'probe', 'ready'],
].map(([method, path, family, name]) => ({
  method, path, family, name,
  pattern: new RegExp(`^${path.replace(/\{[a-z_]+\}/g, '([^/]+)')}$`),
}));

/** The routes this controller serves, as `METHOD path` with the contract's parameter names. */
export const SERVED_ROUTES = Object.freeze(ROUTES.map((r) => `${r.method} ${r.path}`));

const INTERNAL = Object.freeze({ kind: 'internal_error', message: 'controller operation failed' });

export function createFrontDoor({
  controllerFor,
  health,
  // Whether the control plane can serve (`GET /ready`).
  ready = () => true,
  log = () => {},
  authToken,
  authorizeGuild = async () => true,
  clock = realClock,
  ledgerOptions = {},
  bodyTimeoutMs = BODY_TIMEOUT_MS,
  maxConnections = MAX_CONNECTIONS,
}) {
  // Refuses to exist without a token: every route here moves audio or holds
  // a voice connection, and the bridges already refuse to start unauthenticated.
  if (!authToken) throw new Error('createFrontDoor needs an authToken (NORA_BRIDGE_TOKEN)');
  const ledger = createLedger({ clock, ...ledgerOptions });

  // Reads, parses and shape-checks a JSON object body, answering the refusal
  // itself; null when it did.
  async function readJson(req, res, limit) {
    const read = await readBody(req, limit, bodyTimeoutMs);
    if (!read.ok) {
      // Over-limit and timed-out requests close without reading any more.
      if (read.reason === 'too_large') closeUnread(req, res, 413, 'body_too_large');
      else if (read.reason === 'timeout') closeUnread(req, res, 408, 'body_timeout');
      else sendRefusal(res, 400, 'body_read_failed');
      return null;
    }
    let body;
    try {
      body = JSON.parse(read.raw || '{}');
    } catch {
      sendRefusal(res, 400, 'invalid_json');
      return null;
    }
    if (!isObject(body)) {
      sendRefusal(res, 400, 'invalid_body');
      return null;
    }
    return { body, bytes: read.bytes };
  }

  // An operation's outcome before envelope parts are stamped.
  async function run(kind, effect, operationId) {
    if (kind === 'report') {
      return { outcome: 'unsupported', error: { kind: 'unsupported', message: 'the controller takes no reports' } };
    }
    if (!await authorizeGuild(effect.guild_id)) {
      return { outcome: 'denied', error: { kind: 'forbidden_guild', message: 'forbidden_guild' } };
    }
    // `create` ONLY on a join: every other kind addresses a connection that
    // must already exist, so minting for them would grow an unbounded map
    // keyed by whatever a caller put in `guild_id`.
    const controller = controllerFor(effect.guild_id, { create: kind === 'join' });
    if (!controller) {
      // The ordinary state after a restart into idle: nora reads it as
      // "reconnect me".
      return { outcome: 'rejected', error: { kind: 'unknown_guild', message: 'no connection for this guild' } };
    }
    return controller.execute(kind, effect, operationId);
  }

  const answer = (res, a) => (a.refusal
    ? sendRefusal(res, a.refusal.status, a.refusal.error)
    : sendEnvelope(res, a.envelope));

  const handlers = {
    async submit(req, res, trace) {
      const read = await readJson(req, res, MAX_BODY_BYTES);
      if (!read) return;
      const { body } = read;
      // Only `speak` carries audio; every other body keeps the control bound,
      // as sent and as parsed (so the strings a snapshot echoes stay within it).
      if (body.kind !== 'speak' && (read.bytes > MAX_CONTROL_BYTES
        || Buffer.byteLength(JSON.stringify(body)) > MAX_CONTROL_BYTES)) {
        return sendRefusal(res, 413, 'body_too_large');
      }
      const { operation_id: id, kind, effect } = body;
      if (!(typeof id === 'string' && OPERATION_ID_RE.test(id))) {
        return sendRefusal(res, 400, 'invalid_operation_id');
      }
      if (!VOICE_KINDS.includes(kind)) return sendRefusal(res, 422, 'unknown_kind');
      if (!isObject(effect)) return sendRefusal(res, 400, 'invalid_body');
      if (kind !== 'report' && !(typeof effect.guild_id === 'string' && SNOWFLAKE_RE.test(effect.guild_id))) {
        return sendRefusal(res, 400, 'wrong_guild');
      }
      const print = fingerprint(kind, effect);
      const held = ledger.get(id);
      if (held) {
        if (held.fingerprint !== print) return sendRefusal(res, 409, 'operation_id_reused');
        return answer(res, held.promise ? await held.promise : { envelope: held.envelope });
      }
      const target = typeof effect.guild_id === 'string' ? effect.guild_id : '';
      const startedAtMs = clock.now();
      const stamp = (result) => operationEnvelope(trace, { ...result, target, startedAtMs, settledAtMs: clock.now() });
      let settle;
      const promise = new Promise((resolve) => { settle = resolve; });
      if (!ledger.reserve(id, print, promise, operationEnvelope(trace, { outcome: 'pending', target, startedAtMs }))) {
        // Every slot is an operation in flight: refuse, do not queue behind them.
        return sendEnvelope(res, stamp({ outcome: 'unavailable',
          error: { kind: 'dedupe_full', message: 'too many operations in flight' } }));
      }
      let result;
      try {
        result = await run(kind, effect, id);
      } catch (err) {
        log('error', { event: 'request_error', kind, error: String(err?.message ?? err) });
        result = { outcome: 'uncertain', error: INTERNAL };
      }
      const answered = result.refusal ? result : { envelope: stamp(result) };
      if (answered.envelope) ledger.settle(id, print, answered.envelope);
      else ledger.release(id);
      settle(answered);
      return answer(res, answered);
    },

    async readOperation(req, res, trace, [rawId]) {
      let id;
      try {
        id = decodeURIComponent(rawId);
      } catch {
        return sendRefusal(res, 400, 'malformed path parameter');
      }
      const held = ledger.get(id);
      const record = held ? (held.envelope ?? held.pending) : null;
      return sendEnvelope(res, readEnvelope(trace, {
        outcome: record ? 'ok' : 'nothing_found', payload: record, observedAtMs: clock.now(),
      }));
    },

    async query(req, res, trace) {
      const got = await readJson(req, res, MAX_CONTROL_BYTES);
      if (!got) return;
      const { body } = got;
      if (body.kind !== 'status') return sendRefusal(res, 422, 'unknown_kind');
      const guildId = isObject(body.params) ? body.params.guild_id : undefined;
      if (typeof guildId !== 'string' || !guildId) return sendRefusal(res, 400, 'guild_id is required');
      const read = (outcome, payload = null) => sendEnvelope(res, readEnvelope(trace, {
        outcome, payload, observedAtMs: clock.now(),
      }));
      // Every core and configured guild is a snowflake, so anything else
      // resolves to nothing; it never reaches admission's Discord lookup.
      if (!SNOWFLAKE_RE.test(guildId)) return read('nothing_found');
      // A guild outside admission reads exactly as one with no connection.
      if (!await authorizeGuild(guildId)) return read('nothing_found');
      const controller = controllerFor(guildId, { create: false });
      return controller ? read('ok', controller.status()) : read('nothing_found');
    },

    // Process liveness: 200 while healthy-but-idle (the HEALTHCHECK curls it).
    health: (req, res) => sendJson(res, 200, health()),
    ready: (req, res) => {
      const ok = Boolean(ready());
      return sendJson(res, ok ? 200 : 503, { ready: ok });
    },
  };

  const server = http.createServer((req, res) => {
    // Before the route, before any body is read: an unauthenticated caller
    // gets the same 401 whatever it asked for, and holds no socket open.
    if (!bearerMatches(req.headers.authorization, authToken)) {
      return closeUnread(req, res, 401, 'unauthorized');
    }
    const path = String(req.url).split('?')[0];
    let route = null;
    let match = null;
    for (const r of ROUTES) {
      match = req.method === r.method && r.pattern.exec(path);
      if (match) {
        route = r;
        break;
      }
    }
    if (!route) return sendRefusal(res, 404, 'not_found');
    const trace = traceOf(req);
    (async () => handlers[route.name](req, res, trace, match.slice(1)))().catch((err) => {
      log('error', { event: 'request_error', route: `${route.method} ${route.path}`, error: String(err?.message ?? err) });
      try {
        if (res.headersSent) return res.end();
        if (route.family === 'operation') return sendEnvelope(res, operationEnvelope(trace, { outcome: 'uncertain', error: INTERNAL }));
        if (route.family === 'read') {
          return sendEnvelope(res, readEnvelope(trace, { outcome: 'unavailable', healthy: false, observedAtMs: clock.now() }));
        }
        return sendJson(res, 503, { ready: false });
      } catch {
        /* response already gone */
      }
    });
  });

  // A request that dies at the transport level (FIN/reset mid-body, garbage
  // bytes) never reaches the router above, and Node's default answer is a
  // bare 400 with no body. Answer with the refusal shape instead.
  server.on('clientError', (err, socket) => {
    log('error', { event: 'client_error', error: String(err?.code ?? err?.message ?? err) });
    const body = JSON.stringify({ error: 'body_read_failed' });
    if (socket.writable) {
      socket.end(`HTTP/1.1 400 Bad Request\r\ncontent-type: application/json\r\nconnection: close\r\ncontent-length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
    } else {
      socket.destroy();
    }
  });
  server.on('close', () => ledger.dispose());

  // The socket count is capped for this listener. The body deadline is in
  // readBody; Node's own request timeout does not cover a body being read.
  server.maxConnections = maxConnections;
  server.ledger = ledger;

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
  // No CHANNEL_ID here (design/voice-channel/audio.md «Connection lifecycle», reversed 2026-09-17): the control plane admits
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
  // Extra headers on every event POST (the shared bearer Nora's event route requires).
  eventHeaders = {},
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
  const outbox = createOutbox({ url: eventUrl, headers: eventHeaders, fetchImpl, clock, log, ...outboxOptions });
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
