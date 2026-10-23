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

GUILD = "111111111111111111"
# The guild's TEXT room — where its speech is recorded, deliberately NOT the
# voice channel below.
CONV = f"{GUILD}/222222222222222222"
VOICE_CHANNEL = "999999999999999999"
USER = "333333333333333333"
BODY = {
    "user_id": USER, "text": "喂 Nora 而家幾點",
    "guild_id": GUILD, "voice_channel_id": VOICE_CHANNEL,
}
# The wake gate's bearer: the utterance route refuses everything without it.
TOKEN = "test-bridge-token"  # noqa: S105 - a fake
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def bridge_token(monkeypatch):
    monkeypatch.setenv("NORA_BRIDGE_TOKEN", TOKEN)


@pytest.fixture
async def client(aiohttp_client):
    received = []

    async def _ingest(message):
        received.append(message)

    app = web.Application()
    # An async recording sink — the same awaitable shape production's
    # `Dispatcher.on_message` has, which the route awaits.
    register_voice(
        app, ingest=_ingest,
        resolve_voice_room=lambda guild_id: CONV if guild_id == GUILD else None,
    )
    client = await aiohttp_client(app)
    client.received = received
    return client


async def test_a_voice_utterance_becomes_a_typed_shaped_message(client):
    before = int(time.time() * 1000)
    resp = await client.post("/app/v1/voice/utterance", headers=AUTH, json=BODY)
    assert resp.status == 202
    assert (await resp.json())["status"] == "accepted"

    (message,) = client.received
    assert message.platform == "discord"
    # THE SWAP: the guild resolved to its TEXT room, not to the voice channel
    # the words were spoken in.
    assert message.conversation_id == CONV
    assert message.conversation_type == "group"
    assert message.sender_id == USER
    assert message.sender_name is None
    assert message.body == "喂 Nora 而家幾點"
    assert message.voice is True, "the reply must be routed back out as speech"
    assert message.target().voice is True
    assert before <= message.timestamp <= int(time.time() * 1000)
    # The raw envelope marks the message as voice-originated and keeps the
    # real channel as provenance, so a trace can still say where it was said.
    assert message.raw == {
        "voice": True, "user_id": USER,
        "guild_id": GUILD, "voice_channel_id": VOICE_CHANNEL,
    }


async def test_missing_fields_are_refused_with_400(client):
    for field in ("user_id", "text", "guild_id"):
        body = {k: v for k, v in BODY.items() if k != field}
        resp = await client.post("/app/v1/voice/utterance", headers=AUTH, json=body)
        assert resp.status == 400, field
    assert client.received == []


async def test_an_unmarked_guild_is_refused_and_ingests_nothing(client):
    """The resolution IS the admission check. A guild with no room marked
    `voice = true` has nowhere its speech belongs, and falling back to the
    voice channel's own id would write a spoken sentence into a room nobody
    chose — the per-channel rooms this reversal exists to stop."""
    resp = await client.post(
        "/app/v1/voice/utterance", headers=AUTH, json={**BODY, "guild_id": "444444444444444444"},
    )
    assert resp.status == 400
    assert client.received == []


async def test_the_voice_channel_id_is_optional_provenance(client):
    """Only the controller knows which channel it is in, so a caller that
    could not read it must still be able to forward the words. Absent is
    honest; a stale configured id in a trace would not be."""
    body = {k: v for k, v in BODY.items() if k != "voice_channel_id"}
    resp = await client.post("/app/v1/voice/utterance", headers=AUTH, json=body)

    assert resp.status == 202
    (message,) = client.received
    assert message.conversation_id == CONV
    assert message.raw["voice_channel_id"] is None


async def test_an_empty_transcript_is_refused(client):
    resp = await client.post(
        "/app/v1/voice/utterance", headers=AUTH, json={**BODY, "text": "   "}
    )
    assert resp.status == 400
    assert client.received == []


async def test_a_non_json_body_is_refused(client):
    resp = await client.post(
        "/app/v1/voice/utterance",
        data=b"not json",
        headers={"Content-Type": "application/json", **AUTH},
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
    register_voice(
        app, ingest=_ingest, resolve_voice_room=lambda _g: CONV,
        on_voice_event=_on_voice_event,
    )
    client = await aiohttp_client(app)
    client.seen = seen
    return client


async def test_the_event_route_exists_only_when_a_consumer_is_wired(client):
    """Without `on_voice_event` there is no endpoint to poke at all."""
    resp = await client.post("/app/v1/voice/event", headers=AUTH, json=EVENT)
    assert resp.status == 404


async def test_an_event_reaches_the_consumer_and_the_verdict_passes_through(event_client):
    resp = await event_client.post("/app/v1/voice/event", headers=AUTH, json=EVENT)
    assert resp.status == 200
    assert (await resp.json())["status"] == "ok"
    assert event_client.seen == [EVENT]


async def test_an_event_with_invalid_json_is_refused_with_400(event_client):
    resp = await event_client.post(
        "/app/v1/voice/event",
        data=b"not json",
        headers={"Content-Type": "application/json", **AUTH},
    )
    assert resp.status == 400
    assert event_client.seen == []


async def test_an_event_without_the_bearer_never_reaches_the_consumer(event_client):
    """A co-networked caller must not be able to detach a live guild session."""
    resp = await event_client.post("/app/v1/voice/event", json=EVENT)
    assert resp.status == 401
    assert event_client.seen == []


async def test_an_event_with_a_wrong_bearer_never_reaches_the_consumer(event_client):
    resp = await event_client.post(
        "/app/v1/voice/event", json=EVENT, headers={"Authorization": "Bearer not-it"},
    )
    assert resp.status == 401
    assert event_client.seen == []


async def test_an_utterance_without_the_bearer_is_refused_and_nothing_is_ingested(client):
    resp = await client.post("/app/v1/voice/utterance", json=BODY)
    assert resp.status == 401
    assert client.received == []


async def test_a_wrong_bearer_is_refused_and_nothing_is_ingested(client):
    resp = await client.post(
        "/app/v1/voice/utterance", json=BODY, headers={"Authorization": "Bearer not-it"},
    )
    assert resp.status == 401
    assert client.received == []


async def test_an_unset_token_refuses_every_utterance(client, monkeypatch):
    monkeypatch.delenv("NORA_BRIDGE_TOKEN")
    resp = await client.post("/app/v1/voice/utterance", headers=AUTH, json=BODY)
    assert resp.status == 401
    assert client.received == []
