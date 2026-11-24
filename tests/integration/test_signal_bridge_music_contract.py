"""Signal declares its account capabilities and refuses music at the platform edge."""

from unittest.mock import patch

import pytest

from tests.integration import platform_contract as pc
from tests.integration.test_signal_bridge_routes import (
    AUTH,
    ROOM,
    _live_owner,
    _send,
    bridge_app,  # noqa: F401 — pytest fixture
    daemon,  # noqa: F401 — bridge_app fixture dependency
)

pytestmark = pytest.mark.integration


async def test_signal_declares_no_music_capability(bridge_app):  # noqa: F811
    _, http, _ = bridge_app
    account = await pc.envelope(await http.get("/v1/platform/accounts/self", headers=AUTH))
    assert account["outcome"] == "ok" and account["payload"]["capabilities"] == []


async def test_signal_refuses_music_before_reserving_a_delivery(bridge_app):  # noqa: F811
    bridge, http, upstream = bridge_app
    room_scope = {"level": "room", "id": ROOM}
    with patch.object(bridge.store, "reserve_action", wraps=bridge.store.reserve_action) as reserve:
        music = await pc.music_operation(http, AUTH, "music_skip", {"conversation_id": ROOM},
                                         operation_id="signal-no-music")
        assert music["outcome"] == "unsupported" and music["scope"] == room_scope
        assert music["payload"] == {"error": {"kind": "unsupported", "message": ""}}
        status = await pc.envelope(await http.post("/v1/platform/queries", headers=AUTH, json={
            "kind": "music_status", "params": {"conversation_id": ROOM}}), kind="music_status")
        assert status["outcome"] == "unavailable" and status["scope"] == room_scope

        owner, _ = await _live_owner(bridge)
        send = _send("signal-send", owner)
        missing = await http.post(pc.OPERATIONS, headers=AUTH, json={
            key: value for key, value in send.items() if key != "owner"})
        assert missing.status == 400 and set(await missing.json()) == {"error"}
        reserve.assert_not_awaited()
        assert await bridge.store.action("signal-no-music") is None

        delivered = await pc.envelope(await http.post(pc.OPERATIONS, headers=AUTH, json=send))
        assert delivered["outcome"] == "complete" and len(upstream.sent) == 1
        reserve.assert_awaited_once()
