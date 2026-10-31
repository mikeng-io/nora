/**
 * Redis Streams producer contract — the JS mirror of
 * `nora/platform/bridge/queue.py`. The Lua script is THE contract and is
 * duplicated here deliberately, byte-for-byte: a drifted copy fails the
 * parity test (`wire.test` asserts its sha1 against the frozen value).
 *
 * Dedupe is source-key-first, then by `event_id` (the versioned HMAC
 * alias). `nq:src:{stream}:{source_key}` holds the FIRST alias that ever
 * published this source identity. The script returns TWO values —
 * `{stream_id, canonical_event_id}` — so a re-captured transition signed
 * under a rotated key resolves to the original stream id AND the original
 * alias: callers mint receipts/actions on the canonical id, never the new
 * envelope's, or the one queued event would grow a second action owner.
 */
import { createHash } from 'node:crypto';

// KEYS[1] = stream, KEYS[2] = nq:src:{stream}:{source_key},
// KEYS[3] = 'nq:dedupe:' prefix, KEYS[4] = nq:dedupe:{event_id};
// ARGV[1] = envelope JSON, ARGV[2] = event_id.
// Exactly the bytes nora/platform/bridge/queue.py holds.
export const ENQUEUE_LUA = `local orig = redis.call('GET', KEYS[2])
if orig then
    local prior = redis.call('GET', KEYS[3] .. orig)
    if prior then return {prior, orig} end
end
local prior = redis.call('GET', KEYS[4])
if prior then
    redis.call('SET', KEYS[2], ARGV[2])
    return {prior, ARGV[2]}
end
local id = redis.call('XADD', KEYS[1], '*', 'e', ARGV[1])
redis.call('SET', KEYS[4], id)
redis.call('SET', KEYS[2], ARGV[2])
return {id, ARGV[2]}`;

export const ENQUEUE_LUA_SHA1 = createHash('sha1').update(ENQUEUE_LUA, 'utf8').digest('hex');
export const CONSUMER_GROUP = 'nora-v1';
export const DEDUPE_PREFIX = 'nq:dedupe:';

export function streamName(platform, accountKey) {
  return `nq:in:${platform}:${accountKey}`;
}

export function dedupeKey(eventId) {
  return `${DEDUPE_PREFIX}${eventId}`;
}

export function sourceKey(stream, sourceKey_) {
  return `nq:src:${stream}:${sourceKey_}`;
}

export class StreamProducer {
  /** `client` is a connected node-redis client; `stream` an `nq:in:*` name. */
  constructor(client, stream) {
    this.client = client;
    this.stream = stream;
  }

  /**
   * Returns `{streamId, eventId}` — the stream position and the CANONICAL
   * alias this source identity was first queued under, both from the
   * script's `{stream_id, canonical_event_id}` return. `eventId` differs
   * from `envelope.event_id` exactly when this envelope re-captured a
   * transition already queued under a rotated key.
   */
  async enqueue(envelope) {
    const payload = JSON.stringify(envelope);
    const args = {
      keys: [
        this.stream,
        sourceKey(this.stream, envelope.source_key),
        DEDUPE_PREFIX,
        dedupeKey(envelope.event_id),
      ],
      arguments: [payload, envelope.event_id],
    };
    let res;
    try {
      res = await this.client.evalSha(ENQUEUE_LUA_SHA1, args);
    } catch (err) {
      if (!/NOSCRIPT/i.test(String(err?.message || err))) throw err;
      res = await this.client.eval(ENQUEUE_LUA, args);
    }
    const [streamId, eventId] = Array.isArray(res) ? res : [res, envelope.event_id];
    return { streamId, eventId: eventId || envelope.event_id };
  }
}
