"""The room-filtered turn view. The model-facing read ships at a later step."""

from __future__ import annotations

from typing import Any

from nora.agent.lineage import excluded, profile_lines_for, section_permits
from nora.agent.room import Room
from nora.domain.lineage import KINDS, LiveRoom, RenderRoom, message, room_key
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_record import DisclosureRequirement, turn_items


async def _permits(req: DisclosureRequirement, target: RenderRoom, room: Room) -> bool:
    def known(entries: Any) -> bool:
        return bool(entries) and all(
            e.kind in KINDS and (not e.reads or known(e.reads)) for e in entries
        )

    if not req.resolved or not known(req.entries):
        return False
    if excluded(req.exclude_persons, target):
        return False
    if not section_permits(req.entries, target, origin=req.origin):
        return False
    return bool(await profile_lines_for((req,), target, room))


async def turn_record_view(trace: TagTrace | None, target: RenderRoom, room: Room) -> dict:
    """Another unit and an absent handle have exactly the same answer."""
    unknown = {"status": "nothing_found"}
    if trace is None or target.room is None or not trace.unit or trace.unit != target.room.unit:
        return unknown
    try:
        live = await room.scope()
    except Exception:  # noqa: BLE001 - a failed roster read withholds every item
        live = None
    target = target.with_live(LiveRoom(
        person_ids=frozenset(p.person_id for p in live.people) if live else frozenset(),
        available=live.available if live else False,
        degraded=live.degraded if live else True, unnamed=live.unnamed if live else 0,
    ))
    shown, withheld = [], 0
    for item in turn_items(trace.messages, trace.requirements):
        permits = bool(item.requirements) and bool(
            live and live.available and not live.degraded and not live.unnamed
        )
        for req in item.requirements:
            try:
                permits = await _permits(req, target, room) and permits
            except Exception:  # noqa: BLE001 - a requirement that cannot be read denies
                permits = False
        if permits:
            shown.append({"tool": item.name, "input": item.arguments, "result": item.result})
        else:
            withheld += 1
    origin = room_key(trace.platform, trace.conversation_id)
    sent = trace.sent_message_ids if live and live.available and section_permits(
        (message(origin),), target, origin=origin,
    ) else ()
    status = "unavailable" if live is None or not live.available or live.degraded else "ok"
    return {"status": status, "id": trace.handle, "occasion": trace.occasion,
            "started_at": trace.started_at, "outcome": trace.outcome,
            "items": shown, "withheld": withheld, "sent_message_ids": list(sent)}
