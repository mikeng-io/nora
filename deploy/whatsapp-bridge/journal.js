/**
 * The bridge capture journal: an append-only fsync'd spool of records —
 * the JS mirror of `nora/platform/bridge/journal.py`, same byte format.
 *
 * Every accepted upstream transition lands here BEFORE any Redis enqueue, so
 * a bridge restart replays the tail instead of losing or re-deriving events.
 * One record per line: `{length} {crc32} {json}` — length-prefixed so a torn
 * tail record (the acknowledged crash window) truncates cleanly on recover
 * instead of poisoning the spool.
 *
 * Record shapes (the `envelope` key matches the sibling bridges' shared
 * spool; the marker keys are this bridge's own):
 *   {probe:true}                             preflight marker
 *   {envelope:{...}, react?:<react request>} a journaled event awaiting
 *                                            enqueue; `react` is the queued-
 *                                            receipt request to attempt once
 *                                            the enqueue is durable
 *   {enqueued:{event_id, stream_id,          enqueue confirmation — replay
 *              orig_event_id}}               skips confirmed captures;
 *                                            orig_event_id is the alias the
 *                                            stream entry was first queued
 *                                            under (differs from event_id
 *                                            only across key rotation) and
 *                                            owns the queued receipt/action
 *   {receipted:{event_id, action_id,         a settled queued receipt — the
 *              state}}                       bridge_action outcome (confirmed/
 *                                            rejected/uncertain/started) is
 *                                            durable, so replay never retries
 *                                            a receipt that ran; absent here
 *                                            means the receipt is still owed
 *   {ambiguous:{...}}                        a transition we could see but not
 *                                            interpret (undecryptable secret
 *                                            edit): retained for diagnosis,
 *                                            never queued
 *
 * The journal is also the capture preflight: `probe()` must succeed — create
 * dir, open, append+fsync — BEFORE upstream receive is enabled on every
 * restart. A JournalError anywhere is a broken durable capture: the bridge
 * halts upstream and exits, it never drops the oldest.
 */
import { closeSync, existsSync, fstatSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, truncateSync, writeSync } from 'node:fs';
import path from 'node:path';
import { crc32 } from 'node:zlib';

export class JournalError extends Error {
  constructor(message) {
    super(message);
    this.name = 'JournalError';
  }
}

const DEFAULT_MAX_BYTES = 512 * 1024 * 1024;

function encodeRecord(record) {
  const payload = Buffer.from(JSON.stringify(record), 'utf8');
  const head = Buffer.from(`${payload.length} ${crc32(payload)} `, 'utf8');
  return Buffer.concat([head, payload, Buffer.from('\n')]);
}

export class Journal {
  constructor(filePath, { maxBytes = DEFAULT_MAX_BYTES } = {}) {
    this.path = path.resolve(filePath);
    this.maxBytes = maxBytes;
  }

  /** Preflight: prove append+fsync works. Called before upstream starts. */
  probe() {
    mkdirSync(path.dirname(this.path), { recursive: true });
    this.append({ probe: true });
  }

  append(record) {
    const line = encodeRecord(record);
    try {
      const fd = openSync(this.path, 'a');
      try {
        if (fstatSync(fd).size + line.length > this.maxBytes) {
          throw new JournalError(`journal ${this.path} over capacity ${this.maxBytes}`);
        }
        writeSync(fd, line);
        fsyncSync(fd);
      } finally {
        closeSync(fd);
      }
    } catch (err) {
      if (err instanceof JournalError) throw err;
      throw new JournalError(`journal append failed: ${err.message}`);
    }
  }

  /**
   * Read all intact records. Two failure shapes are NOT the same:
   * - TORN TAIL: the last line has no newline, or fails its length/CRC check
   *   and nothing follows it — the acknowledged crash window of append+fsync;
   *   the bytes past `intactEnd` are truncated and replay continues from the
   *   intact prefix.
   * - INTERIOR CORRUPTION: a malformed line with more data after it. A crashed
   *   writer can only tear at the end, so corruption followed by valid bytes
   *   is disk/operator damage, not a torn write. Refusing to continue is the
   *   honest answer: raises JournalError.
   */
  recover() {
    if (!existsSync(this.path)) return [];
    const data = readFileSync(this.path);
    const records = [];
    let pos = 0;
    let intactEnd = 0;
    while (pos < data.length) {
      const nl = data.indexOf(0x0a, pos);
      if (nl < 0) break;                       // torn tail — unterminated line
      const line = data.subarray(pos, nl);
      const first = line.indexOf(0x20);
      const second = first < 0 ? -1 : line.indexOf(0x20, first + 1);
      let ok = false;
      let payload = null;
      if (first > 0 && second > 0) {
        const wantLen = Number(line.subarray(0, first).toString('ascii'));
        const wantCrc = Number(line.subarray(first + 1, second).toString('ascii'));
        payload = line.subarray(second + 1);
        ok = Number.isInteger(wantLen) && Number.isInteger(wantCrc)
          && wantLen === payload.length && wantCrc === crc32(payload);
      }
      if (!ok) {
        if (nl + 1 >= data.length) break;      // malformed LAST line — torn tail
        throw new JournalError(
          `journal ${this.path}: interior corruption at byte ${pos}; refusing to replay past it`,
        );
      }
      records.push(JSON.parse(payload.toString('utf8')));
      pos = nl + 1;
      intactEnd = pos;
    }
    if (intactEnd < data.length) {
      truncateSync(this.path, intactEnd);
    }
    return records;
  }

  /**
   * Ordered drain plan over `records` (journal order = publish order):
   * `work` holds one entry per envelope record still owed something —
   * `enqueued: null` means the stream publish is pending; a record carrying
   * `react` whose `enqueued` marker exists but has no `receipted` marker is
   * receipt-pending (the crash window between enqueue confirmation and the
   * receipt landing). A record needing enqueue surfaces its receipt too,
   * handled in the same drain pass after the publish lands.
   */
  plan(records) {
    const enqueued = new Map();
    const receipted = new Set();
    for (const r of records) {
      const e = r && r.enqueued && r.enqueued.event_id;
      if (typeof e === 'string') enqueued.set(e, r.enqueued);
      const d = r && r.receipted && r.receipted.event_id;
      if (typeof d === 'string') receipted.add(d);
    }
    const work = [];
    let pendingEnqueues = 0;
    let pendingReceipts = 0;
    for (const r of records) {
      if (!r || !r.envelope) continue;
      const id = r.envelope.event_id;
      const mark = enqueued.get(id) || null;
      const owesEnqueue = !mark;
      const owesReceipt = !!r.react && !receipted.has(id);
      if (owesEnqueue) pendingEnqueues += 1;
      if (owesReceipt) pendingReceipts += 1;
      if (owesEnqueue || (owesReceipt && mark)) {
        work.push({ envelope: r.envelope, react: r.react || null, enqueued: mark });
      }
    }
    return { records: records.length, work, pendingEnqueues, pendingReceipts };
  }

  /**
   * The compact keep-set: retained `ambiguous` diagnostics plus every record
   * still owed work and the markers those pending records own. A pending
   * receipt keeps its `enqueued` marker (its orig_event_id is the action
   * owner); fully drained captures, probes and settled markers drop.
   */
  compactable(records) {
    const pendingIds = new Set(this.plan(records).work.map((w) => w.envelope.event_id));
    return records.filter((r) => {
      if (r && r.ambiguous) return true;
      if (r && r.envelope) return pendingIds.has(r.envelope.event_id);
      const id = (r && r.enqueued && r.enqueued.event_id)
        || (r && r.receipted && r.receipted.event_id);
      return typeof id === 'string' && pendingIds.has(id);
    });
  }

  /**
   * Atomic rewrite keeping only `keep` records: write tmp, fsync, rename,
   * fsync the directory. Called once the pending set is fully drained so the
   * spool does not grow without bound across long runs.
   */
  compact(keep) {
    const tmp = `${this.path}.compact`;
    const fd = openSync(tmp, 'w');
    try {
      for (const record of keep) writeSync(fd, encodeRecord(record));
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    renameSync(tmp, this.path);
    try {
      const dir = openSync(path.dirname(this.path), 'r');
      try { fsyncSync(dir); } finally { closeSync(dir); }
    } catch { /* directory fsync is best-effort on some filesystems */ }
  }
}
