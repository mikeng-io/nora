// Event outbox (design/voice-channel/07-audio-owner-controller.md §3.3):
// every controller -> nora event is POSTed to NORA_EVENT_URL. A failed
// delivery (non-2xx or transport error) parks the event in a bounded queue
// (<=256; on overflow the OLDEST is dropped and counted) and the queue is
// retried oldest-first with exponential backoff (1s -> 2s -> ... cap 60s)
// until 2xx. Every attempt carries a per-attempt abort timeout (10s) so a
// receiver that accepts and never answers is a retryable delivery failure,
// not a stalled pipeline. This retry loop is also the redelivery-on-reconnect mechanism:
// if nora is down when a track ends, the event lands once nora is back.
//
// event_seq is assigned at push time and is strictly increasing for the
// life of the process; /voice/status exposes it as last_event_seq.

import { randomUUID } from 'node:crypto';

export const realClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (t) => clearTimeout(t),
};

export function createOutbox({
  url,
  headers = {},
  fetchImpl,
  clock = realClock,
  maxEvents = 256,
  baseDelayMs = 1000,
  maxDelayMs = 60_000,
  fetchTimeoutMs = 10_000,
  log = () => {},
}) {
  let seq = 0;
  let dropped = 0;
  const pending = [];
  let draining = false;
  let inFlight = 0; // events popped for delivery but not yet settled
  let timer = null;
  let delay = baseDelayMs;

  function push(type, fields = {}) {
    const event = { type, event_id: randomUUID(), event_seq: ++seq, ...fields };
    // In-flight events count toward the bound: a delivery that fails and is
    // reinserted must never push the queue past maxEvents.
    while (pending.length + inFlight >= maxEvents && pending.length) {
      pending.shift();
      dropped += 1;
      log('outbox_overflow', { outbox_dropped: dropped });
    }
    pending.push(event);
    // A scheduled backoff timer must not be bypassed by a new push — the
    // retry fires when the timer says, not when the next event arrives.
    if (!timer) void drain();
    return event;
  }

  async function deliver(event) {
    // Per-attempt timeout: a server that accepts the POST and never answers
    // must not stall the whole pipeline — the abort rejects like any
    // transport error and the backoff retry owns the redelivery.
    const aborter = new AbortController();
    const timeout = clock.setTimeout(() => aborter.abort(), fetchTimeoutMs);
    let res;
    try {
      res = await fetchImpl(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...headers },
        body: JSON.stringify(event),
        signal: aborter.signal,
      });
    } finally {
      clock.clearTimeout(timeout);
    }
    if (!res || res.status < 200 || res.status >= 300) {
      throw new Error(`event endpoint answered ${res?.status ?? 'nothing'}`);
    }
  }

  async function drain() {
    if (draining) return;
    draining = true;
    if (timer) {
      clock.clearTimeout(timer);
      timer = null;
    }
    try {
      while (pending.length) {
        // Pop the in-flight event out of the queue so an overflow drop can
        // never remove (or reorder around) an event mid-delivery.
        const event = pending.shift();
        inFlight += 1;
        try {
          await deliver(event);
          delay = baseDelayMs;
        } catch (err) {
          pending.unshift(event); // stays oldest-first for the retry
          log('event_delivery_failed', {
            event_type: event.type,
            event_seq: event.event_seq,
            error: String(err?.message ?? err),
            retry_in_ms: delay,
          });
          timer = clock.setTimeout(() => {
            timer = null;
            void drain();
          }, delay);
          delay = Math.min(delay * 2, maxDelayMs);
          break;
        } finally {
          inFlight -= 1;
        }
      }
    } finally {
      draining = false;
    }
  }

  return {
    push,
    // Exposed for tests and for an explicit nudge after reconnect.
    flush: drain,
    lastEventSeq: () => seq,
    dropped: () => dropped,
    pendingCount: () => pending.length,
  };
}
