"""The reading room's `MemoryScope` — which rooms, people and presence
subjects a memory read may see. `scope_for` derives it from the file-side
policy and roster; `scope_for_async` is the same scope against the selected
identity backend (`nora.access`), where the listed-member sets come from
live room memberships and a backend outage propagates to `unavailable`.

Re-exported through `nora.agent.memory_search`, whose walk consumes the
scope.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from typing import Any

from nora.agent.lineage import section_permits
from nora.domain.lineage import PRESENCE, LineageEntry, RenderRoom
from nora.domain.memory_index import MemoryScope
from nora.domain.rollup import room_key


def scope_for(
    target: RenderRoom, *, social_off: Collection[str], own_room: bool = False,
) -> MemoryScope | None:
    """The reading room's scope, from the live policy (`target.rooms`) and
    its live roster (`target.live`); `None` for a room outside the policy,
    which reads nothing.

    `own_room` narrows it to the reading room alone: no other room of the
    unit and no unit-placed node (a note, memo or event of the unit may come
    from any of its rooms), so nothing said elsewhere is read. The culture
    adaptor's (DEC-316): a room's culture is written from that room's own
    speech. The listed-room and presence rules still apply; a stay or trip, which
    belongs to a person and to no room, is still read where its person
    passes presence here.

    `social_off` are the persons whose `identity.toml` block sets `social =
    false`: none of their social items is read (design/cognition-loop/observation.md),
    whenever it was recorded. Required with no default: a caller that
    forgot it would read every opted-out person's items."""
    here = target.room
    if here is None:
        return None
    unit_rooms = (
        [] if own_room else [r for r in target.rooms if here.unit and r.unit == here.unit]
    )
    if not any(r.platform == here.platform and r.ids == here.ids for r in unit_rooms):
        unit_rooms.append(here)
    room_persons = {
        room_key(r.platform, cid): r.persons or frozenset()
        for r in unit_rooms for cid in sorted(r.ids)
    }
    listed = here.persons or frozenset()
    live = target.live
    vouched = (
        here.persons is not None and live is not None and live.available
        and not live.degraded and live.unnamed == 0
    )
    # Collective cards retain the actual cited presence subjects. Ordinary
    # person nodes still require membership in listed_here inside every read.
    candidates = listed | frozenset(p for members in target.circles for p in members)
    presence = frozenset(
        p for p in candidates
        if section_permits((LineageEntry(PRESENCE, subject=p),), target, origin=None)
    )
    return MemoryScope(
        units=(here.unit,) if here.unit and not own_room else (),
        rooms=tuple(room_persons),
        listed_here=listed,
        people_here=listed | (live.person_ids if live is not None else frozenset()),
        exclude_strict=not vouched,
        presence_persons=presence,
        room_persons=room_persons,
        social_off=frozenset(social_off),
    )


async def scope_for_async(
    target: RenderRoom, *, social_off: Collection[str], own_room: bool = False,
    access: Any = None,
) -> MemoryScope | None:
    """`scope_for` against the selected identity backend. The file path
    defers to `scope_for`; under managed, the listed-member sets come from
    live room memberships (`access.room_refs`), never from a file `persons`
    list — and a backend outage propagates, so the caller answers
    `unavailable` rather than scoping on stale file data."""
    scope = scope_for(target, own_room=own_room, social_off=social_off)
    if scope is None or access is None or getattr(access, "is_file_backend", False):
        return scope
    here = target.room
    if here is None:
        return None
    unit_rooms = (
        [] if own_room else [r for r in target.rooms if here.unit and r.unit == here.unit]
    )
    if not any(r.platform == here.platform and r.ids == here.ids for r in unit_rooms):
        unit_rooms.append(here)
    room_persons: dict[str, frozenset[str]] = {}
    for r in unit_rooms:
        for cid in sorted(r.ids):
            refs = await access.room_refs(r.platform, cid)
            room_persons[room_key(r.platform, cid)] = frozenset(
                ref.person_id for ref in refs
            )
    listed = frozenset(
        pid
        for cid in here.ids
        for pid in room_persons.get(room_key(here.platform, cid), frozenset())
    )
    live = target.live
    candidates = listed | frozenset(p for members in target.circles for p in members)
    presence = frozenset(
        p for p in candidates
        if section_permits((LineageEntry(PRESENCE, subject=p),), target, origin=None)
    )
    return replace(
        scope,
        listed_here=listed,
        people_here=listed | (live.person_ids if live is not None else frozenset()),
        presence_persons=presence,
        room_persons=room_persons,
    )
