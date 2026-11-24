// Shared fakes for the audio-controller tests. Every Discord/network/process
// touch in src/ is behind an injected seam; these fakes drive those seams
// synchronously so no test needs a token, a network, or a real ffmpeg.

import { EventEmitter } from 'node:events';
import net from 'node:net';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { after } from 'node:test';

import { randomUUID } from 'node:crypto';

import { STATUS } from '../src/contract.mjs';
import { createCore, createFrontDoor } from '../src/controller.mjs';
import { createStateStore } from '../src/state_store.mjs';

// A REAL snowflake shape: the front door refuses anything else, because
// `guild_id` names a state file and mints a core on an unauthenticated plane.
// 'g1' would have been rejected, which is the point.
export const GUILD = '111111111111111111';
// The shared bearer every test server requires; AUTH is what a legitimate
// caller (nora, the bridges) sends.
export const TOKEN = 'test-bridge-token';
export const AUTH = { authorization: `Bearer ${TOKEN}` };
export const CHANNEL = 'c1';
export const EVENT_URL = 'http://nora.test/v1/voice/operations';

// The hub's answer to a `report`: an envelope with `outcome`, at the
// kernel's status for it unless `status` overrides it.
// The payload the contract gives each outcome of a report's answer.
function defaultHubPayload(outcome) {
  if (outcome === 'complete') return { status: 'ok' };
  if (['rejected', 'unsupported', 'denied', 'incomplete', 'uncertain'].includes(outcome)) {
    return { state: null, error: { kind: outcome, message: outcome } };
  }
  return {};
}

export function hubAnswer(outcome, {
  status = STATUS[outcome], payload = defaultHubPayload(outcome),
} = {}) {
  const envelope = {
    outcome,
    payload,
    when: { state: outcome === 'pending' ? 'started' : 'settled', started_at_ms: null, settled_at_ms: null },
    from: { target: '', platform_ref: null },
    scope: { level: 'installation' },
    trace: '',
  };
  return { status, envelope, json: async () => envelope };
}

export function createFakeClock(start = 1_000_000) {
  let now = start;
  let nextId = 1;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout: (fn, ms) => {
      const id = nextId++;
      timers.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimeout: (id) => {
      timers.delete(id);
    },
    advance: (ms) => {
      const target = now + ms;
      for (;;) {
        let best = null;
        for (const [id, t] of timers) {
          if (t.at <= target && (!best || t.at < best.t.at)) best = { id, t };
        }
        if (!best) break;
        timers.delete(best.id);
        now = best.t.at;
        best.t.fn();
      }
      now = target;
    },
    pending: () => timers.size,
  };
}

// AudioPlayer stand-in: play/pause/unpause/stop + manual finish() for the
// natural end of the current resource. Emits 'playing' on a microtask like
// the real player transitioning states. pause()/unpause() model the real
// AudioPlayer's transition rules (pause only from playing, unpause only
// from paused, both returning whether they acted) and never discard the
// current resource — a paused player keeps its buffered audio (2026-09-16
// audit CN-5).
export function createFakePlayer() {
  const player = new EventEmitter();
  player.state = { status: 'idle' };
  player.played = [];
  player.stopped = 0;
  player.paused = 0;
  player.unpaused = 0;
  player.play = (resource) => {
    player.played.push(resource);
    player.current = resource;
    player.state = { status: 'playing', resource };
    queueMicrotask(() => player.emit('playing'));
    return true;
  };
  player.pause = () => {
    player.paused += 1;
    if (player.state.status !== 'playing') return false;
    player.state = { status: 'paused', resource: player.current };
    return true;
  };
  player.unpause = () => {
    player.unpaused += 1;
    if (player.state.status !== 'paused') return false;
    player.state = { status: 'playing', resource: player.current };
    return true;
  };
  player.stop = () => {
    player.stopped += 1;
    if (player.state.status !== 'idle') {
      player.state = { status: 'idle' };
      player.emit('idle');
    }
    return true;
  };
  // Natural EOF of the current resource.
  player.finish = () => {
    player.state = { status: 'idle' };
    player.emit('idle');
  };
  return player;
}

// ffmpeg child-process stand-in. Like a real child, a killed process dies
// and emits 'close' (on a microtask, so synchronous assertions on signals
// still run first); 'close' is emitted at most once.
//
// The stdout fake models the Readable surface the arbiter actually drives
// (2026-09-16 audit FG-3): a REAL child stdout has a 'readable' listener
// registered by the arbiter, which pins flowing=false so the first arrival
// announces 'readable' (consuming nothing) BEFORE any 'data' event fires;
// unshift() pushes consumed bytes back to the front, and they replay ahead
// of the next data.
export function createFakeProc() {
  const proc = new EventEmitter();
  const stdout = new EventEmitter();
  const held = []; // bytes pushed back via unshift, replayed before new data
  let announced = false;
  stdout.unshift = (chunk) => {
    held.unshift(chunk);
  };
  proc.stdout = stdout;
  proc.signals = [];
  proc.exitCode = null;
  let closed = false;
  const emitClose = (code, signal) => {
    if (closed) return;
    closed = true;
    proc.emit('close', code, signal);
  };
  proc.kill = (signal) => {
    proc.signals.push(signal);
    queueMicrotask(() => emitClose(null, signal));
    return true;
  };
  proc.data = (chunk = Buffer.from('pcm')) => {
    if (!announced) {
      announced = true;
      stdout.emit('readable'); // the first arrival is announced, not consumed
    }
    for (const c of held.splice(0)) stdout.emit('data', c);
    if (chunk != null) stdout.emit('data', chunk);
  };
  proc.close = (code, signal = null) => emitClose(code, signal);
  proc.fail = (err) => proc.emit('error', err);
  return proc;
}

export function createFakeResource(stream) {
  return {
    stream,
    playbackDuration: 0,
    volume: {
      value: 1,
      setVolume(v) {
        this.value = v;
      },
    },
  };
}

export const tick = () => new Promise((resolve) => setImmediate(resolve));

// Poll a condition across ticks (loopback HTTP + real fs need more than one).
// Waits at least `tries` ticks and at least `minMs` of wall time: a real file
// write under a loaded host can outlast any tick count.
export async function until(cond, tries = 200, minMs = 2000) {
  const started = Date.now();
  for (let i = 0; i < tries || Date.now() - started < minMs; i += 1) {
    if (cond()) return true;
    await tick();
  }
  return cond();
}

// Full harness: createCore wired with fakes, served on a loopback ephemeral
// port. Tests drive it over real HTTP plus the fake seams.
export async function makeHarness(options = {}) {
  const clock = options.clock ?? createFakeClock();
  const dir = mkdtempSync(path.join(tmpdir(), 'voc-test-'));
  const statePath = options.statePath ?? path.join(dir, 'desired_state.json');

  const procs = [];
  const spawn = (args) => {
    if (options.spawnError) throw options.spawnError;
    const p = createFakeProc();
    p.args = args;
    procs.push(p);
    return p;
  };

  const delivered = []; // events (report effects) the hub took
  const eventAttempts = []; // every delivery attempt's operation body
  let fetchBehavior = options.fetchBehavior ?? (async () => hubAnswer('complete'));
  const fetchImpl = async (url, opts) => {
    const body = JSON.parse(opts.body);
    eventAttempts.push(body);
    const res = await fetchBehavior(url, opts);
    if (res && res.status >= 200 && res.status < 300) delivered.push(body.effect);
    return res;
  };

  const player = createFakePlayer();
  const connection = new EventEmitter();
  const client = new EventEmitter();
  client.channels = { cache: new Map() };
  const bus = new EventEmitter();
  let leaveCount = 0;
  let pumpCloseCount = 0;
  const pump = () => {};
  pump.close = () => {
    pumpCloseCount += 1;
  };
  pump.stats = () => ({ backlog_dropped: 0, no_listener_dropped: 0 });

  const joinCalls = [];
  const joinVoice = async (channelId) => {
    joinCalls.push(channelId);
    if (options.joinGate) await options.joinGate();
    if (options.joinError) throw options.joinError;
    return {
      connection,
      client,
      player,
      bus,
      pump,
      leave: async () => {
        leaveCount += 1;
        if (options.leaveGate) await options.leaveGate();
      },
    };
  };
  const entersStateCalls = [];
  const entersStateFn = async (conn, status, ms) => {
    entersStateCalls.push({ status, ms });
    if (options.recoveryFails) throw new Error('recovery failed');
  };

  const core = createCore({
    guildId: GUILD,
    joinVoice,
    entersStateFn,
    stateStore: options.stateStore ?? createStateStore({ path: statePath }),
    spawn,
    createMusicResource: (stream) => createFakeResource(stream),
    createSpeechResource: (audio, mediaType) => ({ audio, mediaType, playbackDuration: 0 }),
    fetchImpl,
    eventUrl: EVENT_URL,
    clock,
    getWakeStats: () => ({ u1: { frames: 1, bytes: 3840 } }),
    pumpInfo: () => ({ host: '127.0.0.1', port: 4599, drops: pump.stats() }),
    log: () => {},
    arbiterOptions: options.arbiterOptions ?? {},
    outboxOptions: options.outboxOptions ?? {},
  });

  // The socket and the operation ledger belong to the FRONT DOOR, not to a
  // core: one process serves N guilds behind one port. The harness wires a
  // single-guild front door, so every test below drives the real request
  // path — body limits, the 413/400 split, the ledger — through the code
  // production uses.
  let ready = options.ready ?? true;
  const server = createFrontDoor({
    authToken: TOKEN,
    controllerFor: (guildId) => (guildId === GUILD ? core.controller : null),
    health: () => ({ status: 'ok', guilds: [GUILD] }),
    ready: () => ready,
    ...(options.authorizeGuild ? { authorizeGuild: options.authorizeGuild } : {}),
    clock,
    ledgerOptions: options.ledgerOptions ?? {},
    ...(options.bodyTimeoutMs ? { bodyTimeoutMs: options.bodyTimeoutMs } : {}),
    log: () => {},
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const close = () => new Promise((resolve) => {
    server.close(resolve);
    // Failed tests may leave an active HTTP request open.
    server.closeAllConnections();
  });
  // Runs even when an assertion throws before a test's explicit close().
  after(close);
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = async (p, body, headers = {}) => {
    const res = await fetch(base + p, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...AUTH, ...headers },
      body: JSON.stringify(body ?? {}),
    });
    return { status: res.status, body: await res.json() };
  };
  const get = async (p) => {
    const res = await fetch(base + p, { headers: AUTH });
    return { status: res.status, body: await res.json() };
  };
  // One `voice` operation; a fresh operation_id unless one is given. The
  // answer is flattened for the assertions most tests make.
  const op = async (kind, effect, { operationId = randomUUID(), headers } = {}) => {
    const res = await post('/v1/voice/operations', { operation_id: operationId, kind, effect }, headers);
    return {
      ...res,
      outcome: res.body.outcome,
      state: res.body.payload?.state ?? null,
      error: res.body.payload?.error?.kind ?? res.body.error ?? null,
    };
  };
  // The status query for a guild; `payload` is the status body on `ok`.
  const status = async (guildId = GUILD) => {
    const res = await post('/v1/voice/queries', { kind: 'status', params: { guild_id: guildId } });
    return { ...res, outcome: res.body.outcome, payload: res.body.payload };
  };

  // Join the default channel and return the minted generation.
  const join = async (channelId = CHANNEL) => {
    const res = await op('join', { guild_id: GUILD, channel_id: channelId });
    return { res, generation: res.state.generation };
  };

  return {
    clock,
    core,
    // The socket is the front door's since 2026-09-17; `core.server` is gone.
    server,
    port: server.address().port,
    player,
    connection,
    client,
    bus,
    procs,
    delivered,
    eventAttempts,
    post,
    get,
    op,
    status,
    join,
    ledger: server.ledger,
    setReady: (value) => {
      ready = value;
    },
    joinCalls,
    entersStateCalls,
    statePath,
    setFetchBehavior: (fn) => {
      fetchBehavior = fn;
    },
    leaves: () => leaveCount,
    pumpCloses: () => pumpCloseCount,
    close,
  };
}

// Drive a `play` all the way to PLAYING: the operation, then first ffmpeg bytes.
export async function playTrack(h, { generation, url = 'https://cdn.test/a', title = 'A', operationId } = {}) {
  const res = await h.op('play', { guild_id: GUILD, generation, stream_url: url, title },
    operationId ? { operationId } : {});
  const proc = h.procs[h.procs.length - 1];
  proc.data();
  await tick();
  return { res, proc, playbackId: res.state.playback_id };
}

// Raw HTTP over a socket, for what fetch will not send: a body that stops
// short. Resolves with the status and the parsed body.
export function rawRequest(port, head, body, { end = false } = {}) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port }, () => {
      socket.write(head + body);
      if (end) socket.end();
    });
    let data = '';
    socket.on('data', (chunk) => { data += chunk.toString(); });
    socket.on('close', () => {
      const status = Number(/^HTTP\/1\.1 (\d{3}) /.exec(data)?.[1]);
      // The body may be chunked: the JSON is the one object in it.
      resolve({ status, body: JSON.parse(data.slice(data.indexOf('{'), data.lastIndexOf('}') + 1)) });
    });
    socket.on('error', reject);
  });
}
// Streams `body` in pieces over a raw socket, stopping once an answer
// arrives, so a refusal mid-upload is read rather than lost to a reset.
// Resolves with the status, the parsed body and the elapsed milliseconds.
export function rawStream(port, body, { contentLength = Buffer.byteLength(body), path = '/v1/voice/operations' } = {}) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    let data = '';
    const socket = net.connect({ host: '127.0.0.1', port }, () => {
      socket.write([`POST ${path} HTTP/1.1`, 'host: x', `authorization: Bearer ${TOKEN}`,
        'content-type: application/json', `content-length: ${contentLength}`, 'connection: close', '', ''].join('\r\n'));
      // One piece at a time, paced, so the answer is read before the server
      // closes on what it did not read.
      let at = 0;
      const pump = () => {
        if (at >= body.length || data || socket.destroyed) return;
        const piece = body.slice(at, at + 16 * 1024);
        at += piece.length;
        socket.write(piece, () => setTimeout(pump, 1));
      };
      pump();
    });
    socket.on('data', (chunk) => { data += chunk.toString(); });
    socket.on('close', () => resolve({
      status: Number(/^HTTP\/1\.1 (\d{3}) /.exec(data)?.[1]),
      body: data.includes('{') ? JSON.parse(data.slice(data.indexOf('{'), data.lastIndexOf('}') + 1)) : null,
      ms: Date.now() - started,
    }));
    socket.on('error', (err) => { if (!['ECONNRESET', 'EPIPE'].includes(err.code)) reject(err); });
  });
}
export const operationBody = (request) => JSON.stringify(request);
export const partialHead = (port) => [
  'POST /v1/voice/operations HTTP/1.1', 'host: x', `authorization: Bearer ${TOKEN}`,
  'content-type: application/json', 'content-length: 100', 'connection: close', '', '',
].join('\r\n');
