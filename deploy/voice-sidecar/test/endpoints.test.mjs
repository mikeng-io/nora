// Endpoint contract tests (design/voice-channel/audio.md; the `voice`
// contract, design/service-api/contracts.md «Voice»): the routes and the
// envelope, generation scoping, single-guild binding, replace-play,
// stop-vs-leave, pause-during-speech, volume bounds, speech bounds, the status
// query and the operation ledger. Events: events.test.mjs; several guilds:
// guilds.test.mjs.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import net from 'node:net';

import { SERVED_ROUTES } from '../src/controller.mjs';
import { makeHarness, playTrack, tick, until, GUILD, CHANNEL, TOKEN, AUTH } from './helpers.mjs';

// The kernel's status table (nora/domain/service_api.py `_STATUS`), restated
// here so the controller's own table is checked, not reused.
const KERNEL_STATUS = {
  ok: 200, nothing_found: 200, unavailable: 503,
  complete: 200, pending: 202, denied: 403, rejected: 200,
  unsupported: 200, incomplete: 200, uncertain: 200,
};
const ENVELOPE_PARTS = ['from', 'outcome', 'payload', 'scope', 'trace', 'when'];
const OTHER_GUILD = '222222222222222222';
const TRACE_ID = 'a1'.repeat(16);
const TRACEPARENT = `00-${TRACE_ID}-${'b2'.repeat(8)}-01`;
const tts = () => Buffer.from('tts').toString('base64');

// The pinned snapshot; `autopaused` is the "is sound actually moving" ground
// truth, beside `player.position_ms`.
const PINNED_SNAPSHOT_KEYS = [
  'voice', 'generation', 'channel_id', 'playback_id', 'player', 'autopaused',
  'subscribers', 'playable', 'pending_reaps', 'speech_queue', 'crossfade_seconds',
];

function assertEnvelope(res, outcome, label) {
  assert.deepEqual(Object.keys(res.body).sort(), ENVELOPE_PARTS, label);
  assert.equal(res.body.outcome, outcome, label);
  assert.equal(res.status, KERNEL_STATUS[outcome], label);
}

function assertNoEnvelope(res, status, label) {
  assert.equal(res.status, status, label);
  assert.deepEqual(Object.keys(res.body), ['error'], label);
}

// Raw HTTP over a socket, for what fetch will not send: a body that stops
// short. Resolves with the status and the parsed body.
function rawRequest(port, head, body, { end = false } = {}) {
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
function rawStream(port, body, { contentLength = Buffer.byteLength(body), path = '/v1/voice/operations' } = {}) {
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
const operationBody = (request) => JSON.stringify(request);

const partialHead = (port) => [
  'POST /v1/voice/operations HTTP/1.1', 'host: x', `authorization: Bearer ${TOKEN}`,
  'content-type: application/json', 'content-length: 100', 'connection: close', '', '',
].join('\r\n');

// -- the voice contract (docs/service-api/openapi.json) -------------------------

test('routes match the voice contract', async () => {
  const doc = JSON.parse(readFileSync(
    new URL('../../../docs/service-api/openapi.json', import.meta.url), 'utf8'));
  const documented = Object.entries(doc.paths)
    .filter(([p]) => /^\/v1\/voice\//.test(p))
    .flatMap(([p, ops]) => Object.keys(ops).map((m) => `${m.toUpperCase()} ${p}`)).sort();
  const served = SERVED_ROUTES.filter((r) => / \/v1\//.test(r)).sort();
  assert.equal(documented.length, 3);
  assert.deepEqual(served, documented);
  assert.deepEqual(SERVED_ROUTES.filter((r) => !/ \/v1\//.test(r)).sort(), ['GET /health', 'GET /ready']);

  const h = await makeHarness({ ready: false });
  const call = (method, p, headers = AUTH) => fetch(`http://127.0.0.1:${h.port}${p}`, {
    method, headers: { 'content-type': 'application/json', ...headers },
    body: method === 'GET' ? undefined : '{}',
  });
  // Each documented route is answered by the router, with `{param}` filled.
  for (const route of documented) {
    const [method, template] = route.split(' ');
    const res = await call(method, template.replace(/\{[a-z_]+\}/g, 'x1'));
    assert.notEqual(res.status, 404, route);
    await res.body?.cancel();
  }
  // None of the old wire is served (built from its parts, so no file names an
  // old route as a literal).
  const oldWire = [
    ...['join', 'exit', 'status', 'event'].map((v) => ['', 'voice', v].join('/')),
    ...['play', 'stop', 'pause', 'resume', 'volume'].map((v) => ['', 'music', v].join('/')),
    ['', 'stream'].join('/'),
    ['', 'play'].join('/'),
  ];
  for (const old of oldWire) {
    for (const method of ['GET', 'POST']) {
      const res = await call(method, old);
      assert.equal(res.status, 404, `${method} ${old}`);
      assert.deepEqual(await res.json(), { error: 'not_found' });
    }
  }
  // The probes: /ready follows whether the control plane can serve; both keep
  // /health's authentication.
  assert.equal((await call('GET', '/ready')).status, 503);
  h.setReady(true);
  assert.equal((await call('GET', '/ready')).status, 200);
  assert.equal((await call('GET', '/health')).status, 200);
  for (const probe of ['/ready', '/health']) {
    assert.equal((await call('GET', probe, {})).status, 401, probe);
  }
  await h.close();
});

test('voice outcomes follow the contract', async () => {
  let authorized = true;
  const h = await makeHarness({ authorizeGuild: async () => authorized });

  // complete, with every part as the contract shapes it.
  const joined = await h.op('join', { guild_id: GUILD, channel_id: CHANNEL },
    { operationId: 'op-join', headers: { traceparent: TRACEPARENT } });
  assertEnvelope(joined, 'complete', 'join');
  assert.deepEqual(Object.keys(joined.body.payload), ['state']);
  assert.deepEqual(Object.keys(joined.state).sort(), [...PINNED_SNAPSHOT_KEYS].sort());
  assert.equal(joined.body.when.state, 'settled');
  assert.equal(typeof joined.body.when.started_at_ms, 'number');
  assert.equal(typeof joined.body.when.settled_at_ms, 'number');
  assert.deepEqual(joined.body.from, { target: GUILD, platform_ref: null });
  assert.deepEqual(joined.body.scope, { level: 'installation' });
  assert.equal(joined.body.trace, TRACE_ID);
  const { generation } = joined.state;

  // rejected: a stale generation, with the current one in the payload's state.
  const stale = await h.op('stop', { guild_id: GUILD, generation: 'stale-gen' });
  assertEnvelope(stale, 'rejected', 'stale generation');
  assert.equal(stale.body.trace, '');
  assert.deepEqual(stale.body.payload.error, { kind: 'generation_conflict', message: 'generation_conflict' });
  assert.equal(stale.state.generation, generation);
  // rejected: connected elsewhere, and an arbiter refusal.
  const elsewhere = await h.op('join', { guild_id: GUILD, channel_id: 'c2' });
  assertEnvelope(elsewhere, 'rejected', 'connected elsewhere');
  assert.equal(elsewhere.error, 'connected_elsewhere');
  await playTrack(h, { generation });
  await h.op('speak', { guild_id: GUILD, generation, audio_b64: tts(), media_type: 'audio/mpeg' });
  const paused = await h.op('pause', { guild_id: GUILD, generation });
  assertEnvelope(paused, 'rejected', 'pause during speech');
  assert.equal(paused.error, 'pause_during_speech');
  const idle = await makeHarness();
  const idleGen = (await idle.join()).generation;
  const nothing = await idle.op('resume', { guild_id: GUILD, generation: idleGen });
  assertEnvelope(nothing, 'rejected', 'an arbiter refusal with no music');
  assert.equal(nothing.error, 'no_music_playing');
  await idle.close();
  // rejected: no connection for a guild, on a non-join operation.
  const unknown = await h.op('stop', { guild_id: OTHER_GUILD, generation: 'g' });
  assertEnvelope(unknown, 'rejected', 'unknown guild');
  assert.equal(unknown.error, 'unknown_guild');
  assert.equal(unknown.state, null);
  assert.deepEqual(unknown.body.from, { target: OTHER_GUILD, platform_ref: null });

  // unavailable: a full speech queue.
  let speech;
  for (let i = 0; i < 6; i += 1) {
    speech = await h.op('speak', { guild_id: GUILD, generation, audio_b64: Buffer.from(`s${i}`).toString('base64') });
  }
  assertEnvelope(speech, 'unavailable', 'speech queue full');
  assert.equal(speech.error, 'speech_queue_full');

  // uncertain: a handler that fails unexpectedly.
  const setVolume = h.core.arbiter.setVolume;
  h.core.arbiter.setVolume = () => { throw new Error('upstream detail'); };
  const failed = await h.op('set_volume', { guild_id: GUILD, generation, percent: 50 });
  h.core.arbiter.setVolume = setVolume;
  assertEnvelope(failed, 'uncertain', 'internal error');
  assert.deepEqual(failed.body.payload.error, { kind: 'internal_error', message: 'controller operation failed' });

  // unsupported: the controller takes no reports.
  const report = await h.op('report', { event_id: 'e1', event_seq: 1, type: 'voice-death' });
  assertEnvelope(report, 'unsupported', 'report');
  assert.deepEqual(report.body.from, { target: '', platform_ref: null });

  // 400 with no envelope, for each malformed request.
  const malformed = [
    ['wrong guild', { kind: 'stop', effect: { guild_id: 'g2', generation } }],
    ['invalid body', { kind: 'play', effect: { guild_id: GUILD, generation } }],
    ['percent out of range', { kind: 'set_volume', effect: { guild_id: GUILD, generation, percent: 201 } }],
    ['audio too large', { kind: 'speak', effect: { guild_id: GUILD, generation,
      audio_b64: Buffer.alloc(2 * 1024 * 1024 + 1, 1).toString('base64') } }],
    ['no effect', { kind: 'stop', effect: 'x' }],
  ];
  for (const [label, request] of malformed) {
    assertNoEnvelope(await h.post('/v1/voice/operations', { operation_id: `bad-${label.length}`, ...request }), 400, label);
  }
  assertNoEnvelope(await h.post('/v1/voice/operations', { kind: 'stop', effect: {} }), 400, 'no operation_id');
  const badJson = await fetch(`http://127.0.0.1:${h.port}/v1/voice/operations`, {
    method: 'POST', headers: { 'content-type': 'application/json', ...AUTH }, body: '{nope' });
  assertNoEnvelope({ status: badJson.status, body: await badJson.json() }, 400, 'invalid json');
  // A 400 never became an operation: no id of them, the ones refused inside a
  // handler included, reads back.
  for (const [label] of malformed) {
    assertEnvelope(await h.get(`/v1/voice/operations/bad-${label.length}`), 'nothing_found', `read-back: ${label}`);
  }
  const partial = await rawRequest(h.port, partialHead(h.port), '{"oper', { end: true });
  assertNoEnvelope(partial, 400, 'body read failed');
  assert.equal(partial.body.error, 'body_read_failed');

  // 401, 408 and 413 are refused before any handler runs; 413 also for a
  // connection operation over the control bound, before its effect.
  const unauthorized = await fetch(`http://127.0.0.1:${h.port}/v1/voice/operations`, { method: 'POST', body: '{}' });
  assertNoEnvelope({ status: unauthorized.status, body: await unauthorized.json() }, 401, 'no bearer');
  assertNoEnvelope(await rawStream(h.port, operationBody({ pad: 'x'.repeat(70 * 1024) })), 413, 'over the control bound');
  const spawnedBefore = h.procs.length;
  const longTitle = await rawStream(h.port, operationBody({ operation_id: 'op-long-title', kind: 'play',
    effect: { guild_id: GUILD, generation, stream_url: 'https://cdn.test/t', title: 'x'.repeat(550 * 1024) } }));
  assertNoEnvelope(longTitle, 413, 'a play over the control bound');
  assert.equal(h.procs.length, spawnedBefore, 'refused before any effect');
  assertEnvelope(await h.get('/v1/voice/operations/op-long-title'), 'nothing_found', 'read-back: long title');
  const slow = await makeHarness({ bodyTimeoutMs: 100 });
  const timedOut = await rawRequest(slow.port, partialHead(slow.port), '{"oper');
  assertNoEnvelope(timedOut, 408, 'body still arriving at the deadline');
  assert.equal(timedOut.body.error, 'body_timeout');
  await slow.close();

  // 422: a kind outside the closed set, and nothing applied.
  const played = h.procs.length;
  assertNoEnvelope(await h.op('shuffle', { guild_id: GUILD, generation }), 422, 'unknown kind');
  assert.equal(h.procs.length, played);

  // The same id and effect is answered once; another effect under it is 409.
  const effect = { guild_id: GUILD, generation, stream_url: 'https://cdn.test/once' };
  const first = await h.op('play', effect, { operationId: 'op-once' });
  assertEnvelope(first, 'complete', 'first play');
  const spawned = h.procs.length;
  const again = await h.op('play', { ...effect }, { operationId: 'op-once' });
  assert.deepEqual(again.body, first.body, 'the stored answer');
  assert.equal(h.procs.length, spawned, 'not run again');
  assertNoEnvelope(await h.op('play', { ...effect, stream_url: 'https://cdn.test/other' }, { operationId: 'op-once' }),
    409, 'another effect');
  assertNoEnvelope(await h.op('stop', effect, { operationId: 'op-once' }), 409, 'another kind');
  assert.equal(h.procs.length, spawned);

  // Read-back: ok with the stored envelope, nothing_found for an unknown id.
  const readBack = await h.get('/v1/voice/operations/op-join');
  assertEnvelope(readBack, 'ok', 'read-back');
  assert.deepEqual(readBack.body.payload, joined.body);
  assert.deepEqual(readBack.body.from, { source: 'voice-controller', healthy: true, failed_sources: [] });
  assert.equal(readBack.body.when.known_by, 'controller_clock');
  assert.equal(typeof readBack.body.when.observed_at_ms, 'number');
  assert.deepEqual(readBack.body.scope, { level: 'installation' });
  const missing = await h.get('/v1/voice/operations/op-never');
  assertEnvelope(missing, 'nothing_found', 'read-back of an unknown id');
  assert.equal(missing.body.payload, null);

  // The status query: ok with the status body, nothing_found with no connection.
  const status = await h.status();
  assertEnvelope(status, 'ok', 'status');
  assert.equal(status.payload.generation, generation);
  assert.equal(typeof status.payload.last_event_seq, 'number');
  const none = await h.status(OTHER_GUILD);
  assertEnvelope(none, 'nothing_found', 'status of a guild with no connection');
  assert.equal(none.payload, null);
  assertNoEnvelope(await h.post('/v1/voice/queries', { kind: 'members', params: { guild_id: GUILD } }), 422, 'unknown query kind');

  // denied on an operation, nothing_found on the status query: a forbidden guild.
  authorized = false;
  const denied = await h.op('stop', { guild_id: GUILD, generation });
  assertEnvelope(denied, 'denied', 'forbidden guild');
  assert.equal(denied.error, 'forbidden_guild');
  assertEnvelope(await h.status(), 'nothing_found', 'status of a forbidden guild');
  authorized = true;

  // unavailable: join_failed, and a ledger whose every slot is in flight.
  const fresh = await makeHarness({ joinError: new Error('discord unreachable') });
  const joinFailed = await fresh.op('join', { guild_id: GUILD, channel_id: CHANNEL });
  assertEnvelope(joinFailed, 'unavailable', 'join failed');
  assert.deepEqual(joinFailed.body.payload.error, { kind: 'join_failed', message: 'join_failed' });
  await fresh.close();

  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const held = await makeHarness({ joinGate: () => gate, ledgerOptions: { maxEntries: 1 } });
  const inFlight = held.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'op-held' });
  await until(() => held.joinCalls.length === 1);
  // In flight, the record reads back as pending.
  const pending = await held.get('/v1/voice/operations/op-held');
  assertEnvelope(pending, 'ok', 'read-back in flight');
  assert.equal(pending.body.payload.outcome, 'pending');
  assert.equal(pending.body.payload.when.state, 'started');
  const full = await held.op('stop', { guild_id: GUILD, generation: 'g' });
  assertEnvelope(full, 'unavailable', 'ledger full');
  assert.equal(full.error, 'dedupe_full');
  release();
  assertEnvelope(await inFlight, 'complete', 'the held operation completes');
  await held.close();
  await h.close();
});

// -- connection operations ------------------------------------------------------

test('join returns the pinned state snapshot shape with a minted generation', async () => {
  const h = await makeHarness();
  const res = await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'req-1' });
  assert.equal(res.status, 200);
  assert.equal(res.outcome, 'complete');
  assert.deepEqual(Object.keys(res.state).sort(), PINNED_SNAPSHOT_KEYS.sort());
  const s = res.state;
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

test('join is idempotent in the same channel and rejected connected_elsewhere in another', async () => {
  const h = await makeHarness();
  const first = await h.join();
  const again = await h.op('join', { guild_id: GUILD, channel_id: CHANNEL });
  assert.equal(again.outcome, 'complete');
  assert.equal(again.state.generation, first.generation);
  assert.equal(h.joinCalls.length, 1); // no second join happened

  // Discord allows ONE voice connection per bot per guild: a second channel is
  // refused by the lifecycle, not by an allow-list.
  const elsewhere = await h.op('join', { guild_id: GUILD, channel_id: 'c2' });
  assert.equal(elsewhere.outcome, 'rejected');
  assert.equal(elsewhere.error, 'connected_elsewhere');
  assert.equal(h.joinCalls.length, 1);
  await h.close();
});

test('join admits ANY channel in the guild when idle (design/voice-channel/audio.md «Connection lifecycle»)', async () => {
  // The regression this guards: a member sitting in any voice channel but a
  // configured one could never be joined. Idle, so no connected_elsewhere.
  const h = await makeHarness();
  const res = await h.op('join', { guild_id: GUILD, channel_id: 'some-other-channel' });
  assert.equal(res.outcome, 'complete');
  assert.equal(res.state.channel_id, 'some-other-channel');
  assert.equal(h.joinCalls.length, 1);
  assert.equal(h.joinCalls[0], 'some-other-channel');
  await h.close();
});

test('join with the wrong guild is 400 wrong_guild', async () => {
  const h = await makeHarness();
  const res = await h.op('join', { guild_id: 'g2', channel_id: CHANNEL });
  assert.equal(res.status, 400);
  assert.equal(res.error, 'wrong_guild');
  assert.equal(h.joinCalls.length, 0);
  await h.close();
});

test('every generation-scoped kind rejects a stale generation with the current one', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const scoped = [
    ['leave', {}],
    ['play', { stream_url: 'https://cdn.test/a' }],
    ['stop', {}],
    ['pause', {}],
    ['resume', {}],
    ['set_volume', { percent: 100 }],
    ['speak', { audio_b64: tts(), media_type: 'audio/mpeg' }],
  ];
  for (const [kind, extra] of scoped) {
    const res = await h.op(kind, { guild_id: GUILD, generation: 'stale-gen', ...extra });
    assert.equal(res.status, 200, kind);
    assert.equal(res.outcome, 'rejected', kind);
    assert.equal(res.error, 'generation_conflict', kind);
    assert.equal(res.state.generation, generation, kind);
  }
  assert.equal(h.procs.length, 0); // nothing acted
  assert.equal(h.leaves(), 0);
  await h.close();
});

test('replace-play: old playback emits no track-finished, new playback_id is minted', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const a = await playTrack(h, { generation, title: 'A' });
  const b = await h.op('play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/b', title: 'B' });
  assert.equal(b.outcome, 'complete');
  const bId = b.state.playback_id;
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

test('stop stays connected: connection and wake pump survive, no-op when idle', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  await playTrack(h, { generation });

  const res = await h.op('stop', { guild_id: GUILD, generation });
  assert.equal(res.outcome, 'complete');
  assert.equal(res.state.voice, 'connected');
  assert.equal(res.state.generation, generation);
  assert.equal(res.state.playback_id, null);
  assert.equal(h.leaves(), 0); // connection NOT destroyed
  assert.equal(h.pumpCloses(), 0); // wake pump NOT closed

  // No music playing/starting/suspended -> complete, a no-op.
  const again = await h.op('stop', { guild_id: GUILD, generation });
  assert.equal(again.outcome, 'complete');
  await h.close();
});

test('leave stops everything: music, speech, pump, connection; generation invalidated', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const a = await playTrack(h, { generation });
  await h.op('speak', { guild_id: GUILD, generation, audio_b64: tts(), media_type: 'audio/mpeg' });

  const res = await h.op('leave', { guild_id: GUILD, generation });
  assert.equal(res.outcome, 'complete');
  assert.equal(res.state.voice, 'idle');
  assert.equal(res.state.generation, null);
  assert.equal(h.leaves(), 1); // connection destroyed
  assert.equal(h.pumpCloses(), 1); // wake pump closed
  assert.deepEqual(a.proc.signals, ['SIGTERM']); // music ffmpeg terminated

  const late = await h.op('play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/c' });
  assert.equal(late.outcome, 'rejected'); // old generation is dead
  assert.equal(h.procs.length, 1);
  await h.close();
});

test('any operation whose guild_id is not a guild is 400 wrong_guild', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  for (const kind of ['play', 'stop', 'leave', 'speak']) {
    const res = await h.op(kind, { guild_id: 'g2', generation, stream_url: 'u', audio_b64: 'eA' });
    assert.equal(res.status, 400, kind);
    assert.equal(res.error, 'wrong_guild', kind);
  }
  await h.close();
});

test('pause during speech is rejected pause_during_speech', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  await playTrack(h, { generation });
  const speech = await h.op('speak', { guild_id: GUILD, generation, audio_b64: tts(), media_type: 'audio/mpeg' });
  assert.equal(speech.outcome, 'complete');
  assert.equal(speech.state.player.status, 'suspended');

  const res = await h.op('pause', { guild_id: GUILD, generation });
  assert.equal(res.outcome, 'rejected');
  assert.equal(res.error, 'pause_during_speech');
  await h.close();
});

test('set_volume accepts 0..200 and refuses the rest with 400 percent_out_of_range', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  await playTrack(h, { generation });
  for (const percent of [0, 100, 200]) {
    const res = await h.op('set_volume', { guild_id: GUILD, generation, percent });
    assert.equal(res.outcome, 'complete', `percent=${percent}`);
  }
  assert.equal(h.player.current.volume.value, 2);
  for (const percent of [-1, 201, 'loud', null]) {
    const res = await h.op('set_volume', { guild_id: GUILD, generation, percent });
    assert.equal(res.status, 400, `percent=${percent}`);
    assert.equal(res.error, 'percent_out_of_range');
  }
  await h.close();
});

test('speak enforces the decoded 2MB budget and the bounded speech queue', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();

  const tooBig = Buffer.alloc(2 * 1024 * 1024 + 1, 1);
  const big = await h.op('speak', { guild_id: GUILD, generation, audio_b64: tooBig.toString('base64'), media_type: 'audio/mpeg' });
  assert.equal(big.status, 400);
  assert.equal(big.error, 'audio_too_large');

  for (let i = 0; i < 5; i += 1) {
    const res = await h.op('speak', {
      guild_id: GUILD, generation, audio_b64: Buffer.from(`s${i}`).toString('base64'), media_type: 'audio/mpeg',
    });
    assert.equal(res.outcome, 'complete', `speech ${i}`);
  }
  const full = await h.op('speak', {
    guild_id: GUILD, generation, audio_b64: Buffer.from('s5').toString('base64'), media_type: 'audio/mpeg',
  });
  assert.equal(full.status, 503);
  assert.equal(full.outcome, 'unavailable');
  assert.equal(full.error, 'speech_queue_full');
  await h.close();
});

test('the status query carries the snapshot plus seq/streaming/error/wake/pump', async () => {
  const h = await makeHarness();
  const idle = await h.status();
  assert.equal(idle.outcome, 'ok');
  assert.equal(idle.payload.last_event_seq, 0);
  assert.equal(idle.payload.streaming, false);
  assert.equal(idle.payload.last_error, null);
  assert.deepEqual(idle.payload.wake, { u1: { frames: 1, bytes: 3840 } });
  assert.deepEqual(idle.payload.pump, {
    host: '127.0.0.1',
    port: 4599,
    drops: { backlog_dropped: 0, no_listener_dropped: 0 },
  });

  const { generation } = await h.join();
  const t = await playTrack(h, { generation });
  const playing = await h.status();
  assert.equal(playing.payload.streaming, true);
  assert.equal(playing.payload.player.status, 'playing');
  assert.equal(playing.payload.playback_id, t.playbackId);
  assert.deepEqual(playing.payload.player.current, { title: 'A', url: 'https://cdn.test/a' });

  t.proc.close(1); // abnormal exit -> stream-failed surfaces in status
  await tick();
  await tick();
  const failed = await h.status();
  assert.equal(failed.payload.streaming, false);
  assert.equal(failed.payload.last_error, 'ffmpeg-exit-1');
  assert.equal(failed.payload.last_event_seq, 1);
  assert.equal(failed.payload.outbox_dropped, 0);
  await h.close();
});

test('/health is 200 liveness even healthy-but-idle', async () => {
  const h = await makeHarness();
  const health = await h.get('/health');
  assert.equal(health.status, 200);
  assert.equal(health.body.status, 'ok');
  // PROCESS liveness, not one guild's: the compose HEALTHCHECK curls this and
  // must get 200 while the process holds no connection at all.
  assert.deepEqual(health.body.guilds, [GUILD]);
  await h.close();
});

test('the same operation_id within 5 minutes returns the stored answer without re-acting', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const effect = { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a' };
  const first = await h.op('play', effect, { operationId: 'req-dedupe' });
  assert.equal(first.outcome, 'complete');
  assert.equal(h.procs.length, 1);

  const second = await h.op('play', effect, { operationId: 'req-dedupe' });
  assert.deepEqual(second.body, first.body); // stored answer, same playback_id
  assert.equal(h.procs.length, 1); // handler NOT re-executed

  h.clock.advance(5 * 60 * 1000 + 1); // past the ledger's window
  const third = await h.op('play', effect, { operationId: 'req-dedupe' });
  assert.equal(third.outcome, 'complete');
  assert.equal(h.procs.length, 2); // re-executed after expiry
  await h.close();
});

test('a refusal carries the six parts and the current snapshot', async () => {
  const h = await makeHarness();
  await h.join();
  const res = await h.op('stop', { guild_id: GUILD, generation: 'nope' }, { operationId: 'r-err' });
  assert.equal(res.status, 200);
  assert.deepEqual(Object.keys(res.body).sort(), ENVELOPE_PARTS);
  assert.deepEqual(Object.keys(res.body.payload).sort(), ['error', 'state']);
  assert.equal(res.state.voice, 'connected');
  await h.close();
});

test('the control bound is enforced on arriving bytes; only speak runs on to the speech cap', async () => {
  // Every kind shares one route, so the kind is read off the arriving bytes:
  // a body past 64 KiB goes on only if it has already named `speak`.
  const h = await makeHarness();
  const { generation } = await h.join();
  const play = (extra) => ({ operation_id: 'big', kind: 'play',
    effect: { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a', ...extra } });

  // A partial upload past the bound is refused at once, not at the deadline.
  const partial = await rawStream(h.port, operationBody(play({ title: 'x'.repeat(70 * 1024) })).slice(0, 70 * 1024),
    { contentLength: 100 * 1024 });
  assertNoEnvelope(partial, 413, 'a partial play upload');
  assert.equal(partial.body.error, 'body_too_large');
  assert.ok(partial.ms < 5000, `refused as it arrived, after ${partial.ms} ms, not at the 15 s deadline`);
  // Whitespace counts: a complete request padded past the bound is refused.
  const padded = operationBody(play({})).replace('{', `{${' '.repeat(70 * 1024)}`);
  assertNoEnvelope(await rawStream(h.port, padded), 413, 'whitespace padding');
  assert.equal(h.procs.length, 0, 'nothing played');
  // A kind repeated after a large body is read as JSON reads it, the last one.
  const repeated = operationBody({ operation_id: 'dup', kind: 'speak',
    effect: { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a', pad: 'x'.repeat(70 * 1024) } })
    .replace(/}$/, ',"kind":"play"}');
  assertNoEnvelope(await rawStream(h.port, repeated), 413, 'a later kind decides');

  // speak runs to the speech cap, and no further.
  const speech = await h.op('speak', { guild_id: GUILD, generation,
    audio_b64: Buffer.alloc(2 * 1024 * 1024, 1).toString('base64') });
  assert.equal(speech.outcome, 'complete', 'a 2 MiB speech, 2.7 MiB as base64');
  const overCap = await rawStream(h.port, operationBody({ operation_id: 'huge', kind: 'speak',
    effect: { guild_id: GUILD, generation, audio_b64: 'A'.repeat(4 * 1024 * 1024) } }));
  assertNoEnvelope(overCap, 413, 'over the speech cap');
  // Its ceiling: a speech must name its kind before an effect past the bound.
  const late = await rawStream(h.port, JSON.stringify({ operation_id: 'late',
    effect: { guild_id: GUILD, generation, audio_b64: 'A'.repeat(70 * 1024) }, kind: 'speak' }));
  assertNoEnvelope(late, 413, 'kind after a large effect');

  // A query's bound is a control body's.
  const query = await rawStream(h.port, JSON.stringify({ kind: 'status', params: { guild_id: GUILD }, pad: 'x'.repeat(70 * 1024) }),
    { path: '/v1/voice/queries' });
  assert.equal(query.status, 413);
  await h.close();
});

test('a body that fails mid-read is 400 body_read_failed, not body_too_large', async () => {
  const h = await makeHarness();
  const port = h.port;
  const raw = await new Promise((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port }, () => {
      // Promise 100 bytes, send 7, then FIN: the upload dies mid-body but
      // the half-open socket can still carry the answer.
      socket.write(`POST /v1/voice/operations HTTP/1.1\r\nhost: x\r\nauthorization: Bearer ${TOKEN}\r\ncontent-length: 100\r\nconnection: close\r\n\r\npartial`);
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
  assert.equal(res.outcome, 'complete');
  await h.close();
});

test('two joins waiting on one leave produce exactly ONE subsequent joinVoice', async () => {
  let releaseLeave;
  const leaveGate = new Promise((resolve) => {
    releaseLeave = resolve;
  });
  const h = await makeHarness({ leaveGate: () => leaveGate });
  const { generation } = await h.join();
  assert.equal(h.joinCalls.length, 1);

  const exitP = h.op('leave', { guild_id: GUILD, generation });
  assert.equal(await until(() => h.leaves() === 1, 5000), true); // teardown parked inside leave()
  const j1 = h.op('join', { guild_id: GUILD, channel_id: CHANNEL });
  const j2 = h.op('join', { guild_id: GUILD, channel_id: CHANNEL });
  assert.equal(await until(() => h.core.lifecycle.voice() === 'destroying'), true);
  await tick();
  await tick();
  releaseLeave();

  const [exitRes, r1, r2] = await Promise.all([exitP, j1, j2]);
  assert.equal(exitRes.outcome, 'complete');
  assert.equal(r1.outcome, 'complete');
  assert.equal(r2.outcome, 'complete');
  assert.equal(h.joinCalls.length, 2); // exactly one re-join, not two
  assert.equal(r1.state.generation, r2.state.generation);
  assert.notEqual(r1.state.generation, generation);
  await h.close();
});

test('an intentional leave emits NO voice-death and invalidates the generation at teardown start', async () => {
  let releaseLeave;
  const leaveGate = new Promise((resolve) => {
    releaseLeave = resolve;
  });
  const h = await makeHarness({ leaveGate: () => leaveGate });
  const { generation } = await h.join();

  const exitP = h.op('leave', { guild_id: GUILD, generation });
  assert.equal(await until(() => h.leaves() === 1, 5000), true); // teardown parked inside leave(); generation already dead
  const late = await h.op('play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/x' });
  assert.equal(late.outcome, 'rejected');
  assert.equal(late.error, 'generation_conflict');

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

test('two concurrent operations with the same operation_id execute the handler exactly once', async () => {
  // This pins the LEDGER, so the kind must be one the lifecycle does NOT
  // already coalesce — join/leave ride joinPromise/destroyPromise and prove
  // nothing about this layer. Music replacement is non-idempotent: without
  // the ledger, two plays would mean two ffmpeg spawns and two playback_ids.
  const h = await makeHarness();
  const { generation } = await h.join();
  const effect = { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a' };
  const p1 = h.op('play', effect, { operationId: 'req-concurrent' });
  const p2 = h.op('play', effect, { operationId: 'req-concurrent' }); // inside the first one's window
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.outcome, 'complete');
  assert.equal(h.procs.length, 1); // the handler executed ONCE
  assert.deepEqual(r2.body, r1.body); // same playback_id, same snapshot
  await h.close();
});

test('play accepts an optional boolean or null resumable; anything else is 400 invalid_body', async () => {
  // nora forwards the source's resumable capability (seekable vs live) in the
  // play effect; absent keeps the arbiter default.
  const h = await makeHarness();
  const { generation } = await h.join();

  for (const resumable of ['yes', 1]) {
    const bad = await h.op('play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/x', resumable });
    assert.equal(bad.status, 400, `resumable=${resumable}`);
    assert.equal(bad.error, 'invalid_body');
  }
  assert.equal(h.procs.length, 0); // nothing acted

  const live = await h.op('play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/radio', resumable: false });
  assert.equal(live.outcome, 'complete');
  assert.equal(live.state.player.position_policy, 'none'); // live source: no resume

  const regular = await h.op('play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a' });
  assert.equal(regular.outcome, 'complete');
  assert.equal(regular.state.player.position_policy, 'approximate_seek'); // default unchanged

  // null is absent (the contract's PlayEffect allows it): the default policy.
  const unset = await h.op('play', { guild_id: GUILD, generation, stream_url: 'https://cdn.test/b', resumable: null });
  assert.equal(unset.outcome, 'complete');
  assert.equal(unset.state.player.position_policy, 'approximate_seek');
  await h.close();
});

test('a retried leave with the same operation_id returns the stored answer without re-acting', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const effect = { guild_id: GUILD, generation };
  const first = await h.op('leave', effect, { operationId: 'req-exit-1' });
  assert.equal(first.outcome, 'complete');
  assert.equal(h.leaves(), 1);

  // The generation is dead now; without the ledger this retry would be
  // rejected (or worse, a second teardown). The stored answer is returned.
  const second = await h.op('leave', effect, { operationId: 'req-exit-1' });
  assert.deepEqual(second.body, first.body);
  assert.equal(h.leaves(), 1);
  await h.close();
});

test('a JSON null body is 400 invalid_body and the server keeps serving', async () => {
  const h = await makeHarness();
  const raw = await fetch(`http://127.0.0.1:${h.port}/v1/voice/operations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...AUTH },
    body: 'null',
  });
  assert.equal(raw.status, 400);
  assert.deepEqual(await raw.json(), { error: 'invalid_body' });

  const list = await fetch(`http://127.0.0.1:${h.port}/v1/voice/operations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...AUTH },
    body: '[1,2]',
  });
  assert.equal(list.status, 400);

  // The server survived: a normal request still works.
  const { res } = await h.join();
  assert.equal(res.outcome, 'complete');
  await h.close();
});

for (const size of [400_000, 1024 * 1024, 2 * 1024 * 1024]) {
  test(`speak admits ${size} decoded bytes, answers its id once and keeps no audio`, async () => {
    const h = await makeHarness();
    try {
      const { generation } = await h.join();
      const effect = { guild_id: GUILD, generation, audio_b64: Buffer.alloc(size, 1).toString('base64') };
      const first = await h.op('speak', effect, { operationId: 'large-speech' });
      assert.equal(first.outcome, 'complete');
      const played = h.player.played.length;
      const duplicate = await h.op('speak', effect, { operationId: 'large-speech' });
      assert.deepEqual(duplicate.body, first.body, 'the duplicate returns the first outcome');
      assert.equal(h.player.played.length, played, 'the duplicate does not enqueue audio');
      // The same id with a changed effect is refused, never replayed.
      const changed = await h.op('speak', { ...effect, generation: 'stale' }, { operationId: 'large-speech' });
      assert.equal(changed.status, 409);
      assert.deepEqual(changed.body, { error: 'operation_id_reused' });
      assert.ok(h.ledger.bytes() < 4096, 'only the hash and the small envelope are retained');
    } finally { await h.close(); }
  });
}
