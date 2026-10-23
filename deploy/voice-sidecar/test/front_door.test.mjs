// The control plane's perimeter (security(voice), Aikido round 2): the shared
// bearer on every route, a bounded request_id dedupe map that expires on a
// timer, over-limit bodies answered and closed unread, and listener limits.
//
// Falsification: drop the bearer check in createFrontDoor and the auth matrix
// goes green-to-red on every route; drop makeRoom and the budget tests see the
// map grow past its bound; drop the sweep interval and the expiry test never
// sees the memo go; restore the drain in readBody and the unread-body test
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
  const calls = [
    ['POST', '/voice/join', JSON.stringify({ guild_id: GUILD, channel_id: CHANNEL })],
    ['POST', '/music/stop', JSON.stringify({ guild_id: GUILD, generation: 'x' })],
    ['POST', '/stream', JSON.stringify({ guild_id: GUILD, generation: 'x', audio_b64: 'AAA=' })],
    ['GET', `/voice/status?guild_id=${GUILD}`, undefined],
    ['GET', '/health', undefined],
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

test('a request_id must be a short ASCII token; anything else is refused before dedupe', async () => {
  const h = await makeHarness();
  const bad = ['x'.repeat(65), 'a/../b', 'has space', 123, ''];
  for (const id of bad) {
    const res = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id: id });
    assert.equal(res.status, 400, `request_id ${JSON.stringify(id)}`);
    assert.equal(res.body.error, 'invalid_request_id');
  }
  assert.equal(h.core.controller.dedupeSize(), 0, 'a refused id takes no dedupe slot');
  const good = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id: 'req-1' });
  assert.equal(good.status, 200);
  await h.close();
});

test('the dedupe map holds at most its entry budget, evicting completed entries oldest-first', async () => {
  const h = await makeHarness({ controllerOptions: { dedupeMaxEntries: 4 } });
  for (let i = 0; i < 10; i += 1) {
    // generation 'x' never matches, so each answers 409 — a completed, memoized answer.
    const res = await h.post('/music/stop', { guild_id: GUILD, generation: 'x', request_id: `flood-${i}` });
    assert.equal(res.status, 409);
    assert.ok(h.core.controller.dedupeSize() <= 4, `size ${h.core.controller.dedupeSize()} after ${i + 1}`);
  }
  assert.equal(h.core.controller.dedupeSize(), 4);
  await h.close();
});

test('the dedupe map holds at most its byte budget, counting the memoized bodies', async () => {
  const budget = 1500;
  const h = await makeHarness({ controllerOptions: { dedupeMaxBytes: budget } });
  for (let i = 0; i < 10; i += 1) {
    await h.post('/music/stop', { guild_id: GUILD, generation: 'x', request_id: `bytes-${i}` });
    assert.ok(h.core.controller.dedupeBytes() <= budget, `bytes ${h.core.controller.dedupeBytes()}`);
  }
  await h.close();
});

test('an in-flight entry is never evicted: at the budget with everything in flight, the next id is refused 503', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const h = await makeHarness({ joinGate: () => gate, controllerOptions: { dedupeMaxEntries: 1 } });
  const first = h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id: 'held' });
  await until(() => h.joinCalls.length === 1);
  const refused = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id: 'other' });
  assert.equal(refused.status, 503);
  assert.equal(refused.body.error, 'dedupe_full');
  release();
  const done = await first;
  assert.equal(done.status, 200, 'the held request still completes');
  await h.close();
});

test('an expired memo is dropped by the timer, with no further traffic', async () => {
  const clock = createFakeClock();
  const h = await makeHarness({
    clock,
    controllerOptions: { dedupeTtlMs: 1000, dedupeSweepMs: 5 },
  });
  await h.post('/music/stop', { guild_id: GUILD, generation: 'x', request_id: 'expiring' });
  assert.equal(h.core.controller.dedupeSize(), 1);
  clock.advance(5000);
  assert.equal(
    await until(() => h.core.controller.dedupeSize() === 0, 400),
    true,
    'the sweep timer, not a later request, must release the memo',
  );
  await h.close();
});

test('an over-limit body is answered 413 and the connection closed without reading the rest', async () => {
  const h = await makeHarness();
  const head = [
    'POST /music/stop HTTP/1.1',
    'host: x',
    `authorization: Bearer ${TOKEN}`,
    'content-type: application/json',
    'content-length: 10000000', // far over the 64 KiB control limit
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
    'POST /music/stop HTTP/1.1',
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


test('request ids have a bounded ASCII format', async () => {
  const h = await makeHarness();
  for (const request_id of ['x'.repeat(65), 'space here', 'é', 42, '']) {
    const r = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id });
    assert.equal(r.status, 400);
    assert.equal(r.body.error, 'invalid_request_id');
  }
  assert.equal(h.joinCalls.length, 0);
  await h.close();
});

test('dedupe caps in-flight entries and bytes before executing', async () => {
  let release;
  const gate = new Promise(r => { release = r; });
  const h = await makeHarness({ joinGate: () => gate,
    controllerOptions: { dedupeMaxEntries: 1, dedupeMaxBytes: 512 } });
  const first = h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id: 'first' });
  await until(() => h.joinCalls.length === 1);
  const r = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id: 'second' });
  assert.equal(r.status, 503);
  assert.equal(h.core.controller.dedupeSize(), 1);
  assert.ok(h.core.controller.dedupeBytes() > 'first'.length, 'the retained body counts too');
  release();
  await first;
  const big = await h.post('/music/stop', { guild_id: GUILD, request_id: 'large', padding: 'x'.repeat(600) });
  assert.equal(big.status, 503, 'an individual body cannot exceed the byte budget');
  await h.close();
});

test('completed dedupe entries evict and expire without new requests', async () => {
  const clock = createFakeClock();
  const h = await makeHarness({ clock, controllerOptions: {
    dedupeMaxEntries: 2, dedupeMaxBytes: 4096, dedupeTtlMs: 100, dedupeSweepMs: 10,
  } });
  for (const request_id of ['one', 'two', 'three']) {
    const r = await h.post('/voice/join', { guild_id: GUILD, channel_id: CHANNEL, request_id });
    assert.equal(r.status, 200);
    assert.ok(h.core.controller.dedupeSize() <= 2);
    assert.ok(h.core.controller.dedupeBytes() <= 4096);
  }
  assert.equal(h.core.controller.dedupeSize(), 2);
  clock.advance(101);
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(h.core.controller.dedupeSize(), 0);
  assert.equal(h.core.controller.dedupeBytes(), 0);
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
    const denied = '222222222222222222';
    const a = await fetch(`${base}/voice/status?guild_id=${denied}`, { headers: AUTH });
    const b = await fetch(`${base}/voice/join`, { method: 'POST', headers: { ...AUTH,
      'content-type': 'application/json' }, body: JSON.stringify({ guild_id: denied }) });
    assert.equal(a.status, 403);
    assert.equal(b.status, 403);
    assert.equal(dispatched, 0);
    const good = await fetch(`${base}/voice/status?guild_id=${GUILD}`, { headers: AUTH });
    assert.equal(good.status, 404);
    assert.equal(dispatched, 1);
  } finally { await new Promise(r => front.close(r)); }
});
