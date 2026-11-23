// Event outbox (design/voice-channel/audio.md; the `voice` contract's
// `report`): every controller -> hub event is POSTed to NORA_EVENT_URL, the
// hub's `/v1/voice/operations`, as `{operation_id, kind: "report", effect}`
// with the event unchanged as the effect and a new `operation_id` for every
// attempt. An event the hub could not take parks in a bounded queue (<=256; on
// overflow the OLDEST is dropped and counted) and the queue is retried
// oldest-first with exponential backoff (1s -> 2s -> ... cap 60s). Every
// attempt carries a per-attempt abort timeout (10s). This retry loop is also
// the redelivery-on-reconnect mechanism: if the hub is down when a track ends,
// the event lands once it is back.
//
// event_seq is assigned at push time and is strictly increasing for the
// life of the process; the status query exposes it as last_event_seq.

import { randomUUID } from 'node:crypto';

import {
  ENVELOPE_PARTS, OPERATION_OUTCOMES, OPERATION_STATES, REPORT_STATUSES, SCOPE_LEVELS, STATUS,
} from './contract.mjs';

export const realClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (t) => clearTimeout(t),
};

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isInt = (v) => Number.isInteger(v);
const nullOr = (check) => (v) => v === null || check(v);
const isString = (v) => typeof v === 'string';

// A part holds exactly its declared fields, the required ones present, each
// of its type (the contract's OperationWhen, OperationFrom and Scope).
function exactly(value, required, optional = {}) {
  if (!isObject(value)) return false;
  const fields = { ...required, ...optional };
  return Object.keys(value).every((key) => Object.hasOwn(fields, key))
    && Object.keys(required).every((key) => Object.hasOwn(value, key))
    && Object.entries(value).every(([key, v]) => fields[key](v));
}

// The hub's acknowledgement as the contract's OperationEnvelope: the six parts
// and nothing more, each part exactly its fields.
export function isAcknowledgement(envelope) {
  return isObject(envelope)
    && Object.keys(envelope).length === ENVELOPE_PARTS.length
    && ENVELOPE_PARTS.every((part) => Object.hasOwn(envelope, part))
    && OPERATION_OUTCOMES.includes(envelope.outcome)
    && exactly(envelope.when, { state: (v) => OPERATION_STATES.includes(v) },
      { started_at_ms: nullOr(isInt), settled_at_ms: nullOr(isInt) })
    && exactly(envelope.from, { target: isString }, { platform_ref: nullOr(isString) })
    && exactly(envelope.scope, { level: (v) => SCOPE_LEVELS.includes(v) }, { id: nullOr(isString) })
    && isString(envelope.trace);
}

// A `complete` report's payload: the contract's VoiceReportPayload, exactly.
const isReportStatus = (payload) => isObject(payload)
  && Object.keys(payload).length === 1 && REPORT_STATUSES.includes(payload.status);

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

  // What the hub's answer means for this event: 'taken' (a settled operation
  // envelope with any outcome but `unavailable`), 'refused' (400, 409, 422:
  // retrying cannot help) or 'retry' (everything else: `unavailable`, 3xx,
  // 401, 404, a status that disagrees with its outcome, a read's outcome, a
  // record not yet settled, a `complete` without the hub's report status, or
  // anything that is not exactly an envelope).
  async function verdict(res) {
    const status = res?.status;
    if (status === 400 || status === 409 || status === 422) return 'refused';
    let envelope = null;
    try {
      envelope = await res.json();
    } catch {
      return 'retry';
    }
    if (!isAcknowledgement(envelope)) return 'retry';
    const { outcome, when, payload } = envelope;
    if (!OPERATION_OUTCOMES.includes(outcome) || STATUS[outcome] !== status) return 'retry';
    if (outcome === 'unavailable' || outcome === 'pending' || when.state !== 'settled') return 'retry';
    if (outcome === 'complete' && !isReportStatus(payload)) return 'retry';
    return 'taken';
  }

  async function deliver(event) {
    // Per-attempt timeout: a server that accepts the POST and never answers
    // must not stall the whole pipeline — the abort rejects like any
    // transport error and the backoff retry owns the redelivery.
    const aborter = new AbortController();
    const timeout = clock.setTimeout(() => aborter.abort(), fetchTimeoutMs);
    try {
      const res = await fetchImpl(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...headers },
        body: JSON.stringify({ operation_id: randomUUID(), kind: 'report', effect: event }),
        // A redirect is never followed: it is retried like a transport failure.
        redirect: 'manual',
        signal: aborter.signal,
      });
      const result = await verdict(res);
      if (result === 'retry') throw new Error(`hub answered ${res?.status ?? 'nothing'}`);
      return result;
    } finally {
      clock.clearTimeout(timeout);
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
          if (await deliver(event) === 'refused') {
            log('event_delivery_refused', { event_type: event.type, event_seq: event.event_seq });
          }
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
