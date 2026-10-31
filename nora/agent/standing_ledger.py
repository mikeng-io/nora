"""Per-entry ledger disclosure and the dense prompt projection."""

from __future__ import annotations

from nora.agent.lineage import excluded, section_permits
from nora.agent.room import Room
from nora.domain.lineage import ROW, LiveRoom, RenderRoom, room_key
from nora.domain.standing_ledger import LedgerEntry
from nora.domain.turn_record import DisclosureRequirement


def ledger_permits(entry: LedgerEntry, target: RenderRoom) -> bool:
    room, live = target.room, target.live
    if room is None or room.unit != entry.unit:
        return False
    if entry.scope == "room" and not room.is_room(room_key(entry.platform, entry.conversation_id)):
        return False
    if live is None or not live.available or live.degraded or live.unnamed or not live.person_ids:
        return False
    requirement = DisclosureRequirement(
        room_key(entry.platform, entry.conversation_id),
        entry.lineage.section(ROW),
        entry.exclude_persons,
    )
    return bool(
        requirement.known
        and not excluded(entry.exclude_persons, target)
        and section_permits(requirement.entries, target, origin=requirement.origin)
    )


async def ledger_target(target: RenderRoom, room: Room) -> RenderRoom:
    view = await room.scope()
    return target.with_live(
        LiveRoom(
            person_ids=frozenset(p.person_id for p in view.people),
            unnamed=view.unnamed,
            available=view.available,
            degraded=view.degraded,
        )
    )


