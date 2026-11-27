"""Nora's WhatsApp roster read decodes the bridge's pinned roster response."""

import json
from pathlib import Path

import pytest
from aiohttp import web

from nora.platform.bridge.client import BridgeEndpoint, BridgeRoster

pytestmark = pytest.mark.integration

# The WhatsApp bridge's own test asserts its real route answers exactly this file.
FIXTURE = json.loads((Path(__file__).resolve().parents[2]
                      / "deploy/whatsapp-bridge/testdata/roster_read.json").read_text())


async def test_whatsapp_roster_decodes_the_bridge_response(aiohttp_server):
    answers = [FIXTURE["response"], {"available": True, "members": [["+85290000101", 7]]},
               {"available": False, "members": []}]
    seen = []

    async def reads(request):
        seen.append((request.headers["Authorization"], await request.json()))
        return web.json_response(answers[len(seen) - 1])

    app = web.Application()
    app.router.add_post("/v1/reads", reads)
    server = await aiohttp_server(app)
    roster = BridgeRoster("whatsapp", BridgeEndpoint(str(server.make_url("")).rstrip("/"),
                                                     "generated-token"))
    known = await roster.roster(conversation_id=FIXTURE["conversation_id"])
    assert known.available
    assert known.members == tuple(frozenset(m) for m in FIXTURE["response"]["members"])
    assert seen[0] == ("Bearer generated-token", {
        "op": "roster", "params": {"conversation_id": FIXTURE["conversation_id"]}})
    assert not (await roster.roster(conversation_id="x")).available
    assert not (await roster.roster(conversation_id="x")).available
