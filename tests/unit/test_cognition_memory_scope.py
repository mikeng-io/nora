"""Cognition's real memory tool, registry and client over two units.

The search double respects named scopes and searches all stored sessions when
none is supplied. The latter is the explicit legacy mode without `[units]`,
not evidence of unit isolation (DEC-214).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp import web

from nora.agent.dreamer import RegistryToolSource
from nora.agent.registry import ToolRegistry
from nora.app.cognition_wiring import COGNITION_READ_CATALOGUE, read_tools_for
from nora.app.composition import scope_home_rooms
from nora.app.memory_routing import build_workspace_router
from nora.domain.cognition_session import Room
from nora.domain.models import PersonRef
from nora.infra.honcho import HonchoClient
from nora.obs.metrics import Metrics
from nora.policy import load_policy
from nora.testing import FakeRecordStore
from tests.fakes import FakeHoncho


class _SearchingHoncho(FakeHoncho):
    async def _handle(self, request: web.Request) -> web.Response:
        parts = request.path.strip("/").split("/")
        if request.method != "POST" or len(parts) != 4 or parts[-1] != "search":
            return await super()._handle(request)
        workspace = parts[2]
        if workspace not in self.workspaces:
            return web.json_response({"detail": "not found"}, status=404)
        body = await request.json()
        scope = body.get("scope")
        if scope is not None and (workspace, scope) not in self.scopes:
            return web.json_response({"detail": "scope not found"}, status=404)
        sessions = (
            self.scopes[(workspace, scope)] if scope is not None else self.sessions[workspace]
        )
        return web.json_response([
            message for sid in sorted(sessions)
            for message in self.messages.get((workspace, sid), ())
            if body["query"] in message["content"]
        ][:body["limit"]])


@pytest.mark.parametrize("declare_units", [True, False], ids=["scoped", "legacy-unscoped"])
async def test_cognition_workspace_search_obeys_the_configured_unit_boundary(
    tmp_path, declare_units,
):
    path = tmp_path / "policy.toml"
    path.write_text('default = "deny"\n' + "\n".join(
        f'[[conversations]]\nplatform = "{platform}"\nid = "{cid}"\n'
        f'label = "{cid}"\npersons = ["speaker"]\nunit = "{unit}"\n'
        for platform, cid, unit in (
            ("signal", "room-a", "unit-a"),
            ("whatsapp", "room-a-other", "unit-a"),
            ("signal", "room-b", "unit-b"),
        )
    ) + ('\n[units.unit-a]\nworkspace = "shared"\n'
         '[units.unit-b]\nworkspace = "shared"\n' if declare_units else ""))
    policy = load_policy(path)
    router = build_workspace_router(policy=policy, bindings={}, default_workspace="shared")
    server = _SearchingHoncho()
    await server.start()
    try:
        client = HonchoClient(server.url, "shared", workspace_router=router)
        await client.ensure_workspace()
        for cid, text in (
            ("room-a", "orchard alpha"), ("room-a-other", "orchard apricot"),
            ("room-b", "orchard beta"),
        ):
            assert await client.feed(peer_id="speaker", text=text, conversation_id=cid)
        # The same known speaker in both units: roster filtering cannot hide a leak.
        persons = SimpleNamespace(by_person={"speaker": PersonRef("speaker", "Speaker", "speaker")})
        source = RegistryToolSource(
            registry=ToolRegistry(unit_for=lambda p, c: policy.conversation(p, c).unit),
            store=FakeRecordStore(), persons=persons, honcho=client, metrics=Metrics(),
            scope_rooms=scope_home_rooms(policy),
        )
        name = "search_memory_workspace"
        assert name in COGNITION_READ_CATALOGUE
        build = read_tools_for(source, names=frozenset({name}))
        for unit, room, expected in (
            ("unit-a", "room-a", {"orchard alpha", "orchard apricot"}),
            ("unit-b", "room-b", {"orchard beta"}),
        ):
            [tool] = build(unit, [Room("signal", room)])
            events = [event async for event in tool.stream(
                {"toolUseId": "test", "name": name, "input": {"query": "orchard"}}, {},
            )]
            result = events[-1].tool_result
            assert result["status"] == "success"
            answer = json.loads(result["content"][0]["text"])
            assert answer["status"] == "ok", answer
            if not declare_units:
                expected = {"orchard alpha", "orchard apricot", "orchard beta"}
            assert {hit["said"] for hit in answer["results"]} == expected
    finally:
        await server.close()
