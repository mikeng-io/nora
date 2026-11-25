"""Gathering reads for the portal."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID

from nora.agent.lineage import LineagePolicy, visible_sections
from nora.agent.security import looks_like_coordinate, scan_identifiers
from nora.domain.gatherings import (
    RSVP,
    Gathering,
    GatheringStore,
    RoomClaims,
    event_in_scope,
)
from nora.domain.ports import PlaceArea
from nora.domain.rollup import room_key


def screened_area(area: PlaceArea | None, *, content_checks: bool = True) -> PlaceArea | None:
    """Remove area names that carry identifiers or coordinates."""
    if area is None:
        return None

    def clean(name: str | None) -> str | None:
        if (
            not name
            or scan_identifiers(name, content_checks=content_checks) is not None
            or (content_checks and looks_like_coordinate(name))
        ):
            return None
        return name

    return PlaceArea(district=clean(area.district), city=clean(area.city)) or None


class GatheringService:
    def __init__(
        self,
        *,
        store: GatheringStore,
    ) -> None:
        self._store = store

    async def list_events_for_person(
        self, *, person_id: str, conversation_ids: Collection[str] = (),
    ) -> Sequence[Gathering]:
        """List events the reader created, answered, or can see by room membership."""
        return await self._store.list_for_person(
            person_id=person_id, conversation_ids=conversation_ids,
        )

    async def get_event_for_person(
        self, *, event_id: UUID, person_id: str, conversation_ids: Collection[str] = (),
    ) -> Gathering | None:
        """Read an event within the reader's participation or room membership."""
        event = await self._store.get(event_id)
        if event is None:
            return None
        if event.created_by == person_id or event.conversation_id in conversation_ids:
            return event
        rsvps = await self._store.rsvps_for(event_id)
        if any(r.person_id == person_id for r in rsvps):
            return event
        return None

    async def rsvps_for(self, *, event_id: UUID) -> Sequence[RSVP]:
        """Read-only RSVP projection for a given event — the public
        passthrough `nora.web.gatherings` calls instead of reaching past the
        underscore into `self._store` directly."""
        return await self._store.rsvps_for(event_id)

#: The event fields a room's view shows, the same four the Cognition session's
#: event index is checked over (`nora.app.cognition_grounding._EVENT_SHOWN_FIELDS`).
ROOM_EVENT_FIELDS = ("what", "start", "place", "status")


def room_event_shown(
    lineage: LineagePolicy, claims: RoomClaims, event: Gathering, viewer: str,
    platform: str, conversation_id: str,
) -> bool:
    """Whether a member of the room `(platform, conversation_id)` who neither
    made nor answered `event` may see it on the portal.

    The membership grant itself checks the PERSISTED owner: `claims` resolves
    the room's trusted `(unit, platform)` from `policy.toml`, exactly one unit
    must match this platform, and the event's stored
    `(unit, platform, conversation_id)` must be that owner. A room that moved
    does not take its old events with it, and a row with no stored platform is
    quarantined. Then: never when the event is a surprise kept from the viewer,
    and, with lineage enforced, only when every shown field passes for the
    room, as the chat path checks before a room's turn reads the event.

    Explicit creator/RSVP access is a SEPARATE grant
    (`list_events_for_person`); a member who is also the creator or has RSVP'd
    still sees it, and this check is not applied to that path."""
    units = {u for (u, p) in claims.get(conversation_id, ()) if p == platform}
    if len(units) != 1:
        return False
    if not event_in_scope(
        event, unit_id=next(iter(units)), conversation_id=conversation_id,
        platform=platform,
    ):
        return False
    if viewer in event.exclude_persons:
        return False
    if lineage.mode != "on":
        return True
    target = lineage.render_room(platform=platform, conversation_id=conversation_id)
    return all(visible_sections(
        event.lineage, ROOM_EVENT_FIELDS, target,
        origin=room_key(platform, conversation_id), exclude_persons=event.exclude_persons,
    ).values())
