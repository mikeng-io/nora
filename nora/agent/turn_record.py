"""The room-filtered turn view. The model-facing read ships at a later step."""

from __future__ import annotations

from nora.agent.lineage import excluded, profile_lines_for, section_permits
from nora.agent.room import Room
from nora.domain.lineage import LiveRoom, RenderRoom, message, room_key
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_record import DisclosureRequirement, turn_items


async def _permits(req: DisclosureRequirement, target: RenderRoom, room: Room) -> bool | None:
    if not req.known:
        return None
    if excluded(req.exclude_persons, target):
        return False
    if not section_permits(req.entries, target, origin=req.origin):
        return False
    return bool(await profile_lines_for((req,), target, room))


async def turn_record_view(trace: TagTrace | None, target: RenderRoom, room: Room) -> dict:
    """Another unit and an absent handle have exactly the same answer."""
    absent = {"status": "nothing_found"}
    if trace is None or target.room is None or not trace.unit or trace.unit != target.room.unit:
        return absent
    try:
        live = await room.scope()
    except Exception:  # noqa: BLE001 - a failed roster read withholds every item
        live = None
    target = target.with_live(LiveRoom(
        person_ids=frozenset(p.person_id for p in live.people) if live else frozenset(),
        available=live.available if live else False,
        degraded=live.degraded if live else True, unnamed=live.unnamed if live else 0,
    ))
    shown, withheld, unresolved = [], 0, 0
    audience_known = bool(live and live.available and not live.degraded and not live.unnamed)
    items = turn_items(trace.messages, trace.requirements)
    for item in items:
        permits = True
        unknown = not item.requirements or not audience_known
        for req in item.requirements:
            try:
                result = await _permits(req, target, room)
                unknown = unknown or result is None
                permits = permits and result is True
            except Exception:  # noqa: BLE001 - failed requirement reads are unresolved
                unknown = True
        if permits and not unknown:
            shown.append({"tool": item.name, "input": item.arguments, "result": item.result})
        else:
            withheld += 1
            unresolved += int(unknown)
    origin = room_key(trace.platform, trace.conversation_id)
    sent = trace.sent_message_ids if live and live.available and section_permits(
        (message(origin),), target, origin=origin,
    ) else ()
    status = ("unavailable" if not audience_known or (items and unresolved == len(items))
              else "partial" if unresolved else "ok")
    return {"status": status, "id": trace.handle, "occasion": trace.occasion,
            "started_at": trace.started_at, "outcome": trace.outcome,
            "items": shown, "withheld": withheld, "unavailable": unresolved,
            "note": "Some item requirements could not be checked; do not infer absence."
                    if unresolved else "All item requirements were checked.",
            "sent_message_ids": list(sent)}
