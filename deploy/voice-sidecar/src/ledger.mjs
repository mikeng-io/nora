// The process's operation ledger (design/service-api/conventions.md
// «Operations»): one bounded in-memory map, keyed by `operation_id`, holding
// a hash of the kind and effect and the answered envelope, never audio.
//
// The first occurrence of an id reserves an in-flight entry BEFORE its handler
// runs, so a concurrent duplicate awaits the same execution. The reservation
// charges `answerBytes`, the most an answer can hold, and the settled envelope
// replaces it in place. Nothing unexpired is ever evicted: an entry leaves only
// when its TTL passes, so a receipt stays readable for its whole five minutes.
// When the bounds are reached a NEW id is refused before it runs, and the
// refusal removes nothing.

import { createHash } from 'node:crypto';

import { realClock } from './outbox.mjs';

const TTL_MS = 5 * 60 * 1000;
const MAX_ENTRIES = 256;
const SWEEP_MS = 30 * 1000;
const ANSWER_BYTES = 12 * 1024;

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, canonical(value[k])]));
  }
  return value;
}

/** The hash an operation is compared by: its kind together with its effect. */
export function fingerprint(kind, effect) {
  return createHash('sha256').update(JSON.stringify([kind, canonical(effect)])).digest('hex');
}

export function createLedger({
  clock = realClock,
  ttlMs = TTL_MS,
  maxEntries = MAX_ENTRIES,
  sweepMs = SWEEP_MS,
  answerBytes = ANSWER_BYTES,
  // Room for every entry at its largest answer, plus its id and hash.
  maxBytes = maxEntries * (answerBytes + 256),
} = {}) {
  // id -> { fingerprint, bytes } plus in-flight { promise, pending } | settled { envelope, expiresAt }
  const entries = new Map();
  let bytes = 0;

  function forget(id) {
    const entry = entries.get(id);
    if (!entry) return;
    entries.delete(id);
    bytes -= entry.bytes;
  }

  function prune() {
    const now = clock.now();
    for (const [id, entry] of entries) {
      if (entry.expiresAt !== undefined && entry.expiresAt <= now) forget(id);
    }
  }

  // Room for one more entry of `size` once expired entries are gone. Never
  // evicts anything live.
  function hasRoom(size) {
    prune();
    return entries.size < maxEntries && bytes + size <= maxBytes;
  }

  // Expiry does not wait for traffic; unref() so the timer never holds the process.
  const sweep = setInterval(prune, sweepMs);
  sweep.unref();

  return {
    get(id) {
      prune();
      return entries.get(id);
    },
    /** Reserve `id` for an execution in flight; false when no room can be made. */
    reserve(id, print, promise, pending) {
      const size = Buffer.byteLength(id) + print.length
        + Math.max(answerBytes, Buffer.byteLength(JSON.stringify(pending)));
      if (!hasRoom(size)) return false;
      entries.set(id, { fingerprint: print, promise, pending, bytes: size });
      bytes += size;
      return true;
    },
    /** Keep the settled envelope in its reservation's place; it is always kept. */
    settle(id, print, envelope) {
      forget(id);
      const size = Buffer.byteLength(id) + print.length + Buffer.byteLength(JSON.stringify(envelope));
      entries.set(id, { fingerprint: print, envelope, expiresAt: clock.now() + ttlMs, bytes: size });
      bytes += size;
    },
    release: forget,
    size: () => entries.size,
    bytes: () => bytes,
    dispose: () => clearInterval(sweep),
  };
}
