"""Bridge HTTP transport lifetime and uncertain delivery."""

import asyncio
import gc
import weakref

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.actions import ActionOwner
from nora.domain.models import ConversationTarget
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint
from nora.platform.discord.bridge_control import BridgeControlClient, BridgeDiscordAdapter


async def test_absent_readback_does_not_reject_an_inflight_post():
    finish_post = asyncio.Event()
    effects: list[str] = []
    pending: list[asyncio.Task] = []
    reads: list[str] = []

    async def finish_later() -> None:
        await finish_post.wait()
        effects.append("accepted")

    async def post(request: web.Request) -> web.Response:
        await request.json()
        pending.append(asyncio.create_task(finish_later()))
        assert request.transport is not None
        request.transport.close()
        return web.Response()

    async def get(request: web.Request) -> web.Response:
        reads.append(request.match_info["action_id"])
        return web.Response(status=404)

    app = web.Application()
    app.router.add_post("/v1/actions", post)
    app.router.add_get("/v1/actions/{action_id}", get)
    server = TestServer(app)
    await server.start_server()
    adapter = BridgeAdapter(
        "signal", BridgeEndpoint(str(server.make_url("")).rstrip("/"), "synthetic"),
        owner=ActionOwner("work", "w-1", 1))
    try:
        result = await adapter.send(
            target=ConversationTarget("signal", "synthetic-room", "group"),
            text="reply")
        assert len(pending) == len(reads) == 1
        assert not effects
        assert not result.ok and result.uncertain
        finish_post.set()
        await asyncio.gather(*pending)
        assert effects == ["accepted"]
    finally:
        finish_post.set()
        await asyncio.gather(*pending, return_exceptions=True)
        await adapter.close()
        await server.close()


@pytest.mark.parametrize("discord", [False, True])
@pytest.mark.parametrize("warm", [False, True])
async def test_bound_facades_borrow_root_sessions_and_isolate_actions(discord, warm):
    actions: list[dict] = []
    health_reads: list[str] = []

    @web.middleware
    async def auth(request, handler):
        if request.headers.get("Authorization") != "Bearer synthetic":
            return web.Response(status=401)
        return await handler(request)

    async def health(request):
        health_reads.append(request.path)
        return web.json_response({"ok": True})

    async def identity(request):
        return web.json_response({"primary": "synthetic-self", "handle": "nora"})

    async def post(request):
        body = await request.json()
        if body.get("op") != "send" or not body.get("request", {}).get("text"):
            return web.Response(status=400)
        actions.append(body)
        return web.json_response({"state": "confirmed", "platform_ms": len(actions)})

    app = web.Application(middlewares=[auth])
    app.router.add_get("/v1/health", health)
    app.router.add_get("/v1/self", identity)
    app.router.add_post("/v1/actions", post)
    async with TestServer(app) as server:
        endpoint = BridgeEndpoint(str(server.make_url("")).rstrip("/"), "synthetic")
        transport = BridgeAdapter("discord" if discord else "signal", endpoint)
        control = BridgeControlClient(endpoint)
        root = (BridgeDiscordAdapter(delegate=transport, control=control)
                if discord else transport)
        facades = []
        try:
            if warm:
                assert (await root.health()).connected
            owner = ActionOwner("work", "w-synthetic", 1)
            bound = root.bind_owner(owner)
            sibling = root.bind_owner(ActionOwner("otp_challenge", "otp-synthetic"))
            rebound = bound.bind_owner(owner)
            live = rebound.bind_owner(ActionOwner("live_invocation", "row-synthetic"))
            facades.extend([bound, sibling, rebound, live])
            assert (await bound.health()).connected
            session = await transport._client()
            connector = session.connector
            assert connector is not None and not connector.closed
            # A cold binding must open the ROOT's session, not an orphan session.
            delegate = bound._delegate if discord else bound
            assert await delegate._client() is session
            assert root.action_owner is None
            assert [f.action_owner for f in facades] == [
                owner, ActionOwner("otp_challenge", "otp-synthetic"), owner,
                ActionOwner("live_invocation", "row-synthetic")]

            target = ConversationTarget(root.platform, "synthetic-room", "group")
            results = await asyncio.gather(*(
                facade.send(target=target, text=text)
                for facade, text in [(bound, "work-first"), (sibling, "otp"),
                                     (rebound, "retry"), (live, "live")]))
            assert all(result.ok for result in results)
            assert (await bound.send(target=target, text="work-second")).ok
            by_text = {a["request"]["text"]: (a["owner"], a["action_id"])
                       for a in actions}
            assert by_text == {
                "work-first": (owner.to_dict(), "w-synthetic:send:0"),
                "work-second": (owner.to_dict(), "w-synthetic:send:1"),
                "retry": (owner.to_dict(), "w-synthetic:send:0"),
                "otp": ({"kind": "otp_challenge", "id": "otp-synthetic"},
                        "otp-synthetic:send:0"),
                "live": ({"kind": "live_invocation", "id": "row-synthetic"},
                         "row-synthetic:send:0"),
            }
            if discord:
                assert await bound.own_handle() == "nora"
                control_session = await control._client()
                control_connector = control_session.connector
                assert control_connector is not None and not control_connector.closed

            await bound.close()
            await rebound.close()
            assert not session.closed and not connector.closed
            if discord:
                assert not control_session.closed and not control_connector.closed
                assert await sibling.own_handle() == "nora"
                assert await control._client() is control_session
            assert (await sibling.health()).connected
            assert (await root.health()).connected
            assert await transport._client() is session
            assert len(health_reads) == (4 if warm else 3)

            # Discard the per-work facades without closing them, as real callers do.
            refs = [weakref.ref(f) for f in facades]
            facades.clear()
            del bound, sibling, rebound, live, delegate
            gc.collect()
            assert all(ref() is None for ref in refs)
            assert not session.closed
            await root.close()
            await root.close()
            assert session.closed and connector.closed
            if discord:
                assert control_session.closed and control_connector.closed
        finally:
            # Also clean the unfixed implementation when running the mutation probe.
            for facade in facades:
                await facade.close()
            await root.close()
            await control.close()
