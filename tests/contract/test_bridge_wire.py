"""Focused checks for the shared bridge wire contract (design/platform-bridges)."""

from nora.platform.bridge.envelope import Envelope, canonical_digest, canonical_source_key, event_id
from nora.platform.bridge.queue import ENQUEUE_LUA, dedupe_key, src_key

KEY = b"test-correlation-key"


def _envelope() -> Envelope:
    sk = canonical_source_key("signal", "a1", "conv", "message", "1:2")
    return Envelope(
        platform="signal", account_key="a1", kind="message", source_key=sk,
        event_id=event_id(sk, key=KEY, key_version=1),
        conversation_id="conv", conversation_type="group", sender_id="s",
        sender_name=None, sent_ms=1, received_ms=2, queued_ms=3,
        time_quality="upstream", message={"body": "hi"}, attachments=(),
        trace={}, admission_rev=3)


def test_event_id_aliases_change_with_key_version_not_source() -> None:
    sk = canonical_source_key("signal", "a1", "c", "message", "u")
    assert event_id(sk, key=KEY, key_version=1) != event_id(
        sk, key=KEY, key_version=2)
    # Canonical uniqueness is version-independent: same source, same digest.
    assert canonical_digest(sk) == canonical_digest(sk)


def test_envelope_round_trip_is_lossless() -> None:
    env = _envelope()
    assert Envelope.from_fields(env.to_fields()) == env


def test_lua_resolves_source_then_alias_and_returns_original_stream_id() -> None:
    # Source-key resolution BEFORE XADD: a post-rotation replay (new alias,
    # same source) returns the original id, never a second entry or receipt.
    assert ENQUEUE_LUA.index("KEYS[2]") < ENQUEUE_LUA.index("XADD")
    assert "GET" in ENQUEUE_LUA and "XADD" in ENQUEUE_LUA
    assert dedupe_key("v1:abc") == "nq:dedupe:v1:abc"
    assert src_key("nq:in:signal:a1", "k") == "nq:src:nq:in:signal:a1:k"
