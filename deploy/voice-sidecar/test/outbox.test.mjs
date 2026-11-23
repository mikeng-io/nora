// Outbox tests (design/voice-channel/audio.md «Connection lifecycle»; the
// `voice` contract's `report`): bounded at 256 with oldest-drop counting,
// backoff retry that delivers after the hub comes back, a retry only when the
// hub could not take the report, unique event_ids, strictly increasing
// event_seq.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { createOutbox } from '../src/outbox.mjs';
import { EVENT_URL as URL, createFakeClock, hubAnswer, tick } from './helpers.mjs';

function makeOutbox({ fetchBehavior, clock = createFakeClock(), ...options } = {}) {
  const attempts = [];
  const delivered = [];
  const outbox = createOutbox({
    url: URL,
    fetchImpl: async (url, opts) => {
      const body = JSON.parse(opts.body);
      attempts.push({ ...body, redirect: opts.redirect });
      const res = await fetchBehavior(url, opts);
      if (res && res.status >= 200 && res.status < 300) delivered.push(body.effect);
      return res;
    },
    clock,
    ...options,
  });
  return { outbox, attempts, delivered, clock };
}

test('failed delivery is retried with exponential backoff until 2xx', async () => {
  const clock = createFakeClock();
  let failures = 2;
  const { outbox, attempts, delivered } = makeOutbox({
    clock,
    baseDelayMs: 1000,
    fetchBehavior: async () => (failures-- > 0 ? { status: 503 } : hubAnswer('complete')),
  });
  outbox.push('track-finished', { playback_id: 'p1' });
  await tick();
  assert.equal(attempts.length, 1); // immediate attempt failed
  assert.equal(delivered.length, 0);
  assert.equal(outbox.pendingCount(), 1);

  await clock.advance(999);
  await tick();
  assert.equal(attempts.length, 1); // backoff has not elapsed
  await clock.advance(1);
  await tick();
  await tick();
  assert.equal(attempts.length, 2); // first retry (1s) also failed
  assert.equal(delivered.length, 0);

  await clock.advance(2000); // second retry (2s) succeeds
  await tick();
  await tick();
  assert.equal(attempts.length, 3);
  assert.equal(delivered.length, 1);
  assert.equal(outbox.pendingCount(), 0);
  assert.equal(delivered[0].playback_id, 'p1');
});

test('backoff is capped at the max delay', async () => {
  const clock = createFakeClock();
  const calls = [];
  const { outbox } = makeOutbox({
    clock,
    baseDelayMs: 1000,
    maxDelayMs: 4_000,
    fetchBehavior: async () => {
      calls.push(clock.now());
      return { status: 503 };
    },
  });
  outbox.push('voice-death', {});
  await tick();
  // Attempt times: t0, +1s, +2s, +4s, +4s (capped).
  for (const step of [1000, 2000, 4000, 4000]) {
    clock.advance(step);
    await tick();
    await tick();
  }
  assert.equal(calls.length, 5);
  const gaps = calls.slice(1).map((t, i) => t - calls[i]);
  assert.deepEqual(gaps, [1000, 2000, 4000, 4000]);
});

test('outbox is bounded: in-flight events count toward capacity, oldest queued is dropped', async () => {
  // A hanging fetch keeps e1 in flight and every later event queued.
  const { outbox } = makeOutbox({
    maxEvents: 3,
    fetchBehavior: () => new Promise(() => {}),
  });
  outbox.push('e1', { n: 1 }); // popped for delivery, hangs in flight
  outbox.push('e2', { n: 2 });
  outbox.push('e3', { n: 3 }); // e1 (in flight) + e2 + e3 = at the bound of 3
  await tick();
  assert.equal(outbox.dropped(), 0);
  outbox.push('e4', { n: 4 }); // overflows: e2 (oldest queued) is dropped
  assert.equal(outbox.dropped(), 1);
  assert.equal(outbox.pendingCount(), 2);
  outbox.push('e5', { n: 5 }); // overflows again: e3 is dropped
  assert.equal(outbox.dropped(), 2);
  assert.equal(outbox.pendingCount(), 2);
});

test('a failed delivery reinserted into a full queue never exceeds the bound', async () => {
  const clock = createFakeClock();
  let fail = true;
  const { outbox } = makeOutbox({
    clock,
    maxEvents: 3,
    baseDelayMs: 1000,
    fetchBehavior: async () => (fail ? { status: 503 } : hubAnswer('complete')),
  });
  outbox.push('e1', { n: 1 }); // attempted, fails, reinserted
  await tick();
  assert.equal(outbox.pendingCount(), 1);
  outbox.push('e2', { n: 2 });
  outbox.push('e3', { n: 3 }); // e1..e3 = 3, at the bound
  outbox.push('e4', { n: 4 }); // overflow drops the OLDEST (e1), queue stays at 3
  assert.equal(outbox.dropped(), 1);
  assert.equal(outbox.pendingCount(), 3); // never 4 with a bound of 3
  fail = false;
  clock.advance(1000);
  await tick();
  await tick();
  assert.equal(outbox.pendingCount(), 0);
});

test('a new push does not bypass a scheduled backoff retry', async () => {
  const clock = createFakeClock();
  let failures = 1;
  const { outbox, attempts, delivered } = makeOutbox({
    clock,
    baseDelayMs: 1000,
    fetchBehavior: async () => (failures-- > 0 ? { status: 503 } : hubAnswer('complete')),
  });
  outbox.push('e1', { n: 1 });
  await tick();
  assert.equal(attempts.length, 1); // failed; backoff of 1s is scheduled

  outbox.push('e2', { n: 2 }); // must NOT trigger an immediate retry
  await tick();
  await tick();
  assert.equal(attempts.length, 1);

  clock.advance(1000); // the scheduled retry fires and drains both, in order
  await tick();
  await tick();
  assert.equal(attempts.length, 3);
  assert.deepEqual(
    delivered.map((e) => e.type),
    ['e1', 'e2'],
  );
});

test('event_ids are unique and event_seq is strictly increasing', async () => {
  const { outbox, delivered } = makeOutbox({ fetchBehavior: async () => (hubAnswer('complete')) });
  outbox.push('a', {});
  outbox.push('b', {});
  outbox.push('c', {});
  await tick();
  await tick();
  assert.equal(delivered.length, 3);
  assert.deepEqual(
    delivered.map((e) => e.event_seq),
    [1, 2, 3],
  );
  assert.equal(new Set(delivered.map((e) => e.event_id)).size, 3);
  assert.equal(outbox.lastEventSeq(), 3);
});

test('events land once the receiver comes back (redelivery-on-reconnect)', async () => {
  const clock = createFakeClock();
  let up = false;
  const { outbox, delivered } = makeOutbox({
    clock,
    fetchBehavior: async () => (up ? hubAnswer('complete') : { status: 503 }),
  });
  outbox.push('track-finished', { playback_id: 'p1' });
  await tick();
  assert.equal(delivered.length, 0);
  up = true;
  clock.advance(1000);
  await tick();
  await tick();
  assert.equal(delivered.length, 1);
  assert.equal(delivered[0].type, 'track-finished');
});

test('a transport error (fetch rejects) is retained and retried, not lost', async () => {
  const clock = createFakeClock();
  let down = true;
  const { outbox, delivered } = makeOutbox({
    clock,
    fetchBehavior: async () => {
      if (down) throw new Error('connection refused');
      return hubAnswer('complete');
    },
  });
  outbox.push('channel-emptied', { channel_id: 'c1' });
  await tick();
  assert.equal(outbox.pendingCount(), 1);
  down = false;
  clock.advance(1000);
  await tick();
  await tick();
  assert.equal(delivered.length, 1);
});

test('a fetch that never answers is aborted after the attempt timeout and retried, never hung', async () => {
  // 2026-09-16 audit N-6: backoff only covered rejections/non-2xx; a server
  // that accepts and holds the socket stalled the whole event pipeline.
  const clock = createFakeClock();
  let calls = 0;
  const { outbox, attempts, delivered } = makeOutbox({
    clock,
    fetchTimeoutMs: 5_000,
    baseDelayMs: 1000,
    fetchBehavior: (url, opts) => {
      calls += 1;
      if (calls === 1) {
        // Accepts and never answers; rejects only when the attempt is aborted.
        return new Promise((_, reject) => {
          opts.signal.addEventListener('abort', () => reject(new Error('The operation was aborted')));
        });
      }
      return Promise.resolve(hubAnswer('complete'));
    },
  });
  outbox.push('track-finished', { playback_id: 'p1' });
  await tick();
  assert.equal(attempts.length, 1);
  assert.equal(delivered.length, 0);

  clock.advance(5_000); // the per-attempt timeout fires the abort
  await tick();
  await tick();
  assert.equal(delivered.length, 0);
  assert.equal(outbox.pendingCount(), 1); // parked for the backoff retry

  clock.advance(1000); // backoff elapses; the retry succeeds
  await tick();
  await tick();
  assert.equal(attempts.length, 2);
  assert.deepEqual(
    delivered.map((e) => e.type),
    ['track-finished'],
  );
});

test('a voice-death pushed while a delivery is in flight is serialized behind it — never concurrent, never lost, never reordered', async () => {
  // Teardown under contention (2026-09-16 audit coverage): the connection
  // dies while the outbox is mid-POST. The death event must wait for the
  // in-flight delivery, then land exactly once, in seq order.
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let hanging = true;
  const { outbox, attempts, delivered } = makeOutbox({
    fetchBehavior: async () => {
      if (hanging) await gate;
      return hubAnswer('complete');
    },
  });
  outbox.push('track-finished', { playback_id: 'p1' }); // popped, hangs in flight
  await tick();
  assert.equal(attempts.length, 1);
  outbox.push('voice-death', { reason: 'stream-stall' }); // pushed mid-delivery
  await tick();
  await tick();
  assert.equal(attempts.length, 1); // no concurrent second attempt
  hanging = false;
  release();
  await tick();
  await tick();
  assert.deepEqual(
    delivered.map((e) => e.type),
    ['track-finished', 'voice-death'],
  );
  assert.ok(delivered[1].event_seq > delivered[0].event_seq);
  assert.equal(outbox.pendingCount(), 0);
});

test('configured headers (the shared bearer) are sent on every event POST', async () => {
  const seen = [];
  const { outbox } = makeOutbox({
    headers: { authorization: 'Bearer test-token' },
    fetchBehavior: async (url, opts) => {
      seen.push(opts.headers);
      return hubAnswer('complete');
    },
  });
  outbox.push('track-finished', { playback_id: 'p1' });
  await tick();
  assert.equal(seen.length, 1);
  assert.equal(seen[0].authorization, 'Bearer test-token');
  assert.equal(seen[0]['content-type'], 'application/json');
});

test('reports are retried only when the hub could not take them', async () => {
  // One event, one first answer, then a hub that takes it. A retried report
  // is a new operation carrying the same event.
  async function firstAnswerIs(answer) {
    const clock = createFakeClock();
    let calls = 0;
    const { outbox, attempts } = makeOutbox({
      clock,
      baseDelayMs: 1000,
      fetchTimeoutMs: 5000,
      fetchBehavior: async (url, opts) => {
        calls += 1;
        if (calls > 1) return hubAnswer('complete');
        if (answer === 'transport') throw new Error('connection refused');
        if (answer === 'timeout') {
          return new Promise((_, reject) => opts.signal.addEventListener('abort', () => reject(new Error('aborted'))));
        }
        return answer;
      },
    });
    outbox.push('track-finished', { playback_id: 'p1' });
    await tick();
    clock.advance(5000); // the attempt timeout, for the one answer that needs it
    await tick();
    await tick();
    clock.advance(60_000); // any backoff
    await tick();
    await tick();
    return { outbox, attempts };
  }
  const envelopeOf = (answer) => answer.envelope;
  const unparseable = { status: 200, json: async () => { throw new SyntaxError('not json'); } };
  const retried = {
    unavailable: hubAnswer('unavailable'),
    transport: 'transport',
    timeout: 'timeout',
    redirect: { status: 302, json: async () => ({}) },
    unauthorized: { status: 401, json: async () => ({ error: 'unauthorized' }) },
    'no route': { status: 404, json: async () => ({ error: 'not_found' }) },
    'status disagrees': hubAnswer('complete', { status: 503 }),
    'outcome disagrees': hubAnswer('unavailable', { status: 200 }),
    'no envelope': unparseable,
    'not settled': hubAnswer('pending'),
    'a read outcome': hubAnswer('ok'),
    'a bare outcome': { status: 200, json: async () => ({ outcome: 'complete' }) },
    'settled but missing parts': { status: 200, json: async () => ({ outcome: 'complete', when: { state: 'settled' } }) },
    'complete but still started': { status: 200, json: async () => ({
      ...envelopeOf(hubAnswer('complete')), when: { state: 'started', started_at_ms: 1, settled_at_ms: null } }) },
  };
  for (const [label, answer] of Object.entries(retried)) {
    const { outbox, attempts } = await firstAnswerIs(answer);
    assert.equal(attempts.length, 2, label);
    assert.equal(outbox.pendingCount(), 0, label);
    const [a, b] = attempts;
    for (const attempt of attempts) {
      assert.equal(attempt.kind, 'report', label);
      assert.equal(attempt.redirect, 'manual', label);
      assert.equal(typeof attempt.operation_id, 'string', label);
    }
    assert.notEqual(a.operation_id, b.operation_id, `${label}: a new operation_id`);
    assert.equal(a.effect.event_id, b.effect.event_id, `${label}: the same event_id`);
    assert.equal(a.effect.event_seq, b.effect.event_seq, `${label}: the same event_seq`);
    assert.deepEqual(a.effect, b.effect, `${label}: the event unchanged`);
  }
  const ended = {
    complete: hubAnswer('complete'),
    denied: hubAnswer('denied'),
    rejected: hubAnswer('rejected'),
    unsupported: hubAnswer('unsupported'),
    uncertain: hubAnswer('uncertain'),
    incomplete: hubAnswer('incomplete'),
    'bad request': { status: 400, json: async () => ({ error: 'invalid_body' }) },
    conflict: { status: 409, json: async () => ({ error: 'operation_id_reused' }) },
    'unknown kind': { status: 422, json: async () => ({ error: 'unknown_kind' }) },
  };
  for (const [label, answer] of Object.entries(ended)) {
    const { outbox, attempts } = await firstAnswerIs(answer);
    assert.equal(attempts.length, 1, `${label}: not retried`);
    assert.equal(outbox.pendingCount(), 0, label);
    assert.equal(attempts[0].effect.type, 'track-finished', label);
    assert.equal(attempts[0].effect.playback_id, 'p1', label);
  }

  // Order holds across a refusal and a retry: e1 refused, e2 retried, e3 after it.
  const clock = createFakeClock();
  const answers = [{ status: 400, json: async () => ({}) }, hubAnswer('unavailable')];
  const { outbox, attempts, delivered } = makeOutbox({
    clock,
    fetchBehavior: async () => answers.shift() ?? hubAnswer('complete'),
  });
  outbox.push('e1', {});
  outbox.push('e2', {});
  outbox.push('e3', {});
  await tick();
  await tick();
  clock.advance(1000);
  await tick();
  await tick();
  assert.deepEqual(attempts.map((a) => a.effect.type), ['e1', 'e2', 'e2', 'e3']);
  assert.deepEqual(delivered.map((e) => e.event_seq), [2, 3]);

  // The default bound is 256, in-flight included.
  const bounded = makeOutbox({ fetchBehavior: () => new Promise(() => {}) }).outbox;
  for (let i = 0; i < 300; i += 1) bounded.push('e', { n: i });
  await tick();
  assert.equal(bounded.dropped(), 300 - 256);
  assert.equal(bounded.pendingCount(), 255);
});
