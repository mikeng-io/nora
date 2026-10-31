"""The platform-bridge runtime's readout routes (design/platform-bridges/04).

Mounted on the health app by `nora/__main__.py` when any platform runs
`mode = "bridge"`, behind the SAME shared bearer every other internal
service route checks (`nora.infra.service_auth.service_auth_ok`): an
operator or monitor reads queue depth through here, and a co-networked
caller without the token gets a 401, never a snapshot.

The bodies are the runtime's own surfaces — `queue_snapshot` is the bounded
inspection (waiting / pending / deferred / retained / uncertain), `health`
the reader-liveness read. Both are already honest about degradation: an
unmeasured section arrives as `unavailable`, never an empty body dressed up
as a quiet queue.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from nora.infra.service_auth import service_auth_ok

log = logging.getLogger("nora.web.bridge")


def register_bridge_routes(app: web.Application, *, runtime: Any) -> None:
    """`runtime` is the `BridgeRuntime` composition built — passed as the
    collaborator itself, not the whole dependency set, for the same reason
    `register_voice` takes `ingest` and `resolve_voice_room`: this module
    never sees a `Dependencies` or a `Policy`."""

    async def bridge_queue(request: web.Request) -> web.Response:
        if not service_auth_ok(request.headers.get("Authorization")):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(await runtime.queue_snapshot())

    async def bridge_health(request: web.Request) -> web.Response:
        if not service_auth_ok(request.headers.get("Authorization")):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await runtime.health()
        return web.json_response(body, status=200 if body["ok"] else 503)

    app.router.add_get("/app/v1/bridge/queue", bridge_queue)
    app.router.add_get("/app/v1/bridge/health", bridge_health)
