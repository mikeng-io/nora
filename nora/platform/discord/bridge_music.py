"""Discord's platform music operations and queue status."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from aiohttp import web

from nora.domain.ports import MusicToolBridge, MusicToolOutcome
from nora.domain.service_api import OperationOutcome
from nora.platform.bridge.answers import (
    OperationLedger,
    now_ms,
    pending_body,
    scope_of,
    settled_body,
    trace_of,
)
from nora.platform.bridge.contract import QueryAnswer

from . import music_messages as m

log = logging.getLogger(__name__)

_NOTE_ERRORS: dict[tuple[str, str], tuple[OperationOutcome, str]] = {
    ("nothing_found", m.nothing_found_line()): ("rejected", "nothing_found"),
    ("nothing_found", m.nothing_playing_line()): ("rejected", "nothing_playing"),
    ("nothing_found", m.nothing_queued_at_position_line()): (
        "rejected", "nothing_queued_at_position"),
    ("unavailable", m.disconnected_note()): ("unavailable", "disconnected"),
    ("unavailable", m.not_in_voice_note()): ("unavailable", "not_in_voice"),
    ("unavailable", m.join_failed_note()): ("unavailable", "join_failed"),
    ("unavailable", m.not_in_allowed_guild_note()): ("unavailable", "not_in_allowed_guild"),
    ("unavailable", m.volume_out_of_range_line()): ("unavailable", "volume_out_of_range"),
}


def _error(kind: str) -> dict[str, str]:
    return {"kind": kind, "message": ""}


class BridgeMusicSurface:
    """Admit and apply music effects once over the bridge's live tool."""

    def __init__(
        self, *, tool: MusicToolBridge,
        admit_room: Callable[[str], bool],
        admit_requester: Callable[[str, Iterable[str]], bool],
        ledger: OperationLedger | None = None,
    ) -> None:
        self._tool = tool
        self._admit_room = admit_room
        self._admit_requester = admit_requester
        self._ledger = ledger if ledger is not None else OperationLedger(full_message="")

    async def submit(self, req: web.Request, operation_id: str, kind: str,
                     effect: dict[str, Any]) -> web.Response:
        room = effect["conversation_id"]
        metadata: dict[str, Any] = {
            "target": room, "scope": scope_of(room), "trace": trace_of(req.headers),
            "started_ms": now_ms()}

        async def work() -> dict[str, Any]:
            try:
                outcome, payload = await self._apply(kind, room, effect)
            except Exception as exc:  # noqa: BLE001 — music boundary; logged by type
                log.error("music operation %s failed: %s", kind, type(exc).__name__)
                outcome, payload = "unavailable", {"error": _error("music_failed")}
            return settled_body(outcome, payload, **metadata)

        return await self._ledger.respond(
            req, operation_id, kind, effect, work, pending_body({}, **metadata))

    def receipt(self, operation_id: str) -> dict[str, Any] | None:
        return self._ledger.get(operation_id)

    async def status(self, conversation_id: str) -> QueryAnswer:
        if not self._admit_room(conversation_id):
            return QueryAnswer("nothing_found")
        try:
            result = await self._tool.status(conversation_id=conversation_id)
        except Exception as exc:  # noqa: BLE001 — music boundary; logged by type
            log.error("music status failed: %s", type(exc).__name__)
            return QueryAnswer("unavailable")
        if result.status == "nothing_found":
            return QueryAnswer("nothing_found")
        if result.status == "ok" and result.title is not None and result.url is not None:
            return QueryAnswer("ok", {"title": result.title, "url": result.url})
        return QueryAnswer("unavailable")

    async def _apply(self, kind: str, room: str, effect: dict[str, Any]
                     ) -> tuple[OperationOutcome, dict[str, Any]]:
        if not self._admit_room(room):
            return "denied", {"error": _error("room_not_admitted")}
        if kind == "music_play" and not self._admit_requester(room, effect["sender_ids"]):
            return "denied", {"error": _error("sender_not_admitted")}
        result = await self._call_tool(kind, room, effect)
        outcome, error_kind = ("complete", None) if result.status == "ok" else _NOTE_ERRORS.get(
            (result.status, result.note), ("unavailable", "music_failed"))
        error = _error(error_kind) if error_kind is not None else None
        if outcome == "unavailable":
            return outcome, {"error": error}
        percent = int(effect["percent"]) if kind == "music_set_volume" else None
        return outcome, {"title": result.title, "url": result.url, "queued": result.queued,
                         "percent": percent, "error": error}

    async def _call_tool(self, kind: str, room: str, effect: dict[str, Any]) -> MusicToolOutcome:
        match kind:
            case "music_play":
                return await self._tool.play(
                    conversation_id=room, sender_ids=frozenset(effect["sender_ids"]),
                    query_or_url=effect["query_or_url"])
            case "music_skip":
                return await self._tool.skip(conversation_id=room)
            case "music_top":
                return await self._tool.top(conversation_id=room, position=int(effect["position"]))
            case "music_stop":
                return await self._tool.stop(conversation_id=room)
            case "music_set_volume":
                return await self._tool.set_volume(
                    conversation_id=room, percent=int(effect["percent"]))
        raise ValueError("unknown music operation")
