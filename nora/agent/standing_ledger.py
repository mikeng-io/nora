"""Per-entry ledger disclosure and the dense prompt projection."""

from __future__ import annotations

from datetime import datetime

from nora.agent.lineage import excluded, section_permits
from nora.agent.room import Room
from nora.domain.blocks import ContextBlock
from nora.domain.lineage import ROW, LiveRoom, RenderRoom, RoomFacts, room_key
from nora.domain.prompt import age, escape
from nora.domain.standing_ledger import LedgerEntry, StandingLedger
from nora.domain.turn_record import DisclosureRequirement
from nora.language.computer_render import estimate_tokens


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


def render_ledger(
    entries: tuple[LedgerEntry, ...],
    *,
    now: datetime,
    section_tokens: int = 600,
    rooms: tuple[RoomFacts, ...] = (),
    closed_horizon_ms: int = 7 * 86400000,
) -> ContextBlock | None:
    """A render budget never changes stored entries; standing_read is unabridged."""
    # ponytail: provisional budgets and seven-day closed horizon; size from the first month.
    if not entries:
        return None
    now_ms = int(now.timestamp() * 1000)
    sections = []
    for kind, title in (("decision", "Decisions"), ("commitment", "I owe"), ("thread", "Threads")):
        lines, tokens, omitted = [], 0, 0
        for e in sorted(
            (e for e in entries if e.kind == kind),
            key=lambda e: (e.last_moved_at or e.last_revised_at, e.id),
            reverse=True,
        ):
            if e.status != "open" and now_ms - e.last_revised_at > closed_horizon_ms:
                omitted += 1
                continue
            moved = e.last_moved_at if e.last_moved_at is not None else e.last_revised_at
            line = f"{e.id} · {e.status} · {age(moved, now)} · r{e.revision} · {e.subject}"
            if e.note_id:
                line += f" · note {e.note_id}"
            if e.body:
                from nora.domain.note_body import split_sections

                schema = (
                    ("Options", "Criteria", "Leaning", "Blocked on", "Reopen if", "Closed as")
                    if kind == "decision"
                    else ("Owed", "Done as")
                )
                for heading, items in split_sections(e.body, schema=schema):
                    line += f"\n  {heading}: " + " · ".join(items)
            if e.kind == "thread":
                labels = [
                    r.label or r.platform
                    for span in e.spans
                    for r in rooms
                    if r.platform == span.platform and span.conversation_id in r.ids
                ]
                if labels:
                    line += " · " + ", ".join(dict.fromkeys(labels))
                line += " · " + ", ".join(e.decision_ids + tuple(f"note {n}" for n in e.note_ids))
            count = estimate_tokens(line)
            if tokens + count > section_tokens:
                omitted += 1
                continue
            lines.append(line)
            tokens += count
        if omitted:
            lines.append(f"+{omitted} older {title.lower()} (standing_read has all entries)")
        if lines:
            sections.append(f"## {title}\n" + "\n".join(lines))
    return (
        ContextBlock(name="standing_ledger", body=escape("\n".join(sections))) if sections else None
    )


async def read_ledger(
    store: StandingLedger,
    target: RenderRoom,
    room: Room,
) -> tuple[LedgerEntry, ...]:
    if target.room is None:
        return ()
    entries = await store.read(
        unit=target.room.unit, platform=room.platform, conversation_id=target.room.primary_id
    )
    if not entries:
        return ()
    target = await ledger_target(target, room)
    return tuple(e for e in entries if ledger_permits(e, target))
