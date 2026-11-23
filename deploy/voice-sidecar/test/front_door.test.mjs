// The control plane's perimeter: the shared bearer on every route, a bounded
// operation ledger that expires on a timer, over-limit bodies answered and
// closed unread, and listener limits.
//
// Falsification: drop the bearer check in createFrontDoor and the auth matrix
// goes green-to-red on every route; drop makeRoom and the budget tests see the
// ledger grow past its bound; drop the sweep interval and the expiry test never
// sees the entry go; restore the drain in readBody and the unread-body test
// waits for the rest of the upload.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import net from 'node:net';

import { bearerMatches } from '../src/auth.mjs';
import { createFrontDoor } from '../src/controller.mjs';
import { createFakeClock, makeHarness, until, GUILD, TOKEN, AUTH, CHANNEL } from './helpers.mjs';

// Raw HTTP over a socket, for the cases fetch will not produce (an unread body,
// a body that never finishes). Resolves with everything received before close.
function rawExchange(port, head, body = '', { after = 0 } = {}) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port }, () => {
      socket.write(head + body);
    });
    let data = '';
    socket.on('data', (chunk) => {
      data += chunk.toString();
    });
    socket.on('close', () => resolve({ data, closed: true }));
    socket.on('error', (err) => {
      if (err.code === 'ECONNRESET' || err.code === 'EPIPE') resolve({ data, closed: true });
      else reject(err);
    });
    if (after) setTimeout(() => { resolve({ data, closed: false }); socket.destroy(); }, after).unref();
  });
}

test('the bearer compare is byte-length safe and refuses an empty token', () => {
  assert.equal(bearerMatches(`Bearer ${TOKEN}`, TOKEN), true);
  assert.equal(bearerMatches('Bearer nope', TOKEN), false);
  assert.equal(bearerMatches(undefined, TOKEN), false);
  // Same string length in characters, different in UTF-8 bytes: must be a
  // plain refusal, not timingSafeEqual's length-mismatch throw.
  assert.equal(bearerMatches(`Bearer ${'é'.repeat(TOKEN.length)}`, TOKEN), false);
  assert.equal(bearerMatches('Bearer ', ''), false);
});

test('a front door refuses to exist without a token', () => {
  assert.throws(
    () => createFrontDoor({ controllerFor: () => null, health: () => ({}) }),
    /needs an authToken/,
  );
});

test('every route refuses a missing or wrong bearer, and does nothing for the caller', async () => {
  const h = await makeHarness();
  const operation = (kind, effect) => JSON.stringify({ operation_id: `op-${kind}`, kind, effect });
  const calls = [
    ['POST', '/v1/voice/operations', operation('join', { guild_id: GUILD, channel_id: CHANNEL })],
    ['POST', '/v1/voice/operations', operation('stop', { guild_id: GUILD, generation: 'x' })],
    ['POST', '/v1/voice/operations', operation('speak', { guild_id: GUILD, generation: 'x', audio_b64: 'AAA=' })],
    ['POST', '/v1/voice/queries', JSON.stringify({ kind: 'status', params: { guild_id: GUILD } })],
    ['GET', '/v1/voice/operations/op-join', undefined],
    ['GET', '/health', undefined],
    ['GET', '/ready', undefined],
  ];
  const headers = [{}, { authorization: 'Bearer wrong' }, { authorization: `Basic ${TOKEN}` }];
  for (const [method, path, body] of calls) {
    for (const h2 of headers) {
      const res = await fetch(`http://127.0.0.1:${h.port}${path}`, {
        method,
        headers: { 'content-type': 'application/json', ...h2 },
        body,
      });
      assert.equal(res.status, 401, `${method} ${path} with ${JSON.stringify(h2)}`);
      assert.equal((await res.json()).error, 'unauthorized');
    }
  }
  assert.equal(h.joinCalls.length, 0, 'an unauthenticated join never reaches lifecycle');
  await h.close();
});

test('the same routes answer for the bearer, and a non-ASCII header does not crash the server', async () => {
  const h = await makeHarness();
  const ok = await fetch(`http://127.0.0.1:${h.port}/health`, { headers: AUTH });
  assert.equal(ok.status, 200);
  const weird = await fetch(`http://127.0.0.1:${h.port}/health`, {
    headers: { authorization: `Bearer ${'é'.repeat(TOKEN.length)}` },
  });
  assert.equal(weird.status, 401);
  const after = await fetch(`http://127.0.0.1:${h.port}/health`, { headers: AUTH });
  assert.equal(after.status, 200, 'the listener survives a malformed bearer');
  await h.close();
});

test('an operation_id must be a short ASCII token; anything else is refused before the ledger', async () => {
  const h = await makeHarness();
  const bad = ['x'.repeat(65), 'a/../b', 'has space', 123, ''];
  for (const id of bad) {
    const res = await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: id });
    assert.equal(res.status, 400, `operation_id ${JSON.stringify(id)}`);
    assert.deepEqual(res.body, { error: 'invalid_operation_id' });
  }
  assert.equal(h.ledger.size(), 0, 'a refused id takes no ledger slot');
  const good = await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'req-1' });
  assert.equal(good.outcome, 'complete');
  await h.close();
});

test('the ledger holds at most its entry budget and keeps every receipt', async () => {
  const h = await makeHarness({ ledgerOptions: { maxEntries: 4 } });
  for (let i = 0; i < 10; i += 1) {
    // generation 'x' never matches: the first four are rejected and kept.
    const res = await h.op('stop', { guild_id: GUILD, generation: 'x' }, { operationId: `flood-${i}` });
    assert.equal(res.outcome, i < 4 ? 'rejected' : 'unavailable', `flood-${i}`);
    assert.ok(h.ledger.size() <= 4, `size ${h.ledger.size()} after ${i + 1}`);
  }
  for (let i = 0; i < 4; i += 1) {
    assert.equal((await h.get(`/v1/voice/operations/flood-${i}`)).body.outcome, 'ok', `flood-${i} still reads back`);
  }
  await h.close();
});

test('the ledger holds at most its byte budget, counting the kept envelopes', async () => {
  const budget = 2500;
  const h = await makeHarness({ ledgerOptions: { maxBytes: budget, answerBytes: 1000 } });
  const outcomes = [];
  for (let i = 0; i < 10; i += 1) {
    const res = await h.op('stop', { guild_id: GUILD, generation: 'x' }, { operationId: `bytes-${i}` });
    outcomes.push(res.outcome);
    assert.ok(h.ledger.bytes() <= budget, `bytes ${h.ledger.bytes()}`);
  }
  assert.equal(outcomes[0], 'rejected');
  assert.equal(outcomes.at(-1), 'unavailable', 'a full budget refuses the next id');
  await h.close();
});

test('an in-flight entry is never evicted: at the budget with everything in flight, the next id is unavailable', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const h = await makeHarness({ joinGate: () => gate, ledgerOptions: { maxEntries: 1 } });
  const first = h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'held' });
  await until(() => h.joinCalls.length === 1);
  const refused = await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'other' });
  assert.equal(refused.status, 503);
  assert.equal(refused.outcome, 'unavailable');
  assert.equal(refused.error, 'dedupe_full');
  release();
  const done = await first;
  assert.equal(done.outcome, 'complete', 'the held request still completes');
  await h.close();
});

test('an expired entry is dropped by the timer, with no further traffic', async () => {
  const clock = createFakeClock();
  const h = await makeHarness({
    clock,
    ledgerOptions: { ttlMs: 1000, sweepMs: 5 },
  });
  await h.op('stop', { guild_id: GUILD, generation: 'x' }, { operationId: 'expiring' });
  assert.equal(h.ledger.size(), 1);
  clock.advance(5000);
  assert.equal(
    await until(() => h.ledger.size() === 0, 400),
    true,
    'the sweep timer, not a later request, must release the entry',
  );
  await h.close();
});

test('an over-limit body is answered 413 and the connection closed without reading the rest', async () => {
  const h = await makeHarness();
  const head = [
    'POST /v1/voice/operations HTTP/1.1',
    'host: x',
    `authorization: Bearer ${TOKEN}`,
    'content-type: application/json',
    'content-length: 10000000', // far over the 64 KiB control bound
    'connection: keep-alive',
    '',
    '',
  ].join('\r\n');
  // Send only the first slice. A server that drains would sit waiting for the
  // other ~10 MB; this one answers at once and closes.
  const { data, closed } = await rawExchange(h.port, head, 'x'.repeat(70 * 1024), { after: 1000 });
  assert.match(data, /^HTTP\/1\.1 413 /);
  assert.match(data, /body_too_large/);
  assert.match(data, /connection: close/i);
  assert.equal(closed, true);
  await h.close();
});

test('a request whose body never finishes is cut by the request deadline', { timeout: 5000 }, async () => {
  const front = createFrontDoor({
    authToken: TOKEN,
    controllerFor: () => null,
    health: () => ({ status: 'ok' }),
    bodyTimeoutMs: 200,
  });
  await new Promise((resolve) => front.listen(0, '127.0.0.1', resolve));
  const head = [
    'POST /v1/voice/operations HTTP/1.1',
    'host: x',
    `authorization: Bearer ${TOKEN}`,
    'content-type: application/json',
    'content-length: 100',
    '',
    '',
  ].join('\r\n');
  const started = Date.now();
  const { closed } = await rawExchange(front.address().port, head, '{"guild_id"', { after: 1000 });
  assert.equal(closed, true, 'the stalled request is closed by the server');
  assert.ok(Date.now() - started < 3000, 'closed at the deadline, not after a socket timeout');
  await new Promise((resolve) => front.close(resolve));
});

test('the listener refuses connections beyond its limit', { timeout: 5000 }, async () => {
  const front = createFrontDoor({
    authToken: TOKEN,
    controllerFor: () => null,
    health: () => ({ status: 'ok' }),
    maxConnections: 1,
  });
  await new Promise((resolve) => front.listen(0, '127.0.0.1', resolve));
  const port = front.address().port;
  const holder = net.connect({ host: '127.0.0.1', port });
  await new Promise((resolve) => holder.once('connect', resolve));
  // Over the limit the server closes the new socket at once; without a limit
  // it stays open, so the wait below is the answer, not a hang.
  const closedByServer = await new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    socket.on('close', () => resolve(true));
    socket.on('error', () => resolve(true));
    setTimeout(() => {
      socket.destroy();
      resolve(false);
    }, 300);
  });
  assert.equal(closedByServer, true, 'the second connection is closed by the listener');
  holder.destroy();
  await new Promise((resolve) => front.close(resolve));
});


test('operation ids have a bounded ASCII format', async () => {
  const h = await makeHarness();
  for (const id of ['x'.repeat(65), 'space here', 'é', 42, '']) {
    const r = await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: id });
    assert.equal(r.status, 400);
    assert.equal(r.error, 'invalid_operation_id');
  }
  assert.equal(h.joinCalls.length, 0);
  await h.close();
});

test('the ledger caps in-flight entries and bytes before executing', async () => {
  let release;
  const gate = new Promise(r => { release = r; });
  const h = await makeHarness({ joinGate: () => gate, ledgerOptions: { maxEntries: 2, maxBytes: 8192, answerBytes: 1024 } });
  const first = h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'first' });
  await until(() => h.joinCalls.length === 1);
  assert.ok(h.ledger.bytes() >= 1024, 'the reservation holds room for the answer');
  const big = await h.op('stop', { guild_id: GUILD, generation: 'x', padding: 'x'.repeat(600) }, { operationId: 'large' });
  assert.equal(big.outcome, 'rejected');
  assert.ok(h.ledger.bytes() < 4096, 'an effect occupies the ledger only as its hash');
  const r = await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'second' });
  assert.equal(r.status, 503);
  assert.equal(h.ledger.size(), 2);
  release();
  await first;
  const tiny = await makeHarness({ ledgerOptions: { maxBytes: 128 } }); // under one answer's room
  const refused = await tiny.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'over-budget' });
  assert.equal(refused.status, 503, 'even an in-flight entry must fit the byte budget');
  assert.equal(tiny.joinCalls.length, 0);
  await tiny.close();
  await h.close();
});

test('settled ledger entries expire without new requests, freeing their room', async () => {
  const clock = createFakeClock();
  const h = await makeHarness({ clock, ledgerOptions: { maxEntries: 2, ttlMs: 100, sweepMs: 10 } });
  for (const id of ['one', 'two']) {
    assert.equal((await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: id })).outcome, 'complete');
  }
  assert.equal((await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'three' })).outcome, 'unavailable');
  assert.equal(h.ledger.size(), 2);
  clock.advance(101);
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(h.ledger.size(), 0);
  assert.equal(h.ledger.bytes(), 0);
  assert.equal((await h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'three' })).outcome, 'complete');
  await h.close();
});

test('an authenticated caller cannot dispatch to a refused guild', async () => {
  let dispatched = 0;
  const front = createFrontDoor({ authToken: TOKEN,
    authorizeGuild: async id => id === GUILD,
    controllerFor: () => { dispatched++; return null; }, health: () => ({}),
  });
  await new Promise(r => front.listen(0, '127.0.0.1', r));
  try {
    const base = `http://127.0.0.1:${front.address().port}`;
    const send = (p, body) => fetch(`${base}${p}`, { method: 'POST',
      headers: { ...AUTH, 'content-type': 'application/json' }, body: JSON.stringify(body) });
    const denied = '222222222222222222';
    const a = await send('/v1/voice/queries', { kind: 'status', params: { guild_id: denied } });
    const b = await send('/v1/voice/operations', { operation_id: 'op-1', kind: 'join', effect: { guild_id: denied } });
    assert.equal(a.status, 200);
    assert.equal((await a.json()).outcome, 'nothing_found');
    assert.equal(b.status, 403);
    assert.equal((await b.json()).outcome, 'denied');
    assert.equal(dispatched, 0);
    const good = await send('/v1/voice/queries', { kind: 'status', params: { guild_id: GUILD } });
    assert.equal((await good.json()).outcome, 'nothing_found');
    assert.equal(dispatched, 1);
  } finally { await new Promise(r => front.close(r)); }
});

test('a receipt survives pressure for its whole TTL: read back and resubmitted, it never runs twice', async () => {
  const h = await makeHarness({ ledgerOptions: { maxEntries: 4 } });
  const { generation } = await h.join();
  const effect = { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a', title: 't'.repeat(63 * 1024) };
  const played = await h.op('play', effect, { operationId: 'op-original' });
  assert.equal(played.outcome, 'complete');
  assert.ok(Buffer.byteLength(JSON.stringify(played.body)) < 12 * 1024, 'the answer stays small: the title is cut');
  for (let i = 0; i < 8; i += 1) {
    await h.op('play', { guild_id: GUILD, generation, stream_url: `https://cdn.test/p${i}` }, { operationId: `op-press-${i}` });
  }
  const readBack = await h.get('/v1/voice/operations/op-original');
  assert.equal(readBack.body.outcome, 'ok', 'still readable under pressure');
  assert.deepEqual(readBack.body.payload, played.body);
  const spawned = h.procs.length;
  const again = await h.op('play', effect, { operationId: 'op-original' });
  assert.deepEqual(again.body, played.body);
  assert.equal(h.procs.length, spawned, 'not run again');
  await h.close();
});

test('a refused request erases no receipt', async () => {
  let release;
  const gate = new Promise(r => { release = r; });
  const h = await makeHarness({ joinGate: () => gate, ledgerOptions: { maxBytes: 3900, answerBytes: 2000 } });
  const small = await h.op('stop', { guild_id: GUILD, generation: 'x' }, { operationId: 'op-small' });
  assert.equal(small.outcome, 'rejected');
  const held = h.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'op-held' });
  await until(() => h.joinCalls.length === 1);
  const refused = await h.op('stop', { guild_id: GUILD, generation: 'x' }, { operationId: 'op-refused' });
  assert.equal(refused.outcome, 'unavailable');
  assert.equal(refused.error, 'dedupe_full');
  assert.equal((await h.get('/v1/voice/operations/op-small')).body.outcome, 'ok', 'the receipt is still there');
  assert.equal((await h.get('/v1/voice/operations/op-refused')).body.outcome, 'nothing_found');
  release();
  await held;
  await h.close();
});

test('guilds each with an operation in flight leave room for a speech in a connected guild', async () => {
  // In flight at once is bounded by the listener's 64 connections, so 63
  // guilds hold one each and the 64th request is the speech.
  const HELD = 63;
  const h = await makeHarness();
  const { generation } = await h.join();
  let release;
  const gate = new Promise((r) => { release = r; });
  let held = 0;
  const front = createFrontDoor({
    authToken: TOKEN,
    controllerFor: (guildId) => (guildId === GUILD ? h.core.controller : {
      execute: async () => { held += 1; await gate; return { outcome: 'complete', state: null }; },
    }),
    health: () => ({}),
  });
  await new Promise((r) => front.listen(0, '127.0.0.1', r));
  const send = (body) => fetch(`http://127.0.0.1:${front.address().port}/v1/voice/operations`, {
    method: 'POST', headers: { ...AUTH, 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  try {
    const inFlight = [];
    for (let i = 0; i < HELD; i += 1) {
      const guild = String(100000000000000000n + BigInt(i));
      inFlight.push(send({ operation_id: `held-${i}`, kind: 'join', effect: { guild_id: guild, channel_id: 'c1' } }));
    }
    assert.ok(await until(() => held === HELD, 5000), `all ${HELD} held`);
    const speech = await send({ operation_id: 'speech', kind: 'speak',
      effect: { guild_id: GUILD, generation, audio_b64: Buffer.from('tts').toString('base64'), media_type: 'audio/mpeg' } });
    assert.equal(speech.status, 200);
    assert.equal((await speech.json()).outcome, 'complete');
    release();
    for (const res of await Promise.all(inFlight)) assert.equal(res.status, 200);
  } finally {
    release();
    front.closeAllConnections();
    await new Promise((r) => front.close(r));
    await h.close();
  }
});

test('an operation that ran always reads back', async () => {
  // Without room for one answer, the operation is refused before it runs.
  const cramped = await makeHarness({ ledgerOptions: { maxBytes: 2000 } });
  const refused = await cramped.op('join', { guild_id: GUILD, channel_id: CHANNEL }, { operationId: 'op-cramped' });
  assert.equal(refused.outcome, 'unavailable', 'no room for the answer');
  assert.equal(cramped.joinCalls.length, 0, 'refused before the effect');
  assert.equal((await cramped.get('/v1/voice/operations/op-cramped')).body.outcome, 'nothing_found', 'nothing ran');
  await cramped.close();

  // Once it ran, its answer is kept in its reservation's place, even when the
  // reservation understates it.
  const h = await makeHarness({ ledgerOptions: { maxBytes: 2000, answerBytes: 500 } });
  const { generation } = await h.join();
  const effect = { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a', title: 't'.repeat(900) };
  const played = await h.op('play', effect, { operationId: 'op-big-answer' });
  assert.equal(played.outcome, 'complete', 'play');
  assert.equal(h.procs.length, 1);
  const readBack = await h.get('/v1/voice/operations/op-big-answer');
  assert.equal(readBack.body.outcome, 'ok', 'read-back');
  assert.deepEqual(readBack.body.payload, played.body);
  const again = await h.op('play', effect, { operationId: 'op-big-answer' });
  assert.deepEqual(again.body, played.body);
  assert.equal(h.procs.length, 1, 'not run again');
  await h.close();
});
