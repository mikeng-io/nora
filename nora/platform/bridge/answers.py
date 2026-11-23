"""The canonical service API's answers on an aiohttp surface: envelope rendering
and one process's operation ledger, with nothing of a bridge's storage.

The Python bridges' `platform` routes (`contract.py`) and the `voice` and
`intake` routes the Discord bridge and the app serve all answer through these.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from nora.domain.service_api import (
    OperationConflictError,
    OperationOutcome,
    ReadOutcome,
    status_for,
)

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


def envelope_response(body: Mapping[str, Any]) -> web.Response:
    """An envelope under the status its outcome owns — never another."""
    return web.json_response(dict(body), status=status_for(body["outcome"]))


#: The scope every voice and intake answer resolves to.
INSTALLATION: Mapping[str, Any] = {"level": "installation"}


class InvalidOperation(ValueError):  # noqa: N818 — names the answer, a 400
    """The request names no effect that can apply: a 400 with no envelope."""


def now_ms() -> int:
    return int(time.time() * 1000)


def settled_body(outcome: OperationOutcome, payload: object, *, target: str, trace: str,
                 started_ms: int) -> dict[str, Any]:
    """A settled operation envelope at installation scope."""
    return {"outcome": outcome, "payload": payload,
            "when": {"state": "settled", "started_at_ms": started_ms,
                     "settled_at_ms": now_ms()},
            "from": {"target": target, "platform_ref": None},
            "scope": dict(INSTALLATION), "trace": trace}


async def operation_request(req: web.Request, kinds: frozenset[str], *,
                            max_bytes: int | None = None
                            ) -> tuple[str, str, dict[str, Any]] | web.Response:
    """An operation request's id, kind and effect, or its 400 or 422 answer."""
    try:
        raw = await (req.clone(client_max_size=max_bytes) if max_bytes else req).read()
        body = json.loads(raw)
    except web.HTTPRequestEntityTooLarge:
        return web.json_response({"error": "body too large"}, status=400)
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)
    if not isinstance(body.get("kind"), str) or body["kind"] not in kinds:
        return web.json_response({"error": "unknown_kind"}, status=422)
    operation_id, effect = body.get("operation_id"), body.get("effect")
    if not isinstance(operation_id, str) or not operation_id or not isinstance(effect, dict):
        return web.json_response({"error": "operation_id and effect are required"},
                                 status=400)
    return operation_id, str(body["kind"]), effect


@dataclass
class _Entry:
    digest: str
    answer: asyncio.Future[dict[str, Any]]
    pending: dict[str, Any]
    #: `None` while the operation runs; five minutes from settlement once it has.
    expires_at: float | None = None


class OperationLedger:
    """One process's operations by `operation_id`, each answer kept five minutes.

    An entry holds a hash of the kind and effect and the answered envelope, never
    the effect itself, so a `speak` keeps no audio. An id is reserved before its
    effect runs and nothing is dropped before its time: with every slot held,
    a new operation is refused `unavailable` before any effect.
    """

    def __init__(self, *, ttl_s: float = 300.0, bound: int = 4096,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_s = ttl_s
        self._bound = bound
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    def _expire(self) -> None:
        now = self._clock()
        for operation_id in [k for k, e in self._entries.items()
                             if e.expires_at is not None and e.expires_at <= now]:
            del self._entries[operation_id]

    async def run(self, operation_id: str, kind: str, effect: object,
                  work: Callable[[], Awaitable[dict[str, Any]]],
                  pending: dict[str, Any]) -> dict[str, Any]:
        """The envelope answered for this id, running `work` once per id and effect.

        `pending` is the operation's envelope while it runs. Raises
        `OperationConflictError` for the id reused with another kind or effect;
        an exception from `work` (an `InvalidOperation`) keeps nothing.
        """
        self._expire()
        digest = hashlib.sha256(json.dumps(
            [kind, effect], sort_keys=True, default=str).encode()).hexdigest()
        held = self._entries.get(operation_id)
        if held is not None:
            if held.digest != digest:
                raise OperationConflictError(operation_id)
            return await asyncio.shield(held.answer)
        if len(self._entries) >= self._bound:
            return _refused(pending)
        entry = _Entry(digest, asyncio.get_running_loop().create_future(), pending)
        self._entries[operation_id] = entry
        try:
            envelope = await work()
        except BaseException as exc:
            if self._entries.get(operation_id) is entry:
                del self._entries[operation_id]
            if isinstance(exc, Exception):
                entry.answer.set_exception(exc)
                entry.answer.exception()
            else:
                entry.answer.cancel()
            raise
        entry.expires_at = self._clock() + self._ttl_s
        entry.answer.set_result(envelope)
        return envelope

    def get(self, operation_id: str) -> dict[str, Any] | None:
        """The envelope kept for an id, its `pending` one while it runs, or `None`."""
        self._expire()
        held = self._entries.get(operation_id)
        if held is None:
            return None
        return held.answer.result() if held.answer.done() else held.pending

    async def respond(self, req: web.Request, operation_id: str, kind: str, effect: object,
                      work: Callable[[], Awaitable[dict[str, Any]]],
                      pending: dict[str, Any]) -> web.Response:
        """`run` on the wire: the envelope, a 409 for a reused id, or a 400."""
        try:
            return envelope_response(await self.run(operation_id, kind, effect, work, pending))
        except OperationConflictError:
            return web.json_response({"error": "operation_id_reused"}, status=409)
        except InvalidOperation as exc:
            return web.json_response({"error": str(exc)}, status=400)

    def read(self, req: web.Request, *, source: str) -> web.Response:
        """`GET …/operations/{operation_id}`: `ok` with the kept envelope, or `nothing_found`."""
        envelope = self.get(req.match_info["operation_id"])
        return envelope_response(read_body(
            "ok" if envelope is not None else "nothing_found", envelope, source=source,
            trace=trace_of(req.headers), scope=INSTALLATION))


def pending_body(payload: Mapping[str, Any], *, target: str, trace: str,
                 started_ms: int) -> dict[str, Any]:
    """An operation's envelope while it runs: `pending`, `started`, not settled."""
    return {"outcome": "pending", "payload": dict(payload),
            "when": {"state": "started", "started_at_ms": started_ms, "settled_at_ms": None},
            "from": {"target": target, "platform_ref": None},
            "scope": dict(INSTALLATION), "trace": trace}


def _refused(pending: Mapping[str, Any]) -> dict[str, Any]:
    """`unavailable` for an operation the full ledger could not hold; nothing ran."""
    error = {"kind": "ledger_full", "message": "too many operations in flight"}
    return {**pending, "outcome": "unavailable",
            "payload": {**pending["payload"], "error": error},
            "when": {**pending["when"], "state": "settled", "settled_at_ms": now_ms()}}


def report_answer(status: int, answer: Mapping[str, Any]
                  ) -> tuple[OperationOutcome, dict[str, Any]]:
    """A hub's answer to a controller report as its outcome and payload.

    A 2xx is `complete` with the hub's own answer; a 400 is `InvalidOperation`.
    """
    if 200 <= status < 300:
        return "complete", dict(answer)
    if status == 400:
        raise InvalidOperation("the report carries no usable event")
    outcome: OperationOutcome = "denied" if status == 403 else "unavailable"
    return outcome, {"error": {"kind": outcome, "message": str(answer.get("error") or "")}}
