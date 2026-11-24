"""The Discord bridge serves the `voice` contract: its routes, and every answer
held to the document's envelope and the kernel's status for its outcome.

The bridge is the real `DiscordBridge` over a real `VoiceControllerHub`, whose
controller is `nora/testing/voice.py`'s fake speaking the controller's wire.
"""

from __future__ import annotations

import base64
import json
from uuid import uuid4

import pytest
from aiohttp import web

from nora.infra.voice.http_controller import VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord.bridge_app import DiscordBridge
from nora.testing.voice import FakeVoiceController, operation_envelope
from tests.integration.platform_contract import DOCUMENT, assert_settled_operation, envelope

TOKEN = "test-bridge-token"  # noqa: S105 — a synthetic fixture token
AUTH = {"Authorization": f"Bearer {TOKEN}"}
GUILD = "100000000000000001"
UNREACHABLE = "100000000000000009"
CHANNEL = "100000000000000002"
BOUND = 1536 * 1024
VOICE_ROUTES = {("POST", "/v1/voice/operations"),
                ("GET", "/v1/voice/operations/{operation_id}"),
                ("POST", "/v1/voice/queries")}


@pytest.fixture
async def bridge(aiohttp_client):
    fake = FakeVoiceController(GUILD)
    controller = await aiohttp_client(fake.app())
    hub = VoiceControllerHub({
        GUILD: VoiceControllerClient(str(controller.make_url("")).rstrip("/")),
        UNREACHABLE: VoiceControllerClient("http://127.0.0.1:1", timeout_seconds=2.0)})
    app = DiscordBridge(api_token=TOKEN, account_key="generated-account", client=object(),
                        hub=hub, max_speech_bytes=BOUND).routes()
    return fake, hub, app, await aiohttp_client(app)


async def _op(http, kind: str, operation_id: str | None = None, **effect) -> dict:
    return await envelope(await http.post("/v1/voice/operations", headers=AUTH, json={
        "operation_id": operation_id or str(uuid4()), "kind": kind, "effect": effect}),
        kind=kind)


def _audio(size: int) -> str:
    return base64.b64encode(b"\x01" * size).decode()


async def test_the_bridge_serves_the_voice_contract_routes(bridge):
    _fake, _hub, app, http = bridge
    served = {(r.method, r.resource.canonical) for r in app.router.routes()
              if r.resource is not None and r.method != "HEAD"}
    documented = {(m.upper(), p) for p, item in json.loads(DOCUMENT.read_text())["paths"].items()
                  for m in item}

    assert {(m, p) for m, p in served if p.startswith("/v1/voice/")} == VOICE_ROUTES
    assert documented >= VOICE_ROUTES
    prefix = "/".join(("", "v1", "voice"))
    for method, path in (("POST", f"{prefix}/event"), ("GET", f"{prefix}/{GUILD}"),
                         ("POST", f"{prefix}/{GUILD}/join"),
                         ("POST", f"{prefix}/{GUILD}/speech")):
        assert (await http.request(method, path, headers=AUTH, json={})).status in (404, 405)


async def test_voice_outcomes_follow_the_contract(bridge):
    fake, hub, _app, http = bridge

    joined = await _op(http, "join", "op-join", guild_id=GUILD, channel_id=CHANNEL)
    assert joined["outcome"] == "complete" and joined["when"]["state"] == "settled"
    assert joined["scope"] == {"level": "installation"} and joined["from"]["target"] == GUILD
    generation = joined["payload"]["state"]["generation"]
    assert generation == fake.generation

    status = await envelope(await http.post("/v1/voice/queries", headers=AUTH, json={
        "kind": "status", "params": {"guild_id": GUILD}}))
    assert status["outcome"] == "ok" and status["when"]["known_by"] == "bridge_clock"
    assert status["payload"] == {"connected": True, "session": True, "playback_id": None,
                                 "generation": generation, "channel_id": CHANNEL}

    # A speech body past aiohttp's 1 MiB default is still read.
    spoken = await _op(http, "speak", guild_id=GUILD, generation=generation,
                       media_type="audio/wav", audio_b64=_audio(1024 * 1024))
    assert spoken["outcome"] == "complete" and fake.speech_active
    over = await http.post("/v1/voice/operations", headers=AUTH, json={
        "operation_id": str(uuid4()), "kind": "speak", "effect": {
            "guild_id": GUILD, "generation": generation, "media_type": "audio/wav",
            "audio_b64": _audio(BOUND + 1)}})
    assert over.status == 400

    stale = await _op(http, "speak", guild_id=GUILD, generation="gen-stale",
                      media_type="audio/wav", audio_b64=_audio(4))
    assert stale["outcome"] == "rejected"
    assert stale["payload"]["error"]["kind"] == "generation_conflict"
    assert stale["payload"]["state"]["generation"] == generation
    assert (await _op(http, "play", guild_id=GUILD, generation=generation,
                      stream_url="https://cdn.example/a"))["outcome"] == "unsupported"
    assert (await _op(http, "join", guild_id=UNREACHABLE,
                      channel_id=CHANNEL))["outcome"] == "unavailable"

    reused = await http.post("/v1/voice/operations", headers=AUTH, json={
        "operation_id": "op-join", "kind": "leave",
        "effect": {"guild_id": GUILD, "generation": generation}})
    assert reused.status == 409
    read = await envelope(await http.get("/v1/voice/operations/op-join", headers=AUTH))
    assert read["outcome"] == "ok" and read["payload"] == joined

    left = await _op(http, "leave", guild_id=GUILD, generation=generation)
    assert left["outcome"] == "complete" and fake.voice == "idle"
    assert hub.session_for(GUILD) is None
    gone = await _op(http, "leave", guild_id=GUILD, generation=generation)
    assert (gone["outcome"], gone["payload"]["error"]["kind"]) == ("rejected", "no_connection")


async def test_a_leave_the_controller_could_not_take_is_unavailable(bridge):
    """An outage on `leave` is `unavailable`, and the session the bridge holds
    is kept: nothing confirmed the connection is gone."""
    fake, hub, _app, http = bridge
    generation = (await _op(http, "join", guild_id=GUILD, channel_id=CHANNEL)
                  )["payload"]["state"]["generation"]
    fake.fault = "join_failed"

    left = await _op(http, "leave", guild_id=GUILD, generation=generation)

    assert left["outcome"] == "unavailable"
    assert hub.session_for(GUILD) is not None and fake.voice == "connected"


@pytest.mark.parametrize("kind", ["speak", "leave"])
async def test_a_controller_restart_behind_the_cache_is_a_generation_conflict(bridge, kind):
    """The controller restarted under a new generation while the bridge still
    holds the old one: the operation is `rejected` `generation_conflict` with
    the generation the controller holds now, and nothing was applied."""
    fake, hub, _app, http = bridge
    stale = (await _op(http, "join", guild_id=GUILD, channel_id=CHANNEL)
             )["payload"]["state"]["generation"]
    fake.restart()
    # The rejection itself names the generation: no status read is needed.
    fake.status_unavailable = True
    effect = {"guild_id": GUILD, "generation": stale}
    if kind == "speak":
        effect |= {"media_type": "audio/wav", "audio_b64": _audio(4)}

    answer = await _op(http, kind, **effect)

    assert answer["outcome"] == "rejected"
    assert answer["payload"]["error"]["kind"] == "generation_conflict"
    assert answer["payload"]["state"]["generation"] == fake.generation != stale
    assert hub.generation_for(GUILD) == fake.generation, "the live session is kept"
    assert fake.voice == "connected" and not fake.speech_active


async def test_a_conflict_from_the_controller_is_an_envelope(aiohttp_client):
    """A 409 from the controller is answered with an envelope on `join`,
    `speak` and `leave`, never a bare 500."""
    conflict = {"on": False}

    async def operation(request: web.Request) -> web.Response:
        if conflict["on"]:
            return web.json_response({"error": "operation_id_reused"}, status=409)
        return web.json_response(operation_envelope("complete", {"state": {
            "voice": "connected", "generation": "gen-1", "channel_id": CHANNEL,
            "playback_id": None}}))

    stub = web.Application()
    stub.router.add_post("/v1/voice/operations", operation)
    controller = await aiohttp_client(stub)
    hub = VoiceControllerHub({GUILD: VoiceControllerClient(
        str(controller.make_url("")).rstrip("/"))})
    http = await aiohttp_client(DiscordBridge(
        api_token=TOKEN, account_key="generated-account", client=object(), hub=hub).routes())
    assert (await _op(http, "join", guild_id=GUILD, channel_id=CHANNEL))["outcome"] == "complete"
    conflict["on"] = True

    spoken = await _op(http, "speak", guild_id=GUILD, generation="gen-1",
                       media_type="audio/wav", audio_b64=_audio(4))
    left = await _op(http, "leave", guild_id=GUILD, generation="gen-1")
    hub.drop(GUILD)
    joined = await _op(http, "join", guild_id=GUILD, channel_id=CHANNEL)

    assert [spoken["outcome"], left["outcome"], joined["outcome"]] == ["unavailable"] * 3


@pytest.mark.parametrize(("snapshot", "kept"), [
    ({"generation": "gen-2", "channel_id": CHANNEL}, True),
    ({"voice": "idle", "generation": None, "channel_id": None}, False),
])
async def test_only_a_rejection_that_states_no_connection_drops_the_session(
        aiohttp_client, snapshot, kept):
    """A rejection snapshot without `voice` says nothing about the connection, so
    the cached session stays; one that states `idle` drops it."""
    async def operation(request: web.Request) -> web.Response:
        if (await request.json())["kind"] == "join":
            return web.json_response(operation_envelope("complete", {"state": {
                "voice": "connected", "generation": "gen-1", "channel_id": CHANNEL,
                "playback_id": None}}))
        return web.json_response(operation_envelope("rejected", {
            "state": snapshot, "error": {"kind": "generation_conflict", "message": "x"}}))

    stub = web.Application()
    stub.router.add_post("/v1/voice/operations", operation)
    controller = await aiohttp_client(stub)
    hub = VoiceControllerHub({GUILD: VoiceControllerClient(
        str(controller.make_url("")).rstrip("/"))})
    http = await aiohttp_client(DiscordBridge(
        api_token=TOKEN, account_key="generated-account", client=object(), hub=hub).routes())
    assert (await _op(http, "join", guild_id=GUILD, channel_id=CHANNEL))["outcome"] == "complete"

    spoken = await _op(http, "speak", guild_id=GUILD, generation="gen-1",
                       media_type="audio/wav", audio_b64=_audio(4))

    assert spoken["payload"]["error"]["kind"] == "generation_conflict"
    assert (hub.session_for(GUILD) is not None) is kept


@pytest.mark.parametrize(("hub_status", "outcome"), [
    (200, "complete"), (403, "denied"), (500, "unavailable"),
])
async def test_a_report_is_answered_with_a_settled_envelope(aiohttp_client, hub_status, outcome):
    """Whatever the event consumer answers, the bridge's report is the settled
    envelope a controller's outbox counts delivered."""
    class _Sink:
        async def consume(self, body: object) -> tuple[int, dict]:
            return hub_status, {"status": "ok"} if hub_status == 200 else {"error": "x"}

    http = await aiohttp_client(DiscordBridge(
        api_token=TOKEN, account_key="generated-account", client=object(),
        voice_event_sink=_Sink()).routes())
    resp = await http.post("/v1/voice/operations", headers={**AUTH, "traceparent": (
        "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01")}, json={
        "operation_id": "op-1", "kind": "report", "effect": {
            "type": "speech-finished", "event_id": "evt-1", "event_seq": 1, "guild_id": GUILD}})

    body = await resp.json()
    assert_settled_operation(resp.status, body)
    assert body["outcome"] == outcome and body["trace"] == "ab" * 16


@pytest.mark.parametrize("kind", [["join"], {"k": 1}, 7, None])
async def test_a_kind_that_is_not_a_string_is_422(bridge, kind):
    _fake, _hub, _app, http = bridge
    resp = await http.post("/v1/voice/operations", headers=AUTH, json={
        "operation_id": "op-1", "kind": kind, "effect": {"guild_id": GUILD}})
    assert resp.status == 422
