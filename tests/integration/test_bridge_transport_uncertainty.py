"""A lost POST can still be running when its readback returns absent."""

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.actions import ActionOwner
from nora.domain.models import ConversationTarget
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint


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
