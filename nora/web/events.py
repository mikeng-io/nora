"""JSON payload builders for the read-only Event web surface. Plain async
functions over typed collaborators, matching `nora.web.memory`'s shape
(`build_memory_payload`) rather than `nora.web.games`'s remote-client-wrapper
shape — Event's data is local Postgres via `EventService`, not a remote
REST API."""

from __future__ import annotations

from uuid import UUID

from nora.app.events import EventService
from nora.domain.events import Event


def _event_dict(event: Event) -> dict:
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


async def build_events_payload(
    *, events: EventService, conversation_id: str,
) -> list[dict]:
    out = await events.list_events(conversation_id=conversation_id)
    return [_event_dict(e) for e in out]


async def build_event_payload(
    *, events: EventService, event_id: UUID,
) -> dict | None:
    event = await events.get_event(event_id=event_id)
    if event is None:
        return None
    rsvps = await events.rsvps_for(event_id=event_id)
    payload = _event_dict(event)
    payload["rsvps"] = [
        {"person_id": r.person_id, "response": r.response.value}
        for r in rsvps
    ]
    return payload
