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


def _room_ids(rooms_of: RoomsOf | None, person_id: str) -> frozenset[str]:
    """The conversation ids an event of the viewer's rooms can be stored under:
    the bare id and its `platform:` form, because both are in `events`."""
    if rooms_of is None:
        return frozenset()
    return frozenset(
        form for platform, cid in rooms_of(person_id) for form in (cid, f"{platform}:{cid}")
    )


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
    *, events: GatheringService, person_id: str, rooms_of: RoomsOf | None = None,
) -> list[dict[str, Any]]:
    """Every event `person_id` created or RSVP'd to, across every group, and
    every event held in a room that names them (`rooms_of`, the same membership
    the portal's documents and artifacts are scoped by). Leaving a room hides
    its events at the next read. An event in a room they are not in, that they
    neither made nor answered, stays invisible."""
    out = await events.list_events_for_person(
        person_id=person_id, conversation_ids=_room_ids(rooms_of, person_id),
    )
    return [_gathering_dict(e) for e in out]


async def build_gathering_payload(
    *, events: GatheringService, event_id: UUID, person_id: str,
    rooms_of: RoomsOf | None = None,
) -> dict[str, Any] | None:
    event = await events.get_event_for_person(
        event_id=event_id, person_id=person_id,
        conversation_ids=_room_ids(rooms_of, person_id),
    )
    if event is None:
        return None
    rsvps = await events.rsvps_for(event_id=event_id)
    payload = _gathering_dict(event)
    payload["rsvps"] = [
        {"person_id": r.person_id, "response": r.response.value}
        for r in rsvps
    ]
    return payload
