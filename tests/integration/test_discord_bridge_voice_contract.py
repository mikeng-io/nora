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

from nora.infra.voice.http_controller import VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord.bridge_app import DiscordBridge
from nora.testing.voice import FakeVoiceController
from tests.integration.platform_contract import DOCUMENT, envelope

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
        "operation_id": operation_id or str(uuid4()), "kind": kind, "effect": effect}))


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
    assert status["payload"] == {"connected": True, "session": True,
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
