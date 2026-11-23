// The process's operation ledger (design/service-api/conventions.md
// «Operations»): one bounded in-memory map, keyed by `operation_id`, holding
// a hash of the kind and effect and the answered envelope, never audio.
//
// The first occurrence of an id reserves an in-flight entry BEFORE its handler
// runs, so a concurrent duplicate awaits the same execution. A settled
// envelope is kept for the TTL. Both kinds count toward the entry and byte
// bounds; only settled entries are evictable, so a map full of in-flight
// requests refuses the next id rather than grow.

import { createHash } from 'node:crypto';

import { realClock } from './outbox.mjs';

const TTL_MS = 5 * 60 * 1000;
const MAX_ENTRIES = 256;
const MAX_BYTES = 512 * 1024;
const SWEEP_MS = 30 * 1000;

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
  maxBytes = MAX_BYTES,
  sweepMs = SWEEP_MS,
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

  // Room for one more entry of `size`, evicting settled entries oldest-first
  // only as far as needed. False when everything held is in flight.
  function makeRoom(size) {
    const fits = () => entries.size < maxEntries && bytes + size <= maxBytes;
    for (const [id, entry] of entries) {
      if (fits()) return true;
      if (entry.expiresAt !== undefined) forget(id);
    }
    return fits();
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
      const size = Buffer.byteLength(id) + print.length + Buffer.byteLength(JSON.stringify(pending));
      if (!makeRoom(size)) return false;
      entries.set(id, { fingerprint: print, promise, pending, bytes: size });
      bytes += size;
      return true;
    },
    /** Keep the settled envelope; one that cannot fit is answered but not kept. */
    settle(id, print, envelope) {
      forget(id);
      const size = Buffer.byteLength(id) + print.length + Buffer.byteLength(JSON.stringify(envelope));
      if (!makeRoom(size)) return;
      entries.set(id, { fingerprint: print, envelope, expiresAt: clock.now() + ttlMs, bytes: size });
      bytes += size;
    },
    release: forget,
    size: () => entries.size,
    bytes: () => bytes,
    dispose: () => clearInterval(sweep),
  };
}
