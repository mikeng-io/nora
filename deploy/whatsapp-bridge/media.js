/**
 * Bridge-owned media custody — a durable, content-bound spool on the same
 * volume class as the journal (`NORA_BRIDGE_ATTACH_DIR`, defaulting beside the
 * journal file so one durable mount serves both).
 *
 * - Outbound: `PUT /v1/attachments/{key}` stores raw bytes before the action
 *   that references `key` executes. Identical bytes under the same key are
 *   idempotent; a DIFFERENT body under a taken key is refused (a custody key
 *   is immutable — the wire's "conflicting hash refused").
 * - Inbound: WhatsApp media downloaded at capture lands under a
 *   `sha256[:32]` content key, which becomes the envelope `attachments[].key`
 *   and `media[].platform_id` the app later GETs to copy into its own
 *   content-addressed store.
 * - Bounded: a single body cap; the route answers 413 over it.
 *
 * v1 has no release route — pins live for the volume's life. Key charset is
 * `{1,128}` of `[A-Za-z0-9._~:-]` (covers the client's `out-{action_id}-{i}`
 * form and content hashes); anything else is a 400, never a path.
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';

export const MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024;
const KEY_RE = /^[A-Za-z0-9._~:-]{1,128}$/;

export function validAttachmentKey(key) {
  return typeof key === 'string' && KEY_RE.test(key) && !key.includes('..');
}

export class AttachmentStore {
  constructor(dir, { maxBytes = MAX_ATTACHMENT_BYTES } = {}) {
    this.dir = path.resolve(dir);
    this.maxBytes = maxBytes;
    mkdirSync(this.dir, { recursive: true });
  }

  _path(key) { return path.join(this.dir, key); }

  has(key) { return validAttachmentKey(key) && existsSync(this._path(key)); }

  read(key) {
    // Reads are key-validated too — never join an untrusted path segment.
    if (!validAttachmentKey(key)) return null;
    try { return readFileSync(this._path(key)); } catch { return null; }
  }

  /**
   * Store `data` under `key`. Returns 'created' | 'identical' | 'conflict'.
   * Atomic: tmp file + rename, so a reader never sees a half body.
   */
  put(key, data) {
    if (!validAttachmentKey(key)) throw new Error('invalid attachment key');
    if (data.length > this.maxBytes) throw new Error('attachment over bound');
    const target = this._path(key);
    if (existsSync(target)) {
      return readFileSync(target).equals(data) ? 'identical' : 'conflict';
    }
    const tmp = `${target}.tmp-${process.pid}-${Math.random().toString(36).slice(2)}`;
    writeFileSync(tmp, data);
    renameSync(tmp, target);
    return 'created';
  }

  /**
   * Inbound custody: store downloaded media under its content key
   * (`sha256[:32]`, same scheme as the signal bridge). Returns
   * `{key, size}` for the envelope's `attachments[]`.
   */
  putInbound(data) {
    const key = createHash('sha256').update(data).digest('hex').slice(0, 32);
    this.put(key, data);
    return { key, size: data.length };
  }

  bytes() {
    try { return statSync(this.dir).size; } catch { return 0; }
  }
}
