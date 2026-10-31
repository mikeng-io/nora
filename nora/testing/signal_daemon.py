"""A fake signal-cli HTTP daemon for route/capture tests — real wire, real JSON-RPC.

`nora.platform.signal.send.SignalSender`, `SignalReactor` and the bridge
wrapper's `_rpc`/`capture` paths all speak HTTP to signal-cli's daemon mode:
`POST /api/v1/rpc` for JSON-RPC methods and `GET /api/v1/events` for the SSE
capture stream. This double serves both over REAL aiohttp, so a test drives
the same client code production runs instead of a fake that bypasses it.

Failure knobs model what the real daemon does, not invented shapes:

- `drop_next_rpc` — severs the connection without answering (the deployed
  "send landed but the answer was lost" window: the client's `ok=False`
  genuinely cannot say whether the effect applied);
- `rpc_errors` — a JSON-RPC `error` body for a named method (the daemon
  answering a bad call, distinct from a transport failure).

Every accepted call is recorded (`sent`, `reactions`, `attachments`) so a
test asserts on upstream EFFECTS, not on internal bookkeeping.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from aiohttp import web


class FakeSignalDaemon:
    """signal-cli daemon-mode double: `/api/v1/events` SSE + `/api/v1/rpc`."""

    def __init__(self, *, attachments: dict[str, bytes] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.reactions: list[dict[str, Any]] = []
        self.attachment_reads: list[str] = []
        self.rpc_calls: list[str] = []
        # `drop_next_rpc` severs the connection that many times — the real
        # "answer lost" window. `rpc_errors` answers a named method with a
        # JSON-RPC error instead of a result.
        self.drop_next_rpc = 0
        self.rpc_errors: set[str] = set()
        self._attachments = dict(attachments or {})
        self._events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    # ---- test surface ------------------------------------------------------

    async def emit(self, event: dict[str, Any]) -> None:
        """Queue one signal-cli receive event onto the SSE stream."""
        await self._events.put(event)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/v1/events", self.events)
        app.router.add_post("/api/v1/rpc", self.rpc)
        return app

    # ---- the wire -----------------------------------------------------------

    async def events(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        while True:
            event = await self._events.get()
            if event is None:  # test closed the stream
                break
            try:
                await resp.write(
                    f"data: {json.dumps(event)}\n\n".encode())
            except (ConnectionError, asyncio.CancelledError):
                break
        return resp

    async def rpc(self, request: web.Request) -> web.Response:
        body = await request.json()
        method = str(body.get("method"))
        params = dict(body.get("params") or {})
        self.rpc_calls.append(method)
        response = self._dispatch(method, params, body.get("id"))
        if self.drop_next_rpc and method in ("send", "sendReaction"):
            # The effect ABOVE already ran — only the answer is lost. That is
            # exactly the deployed ambiguity the bridge must record as
            # `uncertain`: it cannot say the effect did not land.
            self.drop_next_rpc -= 1
            raise ConnectionResetError("answer lost")
        return response

    def _dispatch(self, method: str, params: dict[str, Any],
                  request_id: Any) -> web.Response:
        if method in self.rpc_errors:
            return web.json_response({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32000, "message": f"{method} refused"}})
        if method == "send":
            self.sent.append({"params": params, "id": request_id})
            return web.json_response(
                {"jsonrpc": "2.0", "id": request_id,
                 "result": {"timestamp": 1700000000001}})
        if method == "sendReaction":
            self.reactions.append({"params": params, "id": request_id})
            return web.json_response(
                {"jsonrpc": "2.0", "id": request_id, "result": {}})
        if method == "getAttachment":
            aid = str(params.get("id"))
            self.attachment_reads.append(aid)
            raw = self._attachments.get(aid, b"")
            return web.json_response(
                {"jsonrpc": "2.0", "id": request_id,
                 "result": {"data": base64.b64encode(raw).decode()}})
        if method == "listGroups":
            return web.json_response(
                {"jsonrpc": "2.0", "id": request_id, "result": []})
        if method == "getUserStatus":
            return web.json_response(
                {"jsonrpc": "2.0", "id": request_id,
                 "result": {"isRegistered": True}})
        return web.json_response({
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"unknown method {method}"}})
