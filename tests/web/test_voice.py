"""POST /app/v1/voice/utterance — the shape contract, with the sink faked.

The reactive half (that the emitted `NormalizedMessage` really drives ingest
to an audit row) is the integration test's job
(`tests/integration/test_voice_utterance.py`); here the sink only records,
and the assertions are on what the route BUILDS and what it refuses.
"""

import time

import pytest
from aiohttp import web

from nora.web.voice import register_voice

CONV = "111111111111111111/222222222222222222"
USER = "333333333333333333"
BODY = {"user_id": USER, "text": "喂 Nora 而家幾點", "conversation_id": CONV}


@pytest.fixture
async def client(aiohttp_client):
    received = []

    async def _ingest(message):
        received.append(message)

    app = web.Application()
    # An async recording sink — the same awaitable shape production's
    # `Dispatcher.on_message` has, which the route awaits.
    register_voice(app, ingest=_ingest)
    client = await aiohttp_client(app)
    client.received = received
    return client


async def test_a_voice_utterance_becomes_a_typed_shaped_message(client):
    before = int(time.time() * 1000)
    resp = await client.post("/app/v1/voice/utterance", json=BODY)
    assert resp.status == 202
    assert (await resp.json())["status"] == "accepted"

    (message,) = client.received
    assert message.platform == "discord"
    assert message.conversation_id == CONV
    assert message.conversation_type == "group"
    assert message.sender_id == USER
    assert message.sender_name is None
    assert message.body == "喂 Nora 而家幾點"
    assert before <= message.timestamp <= int(time.time() * 1000)
    # The raw envelope marks the message as voice-originated, so the audit
    # row's raw_json can always tell a spoken utterance from a typed one.
    assert message.raw == {"voice": True, "user_id": USER, "channel_id": CONV}


async def test_missing_fields_are_refused_with_400(client):
    for field in ("user_id", "text", "conversation_id"):
        body = {k: v for k, v in BODY.items() if k != field}
        resp = await client.post("/app/v1/voice/utterance", json=body)
        assert resp.status == 400, field
    assert client.received == []


async def test_an_empty_transcript_is_refused(client):
    resp = await client.post(
        "/app/v1/voice/utterance", json={**BODY, "text": "   "}
    )
    assert resp.status == 400
    assert client.received == []


async def test_a_non_json_body_is_refused(client):
    resp = await client.post(
        "/app/v1/voice/utterance",
        data=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    assert client.received == []


EVENT = {
    "type": "speech-finished", "guild_id": "111111111111111111",
    "event_id": "evt-1", "event_seq": 7, "generation": "gen-1",
}


@pytest.fixture
async def event_client(aiohttp_client):
    seen = []

    async def _ingest(message):
        pass

    async def _on_voice_event(body):
        seen.append(body)
        return 200, {"status": "ok"}

    app = web.Application()
    register_voice(app, ingest=_ingest, on_voice_event=_on_voice_event)
    client = await aiohttp_client(app)
    client.seen = seen
    return client


async def test_the_event_route_exists_only_when_a_consumer_is_wired(client):
    """Without `on_voice_event` there is no endpoint to poke at all."""
    resp = await client.post("/app/v1/voice/event", json=EVENT)
    assert resp.status == 404


async def test_an_event_reaches_the_consumer_and_the_verdict_passes_through(event_client):
    resp = await event_client.post("/app/v1/voice/event", json=EVENT)
    assert resp.status == 200
    assert (await resp.json())["status"] == "ok"
    assert event_client.seen == [EVENT]


async def test_an_event_with_invalid_json_is_refused_with_400(event_client):
    resp = await event_client.post(
        "/app/v1/voice/event",
        data=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    assert event_client.seen == []
