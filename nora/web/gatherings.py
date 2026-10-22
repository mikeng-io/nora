"""JSON payload builders for the read-only Gathering web surface. Plain async
functions over typed collaborators, matching `nora.web.memory`'s shape
(`build_memory_payload`) rather than `nora.web.games`'s remote-client-wrapper
shape — Gathering's data is local Postgres via `GatheringService`, not a remote
REST API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from nora.app.gatherings import GatheringService
from nora.domain.documents import RoomKey
from nora.domain.gatherings import Gathering

#: `rooms_of(person_id)`: every room naming the person, every id it answers to.
RoomsOf = Callable[[str], frozenset[RoomKey]]
#: `shown(event, viewer, platform, conversation_id)`: whether a member of that room
#: who neither made nor answered the event may see it (`nora.app.gatherings.room_event_shown`).
RoomShown = Callable[[Gathering, str, str, str], bool]


def _room_forms(rooms_of: RoomsOf | None, person_id: str) -> dict[str, RoomKey]:
    """Each conversation id an event of the viewer's rooms can be stored under,
    the bare id and its `platform:` form, mapped to the room it names. `events`
    now stores the platform too, but the form is still tried because a row's
    `conversation_id` may be the bare id; the membership grant
    (`room_event_shown`) then requires the event's stored owner to match the
    room the form resolves to, so a same-id room on another platform cannot
    hand over its events."""
    if rooms_of is None:
        return {}
    return {
        form: (platform, cid)
        for platform, cid in rooms_of(person_id) for form in (cid, f"{platform}:{cid}")
    }


def _gathering_dict(event: Gathering) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "title": event.title,
        "when": event.when.isoformat() if event.when else None,
        "venue": {
            "text": event.venue_text,
            "name": event.venue_name,
            "address": event.venue_address,
            "rating": event.venue_rating,
            "price_level": event.venue_price_level,
            "maps_url": event.venue_maps_url,
        },
        "status": event.status.value,
        "created_by": event.created_by,
        "created_at": event.created_at.isoformat(),
    }


async def build_gatherings_payload(
    *, events: GatheringService, person_id: str,
    rooms_of: RoomsOf | None = None, shown: RoomShown | None = None,
) -> list[dict[str, Any]]:
    """Every event `person_id` created or RSVP'd to, across every group, and
    every event held in a room that names them (`rooms_of`, the membership the
    portal's documents and artifacts are scoped by) that `shown` lets them see:
    not a surprise kept from them, and every field passing lineage for that room.
    Without `shown` no room-only event is listed (fail closed). Leaving a room
    hides its events at the next read."""
    mine = list(await events.list_events_for_person(person_id=person_id))
    forms = _room_forms(rooms_of, person_id)
    if forms and shown is not None:
        seen = {e.id for e in mine}
        for e in await events.list_events_for_person(
            person_id=person_id, conversation_ids=frozenset(forms),
        ):
            if e.id not in seen and shown(e, person_id, *forms[e.conversation_id]):
                mine.append(e)
    mine.sort(key=lambda e: e.created_at)
    return [_gathering_dict(e) for e in mine]


async def build_gathering_payload(
    *, events: GatheringService, event_id: UUID, person_id: str,
    rooms_of: RoomsOf | None = None, shown: RoomShown | None = None,
) -> dict[str, Any] | None:
    event = await events.get_event_for_person(event_id=event_id, person_id=person_id)
    if event is None:
        forms = _room_forms(rooms_of, person_id)
        if not forms or shown is None:
            return None
        event = await events.get_event_for_person(
            event_id=event_id, person_id=person_id, conversation_ids=frozenset(forms),
        )
        if event is None or not shown(event, person_id, *forms[event.conversation_id]):
            return None
    rsvps = await events.rsvps_for(event_id=event_id)
    payload = _gathering_dict(event)
    payload["rsvps"] = [
        {"person_id": r.person_id, "response": r.response.value}
        for r in rsvps
    ]
    return payload
