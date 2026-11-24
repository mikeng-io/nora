"""The Discord bridge's music effects and reads over the platform wire."""

import asyncio
from unittest.mock import create_autospec

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nora.domain.ports import MusicToolBridge, MusicToolOutcome
from nora.platform.bridge.answers import OperationLedger
from nora.platform.bridge.contract import PlatformSurface, QueryAnswer
from nora.platform.discord import music_messages as m
from nora.platform.discord.bridge_app import DiscordBridge
from nora.platform.discord.bridge_music import BridgeMusicSurface
from tests.integration.platform_contract import OPERATIONS, envelope, music_operation
from tests.integration.test_discord_bridge_control import (
    ACCOUNT,
    AUTH,
    CONV,
    GUILD,
    MEMBER,
    TOKEN,
    bridge,  # noqa: F401 — pytest fixture
    world,  # noqa: F401 — bridge fixture dependency
)

ROOM = {"conversation_id": CONV}
ROOM_SCOPE = {"level": "room", "id": CONV}
PLAY = {**ROOM, "sender_ids": [MEMBER], "query_or_url": "song-a"}
ACCOUNTS = "/v1/platform/accounts/self"


async def _status(http, room=CONV):
    return await envelope(await http.post("/v1/platform/queries", headers=AUTH, json={
        "kind": "music_status", "params": {"conversation_id": room}}), kind="music_status")


async def test_music_commands_and_status_use_the_live_discord_queue(bridge):  # noqa: F811
    await bridge["bridge"].start()
    http = bridge["http"]
    account = await envelope(await http.get(ACCOUNTS, headers=AUTH))
    assert account["payload"]["capabilities"] == ["music"]
    empty = await _status(http)
    assert empty["outcome"] == "nothing_found" and empty["payload"] is None
    played = await music_operation(http, AUTH, "music_play", PLAY)
    assert played["outcome"] == "complete"
    track = {key: played["payload"][key] for key in ("title", "url")}
    assert track["title"] == "Track A"
    assert played["payload"] == {**track, "queued": False, "percent": None, "error": None}
    current = await _status(http)
    assert current["outcome"] == "ok" and current["payload"] == track
    assert current["scope"] == ROOM_SCOPE
    absent_room = f"{GUILD}/unadmitted"
    outside = await _status(http, absent_room)
    assert outside["outcome"] == "nothing_found" and outside["payload"] is None
    assert outside["scope"] == {"level": "room", "id": absent_room}

    queued = await music_operation(http, AUTH, "music_play", {**PLAY, "query_or_url": "song-b"})
    assert queued["outcome"] == "complete" and queued["payload"]["queued"] is True
    top = await music_operation(http, AUTH, "music_top", {**ROOM, "position": 1})
    assert top["outcome"] == "complete" and top["payload"]["title"] == "Track B"
    skip = await music_operation(http, AUTH, "music_skip", ROOM)
    assert skip["outcome"] == "complete" and skip["payload"]["title"] == "Track A"
    volume = await music_operation(http, AUTH, "music_set_volume", {**ROOM, "percent": 50})
    assert volume["outcome"] == "complete" and volume["payload"]["percent"] == 50
    stop = await music_operation(http, AUTH, "music_stop", ROOM)
    assert stop["outcome"] == "complete"
    for answer in (played, queued, top, skip, volume, stop):
        assert answer["scope"] == ROOM_SCOPE
    assert (await _status(http))["outcome"] == "nothing_found"


async def test_discord_without_music_refuses_the_closed_kinds():
    discord = DiscordBridge(api_token=TOKEN, account_key=ACCOUNT, client=object())
    discord.self_id = "fixture-discord-account"
    async with TestClient(TestServer(discord.routes())) as http:
        account = await envelope(await http.get(ACCOUNTS, headers=AUTH))
        assert account["payload"]["capabilities"] == []
        answer = await music_operation(http, AUTH, "music_play", PLAY)
        assert answer["outcome"] == "unsupported"
        assert answer["payload"] == {"error": {"kind": "unsupported", "message": ""}}
        assert answer["scope"] == ROOM_SCOPE
        status = await _status(http)
        assert status["outcome"] == "unavailable" and status["scope"] == ROOM_SCOPE


@pytest.fixture
async def music_http():
    tool = create_autospec(MusicToolBridge, instance=True)
    music = BridgeMusicSurface(
        tool=tool, admit_room=lambda room: room == CONV,
        admit_requester=lambda _room, senders: MEMBER in senders,
        ledger=OperationLedger(bound=1, full_message=""))

    async def account():
        return {"platform": "discord", "primary": "fixture-discord-account", "aliases": []}

    async def status():
        return {"ok": True}

    async def query(kind, params):
        return QueryAnswer("unavailable")

    app = web.Application()
    PlatformSurface(
        platform="discord", actions=lambda: None, music=music, account=account, status=status,
        query=query, custody_dir=lambda: None, max_blob_bytes=lambda: 1).add_routes(app)
    async with TestClient(TestServer(app)) as http:
        yield http, tool


async def test_self_defaults_capabilities_when_the_account_omits_them(music_http):
    http, _ = music_http
    account = await envelope(await http.get(ACCOUNTS, headers=AUTH))
    assert account["outcome"] == "ok" and account["payload"]["capabilities"] == []


@pytest.mark.parametrize(("kind", "status", "note", "error_kind"), [
    ("music_play", "nothing_found", m.nothing_found_line(), "nothing_found"),
    ("music_skip", "nothing_found", m.nothing_playing_line(), "nothing_playing"),
    ("music_play", "unavailable", m.disconnected_note(), "disconnected"),
    ("music_play", "unavailable", m.not_in_voice_note(), "not_in_voice"),
    ("music_play", "unavailable", m.join_failed_note(), "join_failed"),
    ("music_play", "unavailable", m.not_in_allowed_guild_note(), "not_in_allowed_guild"),
    ("music_set_volume", "unavailable", m.volume_out_of_range_line(), "volume_out_of_range"),
    ("music_top", "nothing_found", m.nothing_queued_at_position_line(),
     "nothing_queued_at_position"),
])
async def test_music_notes_become_typed_errors(music_http, kind, status, note, error_kind):
    http, tool = music_http
    method = getattr(tool, kind.removeprefix("music_"))
    method.return_value = MusicToolOutcome(status=status, note=note)
    effect = {"music_play": PLAY, "music_top": {**ROOM, "position": 2},
              "music_set_volume": {**ROOM, "percent": 201}}.get(kind, ROOM)
    answer = await music_operation(http, AUTH, kind, effect)
    error = {"kind": error_kind, "message": ""}
    method.assert_awaited_once()
    if status == "nothing_found":
        assert answer["outcome"] == "rejected"
        assert answer["payload"] == {
            "title": None, "url": None, "queued": False, "percent": None, "error": error}
    else:
        assert answer["outcome"] == "unavailable" and answer["payload"] == {"error": error}
    assert answer["scope"] == ROOM_SCOPE


@pytest.mark.parametrize("raises", [True, False], ids=["exception", "unmapped-note"])
async def test_unclassified_music_failures_return_music_failed(music_http, raises):
    http, tool = music_http
    if raises:
        tool.skip.side_effect = RuntimeError("private upstream failure")
    else:
        tool.skip.return_value = MusicToolOutcome(status="nothing_found", note="unmapped note")
    answer = await music_operation(http, AUTH, "music_skip", ROOM)
    assert answer["outcome"] == "unavailable"
    assert answer["payload"] == {"error": {"kind": "music_failed", "message": ""}}
    tool.skip.assert_awaited_once()


async def test_music_status_tool_failure_is_unavailable(music_http):
    http, tool = music_http
    tool.status.side_effect = RuntimeError("private upstream failure")
    answer = await _status(http)
    assert answer["outcome"] == "unavailable" and answer["payload"] is None
    tool.status.assert_awaited_once_with(conversation_id=CONV)


@pytest.mark.parametrize(("overrides", "error_kind"), [
    ({"conversation_id": "unadmitted-room"}, "room_not_admitted"),
    ({"sender_ids": ["unadmitted-member"]}, "sender_not_admitted"),
])
async def test_music_refuses_before_the_tool_runs(music_http, overrides, error_kind):
    http, tool = music_http
    answer = await music_operation(http, AUTH, "music_play", {**PLAY, **overrides})
    assert answer["outcome"] == "denied"
    assert answer["payload"] == {"error": {"kind": error_kind, "message": ""}}
    tool.play.assert_not_awaited()


async def test_music_ledger_replays_the_first_answer_and_refuses_other_effects(music_http):
    http, tool = music_http
    tool.play.side_effect = [
        MusicToolOutcome(status="ok", title="First", url="https://example.test/first"),
        MusicToolOutcome(status="ok", title="Second", url="https://example.test/second"),
    ]
    first = await music_operation(http, AUTH, "music_play", PLAY, operation_id="play-once")
    replay = await music_operation(http, AUTH, "music_play", PLAY, operation_id="play-once")
    assert first["outcome"] == "complete" and replay == first
    conflict = await http.post(OPERATIONS, headers=AUTH, json={
        "operation_id": "play-once", "kind": "music_play",
        "effect": {**PLAY, "query_or_url": "song-b"}})
    assert conflict.status == 409 and await conflict.json() == {"error": "operation_id_reused"}
    full = await music_operation(http, AUTH, "music_play", PLAY, operation_id="other-play")
    assert full["outcome"] == "unavailable"
    assert full["payload"] == {"error": {"kind": "ledger_full", "message": ""}}
    assert full["scope"] == ROOM_SCOPE
    tool.play.assert_awaited_once()


async def test_music_read_back_works_without_a_delivery_store(music_http):
    http, tool = music_http
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop(**_):
        entered.set()
        await release.wait()
        return MusicToolOutcome(status="ok")

    async def read_back():
        return await envelope(await http.get(f"{OPERATIONS}/stop-once", headers=AUTH),
                              kind="music_stop")

    tool.stop.side_effect = stop
    async with asyncio.TaskGroup() as tasks:
        applied = tasks.create_task(
            music_operation(http, AUTH, "music_stop", ROOM, operation_id="stop-once"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            pending = await read_back()
            assert pending["outcome"] == "ok"
            assert pending["payload"]["outcome"] == "pending"
            assert pending["payload"]["payload"] == {}
            assert pending["scope"] == pending["payload"]["scope"] == ROOM_SCOPE
        finally:
            release.set()
    receipt = await read_back()
    assert receipt["outcome"] == "ok" and receipt["payload"] == applied.result()
    assert receipt["scope"] == receipt["payload"]["scope"] == ROOM_SCOPE
    assert receipt["payload"]["outcome"] == "complete"


@pytest.mark.parametrize(("overrides", "status"), [
    ({"kind": []}, 422),
    ({"kind": "music_unknown"}, 422),
    ({"effect": {"sender_ids": [], "query_or_url": "song-a"}}, 400),
    ({"effect": {**ROOM, "position": 1}}, 400),
    ({"owner": None}, 400),
])
async def test_invalid_music_requests_fail_before_the_tool(music_http, overrides, status):
    http, tool = music_http
    answer = await http.post(OPERATIONS, headers=AUTH, json={
        "operation_id": "invalid-play", "kind": "music_play", "effect": PLAY, **overrides})
    assert answer.status == status
    assert set(await answer.json()) == {"error"}
    tool.play.assert_not_awaited()
