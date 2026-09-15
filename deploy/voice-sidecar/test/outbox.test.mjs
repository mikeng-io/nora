// Outbox tests (§3.3): bounded at 256 with oldest-drop counting, backoff
// retry that delivers after the receiver comes back, unique event_ids,
// strictly increasing event_seq.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { createOutbox } from '../src/outbox.mjs';
import { createFakeClock, tick } from './helpers.mjs';

const URL = 'http://nora.test/app/v1/voice/event';

function makeOutbox({ fetchBehavior, clock = createFakeClock(), ...options } = {}) {
  const attempts = [];
  const delivered = [];
  const outbox = createOutbox({
    url: URL,
    fetchImpl: async (url, opts) => {
      const body = JSON.parse(opts.body);
      attempts.push(body);
      const res = await fetchBehavior(url, opts);
      if (res && res.status >= 200 && res.status < 300) delivered.push(body);
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
    fetchBehavior: async () => (failures-- > 0 ? { status: 503 } : { status: 200 }),
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

test('outbox is bounded: oldest event is dropped and counted on overflow', async () => {
  // A hanging fetch keeps e1 in flight and every later event queued.
  const { outbox } = makeOutbox({
    maxEvents: 3,
    fetchBehavior: () => new Promise(() => {}),
  });
  outbox.push('e1', { n: 1 }); // popped for delivery, hangs in flight
  outbox.push('e2', { n: 2 });
  outbox.push('e3', { n: 3 });
  outbox.push('e4', { n: 4 }); // queue is now full at 3
  await tick();
  assert.equal(outbox.dropped(), 0);
  outbox.push('e5', { n: 5 }); // overflows: e2 (oldest queued) is dropped
  assert.equal(outbox.dropped(), 1);
  assert.equal(outbox.pendingCount(), 3);
});

test('event_ids are unique and event_seq is strictly increasing', async () => {
  const { outbox, delivered } = makeOutbox({ fetchBehavior: async () => ({ status: 200 }) });
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
    fetchBehavior: async () => (up ? { status: 200 } : { status: 503 }),
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
      return { status: 200 };
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
