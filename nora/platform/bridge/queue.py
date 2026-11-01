"""Redis Streams producer contract — one stream per platform account.

Dedupe resolves the canonical `source_key` first, THEN the `event_id` alias.
The source key is key-version-independent, so a post-rotation replay (new
alias, same source) still returns the ORIGINAL stream id — the second entry
and second receipt a bare alias-dedupe would emit never exist.

The Lua script is THE contract: `nq:src:{stream}:{source_key}` → original
event_id, `nq:dedupe:{event_id}` → stream id. Written independently by the JS
and Go bridges — the literal is duplicated there deliberately, so a drifted
copy fails a parity test rather than agreeing.
"""

from __future__ import annotations

import json

import redis.asyncio as aioredis

from .envelope import Envelope

# KEYS[1] = stream, KEYS[2] = src->event_id key, KEYS[3] = dedupe prefix,
# KEYS[4] = this event's dedupe key; ARGV[1] = envelope JSON, ARGV[2] = event_id.
# Returns {stream_id, canonical_event_id} — the ORIGINAL alias under which the
# entry was written, so receipts/actions key on one effect across rotation.
ENQUEUE_LUA = """
local orig = redis.call('GET', KEYS[2])
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
return {id, ARGV[2]}
""".strip()

DEDUPE_PREFIX = "nq:dedupe:"
SRC_PREFIX = "nq:src:"
CONSUMER_GROUP = "nora-v1"


def dedupe_key(event_id: str) -> str:
    return DEDUPE_PREFIX + event_id


def src_key(stream: str, source_key: str) -> str:
    """The rotation-stable dedupe lookup — plaintext source identity is private
    but already travels inside the stream entry, so a mapping key adds nothing
    to the exposure envelope."""
    return f"{SRC_PREFIX}{stream}:{source_key}"


class StreamProducer:
    """Journaled, deduplicated enqueue. Bridges hold one per account stream."""

    def __init__(self, client: aioredis.Redis, stream: str) -> None:
        self._client = client
        self.stream = stream
        self._script = client.register_script(ENQUEUE_LUA)

    async def enqueue(self, envelope: Envelope) -> tuple[str, str]:
        """Returns (stream_id, canonical_event_id) — the producer's only
        receipt. Idempotent on source identity: a retry under the same OR a
        rotated alias returns the original stream id and the ORIGINAL
        event_id, never a second entry. Callers keying downstream effects
        (enqueue receipts, action ids) on the event MUST use the canonical id
        returned here, not `envelope.event_id` — a rotated retry carries a new
        alias for the same effect."""
        payload = json.dumps(envelope.to_fields(), separators=(",", ":"))
        result = await self._script(
            keys=[
                self.stream,
                src_key(self.stream, envelope.source_key),
                DEDUPE_PREFIX,
                dedupe_key(envelope.event_id),
            ],
            args=[payload, envelope.event_id],
        )
        sid, canonical = result
        return (
            sid.decode() if isinstance(sid, bytes) else sid,
            canonical.decode() if isinstance(canonical, bytes) else canonical,
        )


async def ensure_group(client: aioredis.Redis, stream: str, group: str = CONSUMER_GROUP) -> None:
    try:
        await client.xgroup_create(stream, group, id="0", mkstream=True)
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
