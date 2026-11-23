"""The Python bridges' edge onto the `platform` contract
(`nora.domain.service_api_platform`, `docs/service-api/openapi.json`).

One rendering of a stored action row into an operation envelope, one read
envelope, and the routes Signal and Discord both serve. Storage keeps its own
words (`confirmed`, `started`, ...); they become contract outcomes only here.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from os.path import realpath
from pathlib import Path
from typing import Any, Protocol, get_args

from aiohttp import web

from nora.domain.service_api import OperationOutcome, ReadOutcome, status_for
from nora.domain.service_api_platform import (
    SELF_ACCOUNT_ID,
    PlatformOperationKind,
    PlatformQueryKind,
)
from nora.platform.bridge import telemetry as bt
from nora.platform.bridge.service import ActionReply, reply_from_row
from nora.platform.bridge.wire import attachment_key_ok

OPERATION_KINDS = frozenset(get_args(PlatformOperationKind))
QUERY_KINDS = frozenset(get_args(PlatformQueryKind))
#: How a read's `observed_at_ms` was known: the bridge's own clock.
KNOWN_BY = "bridge_clock"


def trace_of(headers: Mapping[str, str]) -> str:
    """The trace id a request's W3C `traceparent` carries, or ``""``."""
    parts = (headers.get("traceparent") or "").split("-")
    trace = parts[1] if len(parts) == 4 else ""
    valid = len(trace) == 32 and trace.strip("0") and all(
        c in "0123456789abcdef" for c in trace)
    return trace if valid else ""


def scope_of(conversation_id: object) -> dict[str, Any]:
    """`room` when the request names one conversation, else `installation`."""
    if isinstance(conversation_id, str) and conversation_id:
        return {"level": "room", "id": conversation_id}
    return {"level": "installation", "id": None}


def read_body(outcome: ReadOutcome, payload: object, *, source: str, trace: str,
              observed_ms: int | None = None,
              scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A read envelope; `payload` is null unless the outcome is `ok`."""
    return {
        "outcome": outcome, "payload": payload if outcome == "ok" else None,
        "when": {"observed_at_ms": observed_ms or int(time.time() * 1000),
                 "known_by": KNOWN_BY},
        "from": {"source": source, "healthy": outcome != "unavailable",
                 "failed_sources": []},
        "scope": dict(scope or scope_of(None)), "trace": trace}


def _operation_outcome(reply: ActionReply) -> OperationOutcome | None:
    """A reply's contract outcome; `None` is an answer with no envelope (400, 409)."""
    match reply.state:
        case "confirmed":
            return "complete"
        case "uncertain":
            return "uncertain"
        case "prepared" | "started":
            return "pending"
        case "unavailable":
            return "unavailable"
        case "rejected" if reply.kind == "bad_request":
            return None
        case "rejected" if reply.kind == "unsupported":
            return "unsupported"
        case "rejected" if reply.kind in ("owner", "fenced"):
            return "denied"
        case "rejected":
            return "rejected"
    return None


def operation_body(reply: ActionReply, *, target: str, trace: str
                   ) -> dict[str, Any] | None:
    """The operation envelope for a reply, or `None` when it has no envelope."""
    outcome = _operation_outcome(reply)
    if outcome is None:
        return None
    return {
        "outcome": outcome,
        "payload": {
            "platform_refs": list(reply.platform_refs), "platform_ms": reply.platform_ms,
            "message_id": reply.message_id,
            "error": ({"kind": reply.kind, "message": reply.message}
                      if reply.kind else None)},
        "when": {"state": reply.state if outcome == "pending" else "settled",
                 "started_at_ms": reply.started_ms, "settled_at_ms": reply.settled_ms},
        "from": {"target": target, "platform_ref": reply.platform_ref},
        "scope": scope_of(target), "trace": trace}


def envelope_response(body: Mapping[str, Any]) -> web.Response:
    """An envelope under the status its outcome owns — never another."""
    return web.json_response(dict(body), status=status_for(body["outcome"]))


def operation_response(reply: ActionReply, *, target: str, trace: str) -> web.Response:
    """A reply on the wire: its envelope, or 409/400 with no envelope."""
    body = operation_body(reply, target=target, trace=trace)
    if body is not None:
        return envelope_response(body)
    if reply.state == "conflict":
        return web.json_response({"error": "operation_id reused"}, status=409)
    return web.json_response({"error": reply.message or "bad request"}, status=400)


def effect_target(effect: object) -> str:
    target = effect.get("target") if isinstance(effect, Mapping) else None
    return str((target or {}).get("id") or "") if isinstance(target, Mapping) else ""


def action_of(body: Mapping[str, Any]) -> dict[str, Any]:
    """An operation request as the action the ledger stores; the effect is hashed alone."""
    return {"action_id": body.get("operation_id"), "owner": body.get("owner"),
            "op": body.get("kind"), "request": body.get("effect")}


@dataclass(frozen=True)
class QueryAnswer:
    """A query's answer: `ok` carries the kind's value."""

    outcome: ReadOutcome
    value: object = None
    observed_ms: int | None = None


class ActionService(Protocol):
    """The ledger behind the operations: `DurableActionService`, or a test's stand-in."""

    async def run(self, action: Mapping[str, Any], *,
                  links: list[Any] | None = None) -> ActionReply: ...

    async def get(self, action_id: str) -> dict[str, Any] | None: ...


class PlatformSurface:
    """The platform contract's routes and the root probes, over one bridge's parts.

    `account` answers the bridge's own account or `None` before login; `status`
    the `BridgeStatus` fields; `query` a closed query kind; `custody_dir` where
    blob bytes rest, `None` when custody is off.
    """

    def __init__(
        self, *, platform: str,
        actions: Callable[[], ActionService | None],
        account: Callable[[], Awaitable[Mapping[str, Any] | None]],
        status: Callable[[], Awaitable[Mapping[str, Any]]],
        query: Callable[[str, Mapping[str, Any]], Awaitable[QueryAnswer]],
        custody_dir: Callable[[], Path | None],
        max_blob_bytes: Callable[[], int],
    ) -> None:
        self._platform = platform
        self._actions = actions
        self._account = account
        self._status = status
        self._query = query
        self._custody_dir = custody_dir
        self._max_blob_bytes = max_blob_bytes

    def add_routes(self, app: web.Application) -> None:
        app.router.add_get("/health", self.health)
        app.router.add_get("/ready", self.ready)
        app.router.add_get("/v1/operations/status", self.read_status)
        app.router.add_post("/v1/platform/operations", self.submit_operation)
        app.router.add_get("/v1/platform/operations/{operation_id}", self.read_operation)
        app.router.add_post("/v1/platform/queries", self.post_query)
        app.router.add_get("/v1/platform/accounts/{account_id}", self.read_account)
        app.router.add_put("/v1/platform/blobs/{key}", self.put_blob)
        app.router.add_get("/v1/platform/blobs/{key}", self.get_blob)

    def _read(self, req: web.Request, outcome: ReadOutcome, payload: object = None,
              **kw: Any) -> web.Response:
        return envelope_response(read_body(
            outcome, payload, source=self._platform, trace=trace_of(req.headers), **kw))

    async def health(self, req: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def ready(self, req: web.Request) -> web.Response:
        status = dict(await self._status())
        return web.json_response(status, status=200 if status.get("ok") else 503)

    async def read_status(self, req: web.Request) -> web.Response:
        return self._read(req, "ok", dict(await self._status()))

    async def submit_operation(self, req: web.Request) -> web.Response:
        try:
            body = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
        if body.get("kind") not in OPERATION_KINDS:
            return web.json_response({"error": "unknown operation kind"}, status=422)
        actions = self._actions()
        if actions is None:
            reply = ActionReply("unavailable", kind="unavailable", message="no action service")
        else:
            reply = await actions.run(action_of(body), links=bt.carrier_links({
                "traceparent": req.headers.get("traceparent") or "",
                "tracestate": req.headers.get("tracestate") or ""}))
        return operation_response(
            reply, target=effect_target(body.get("effect")), trace=trace_of(req.headers))

    async def read_operation(self, req: web.Request) -> web.Response:
        actions = self._actions()
        if actions is None:
            return self._read(req, "unavailable")
        try:
            row = await actions.get(req.match_info["operation_id"])
        except Exception:  # noqa: BLE001 — an unreadable ledger is unavailable, not absent
            return self._read(req, "unavailable")
        if row is None:
            return self._read(req, "nothing_found")
        target = effect_target(row.get("request"))
        return self._read(req, "ok", operation_body(
            reply_from_row(row), target=target, trace=trace_of(req.headers)),
            scope=scope_of(target))

    async def post_query(self, req: web.Request) -> web.Response:
        try:
            body = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("params") or {}, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
        kind, params = body.get("kind"), body.get("params") or {}
        if kind not in QUERY_KINDS:
            return web.json_response({"error": "unknown query kind"}, status=422)
        answer = await self._query(str(kind), params)
        return self._read(req, answer.outcome, answer.value, observed_ms=answer.observed_ms,
                          scope=scope_of(params.get("conversation_id")))

    async def read_account(self, req: web.Request) -> web.Response:
        if req.match_info["account_id"] != SELF_ACCOUNT_ID:
            return self._read(req, "nothing_found")
        account = await self._account()
        return self._read(req, "ok", dict(account)) if account else self._read(req, "unavailable")

    def _blob_path(self, key: str) -> Path | None:
        """The key's file in custody, `None` with custody off; a stray key is a 400."""
        custody = self._custody_dir()
        if not attachment_key_ok(key):
            raise web.HTTPBadRequest(text="bad key")
        if custody is None:
            return None
        root = realpath(custody) + "/"
        path = realpath(root + key)
        if not path.startswith(root):
            raise web.HTTPBadRequest(text="bad key")
        return Path(path)

    def _blob_settled(self, req: web.Request, outcome: OperationOutcome,
                      payload: object) -> web.Response:
        return envelope_response({
            "outcome": outcome, "payload": payload,
            "when": {"state": "settled", "started_at_ms": None, "settled_at_ms": None},
            "from": {"target": req.match_info["key"], "platform_ref": None},
            "scope": scope_of(None), "trace": trace_of(req.headers)})

    async def put_blob(self, req: web.Request) -> web.Response:
        key = req.match_info["key"]
        if (path := self._blob_path(key)) is None:
            return self._blob_settled(req, "unavailable", None)
        limit = self._max_blob_bytes()
        try:
            data = await req.clone(client_max_size=limit + 1).read()
        except web.HTTPRequestEntityTooLarge:
            data = None
        if data is None or len(data) > limit:
            return self._blob_settled(req, "rejected", {"error": {
                "kind": "too_large", "message": f"over {limit} bytes"}})
        if path.is_file() and path.read_bytes() != data:
            return self._blob_settled(req, "rejected", {"error": {
                "kind": "conflict", "message": "the key holds other bytes"}})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._blob_settled(req, "complete", {"key": key})

    async def get_blob(self, req: web.Request) -> web.Response:
        if (path := self._blob_path(req.match_info["key"])) is None:
            return self._read(req, "unavailable")
        if not path.is_file():
            return self._read(req, "nothing_found")
        try:
            data = path.read_bytes()
        except OSError:
            return self._read(req, "unavailable")
        return web.Response(body=data, content_type="application/octet-stream")
