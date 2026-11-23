"""Gathering reads and room-scoped RSVP handling."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from datetime import UTC, datetime
from uuid import UUID

from nora.agent.lineage import LineagePolicy, visible_sections
from nora.agent.security import looks_like_coordinate, scan_identifiers
from nora.domain.gatherings import (
    RSVP,
    TERMINAL_STATUSES,
    Gathering,
    GatheringStatus,
    GatheringStore,
    RoomClaims,
    RSVPResponse,
    event_in_scope,
)
from nora.domain.gatherings import (
    AmbiguousOpenGatheringsError as AmbiguousOpenGatheringsError,
)
from nora.domain.gatherings import (
    GatheringCancelledError as GatheringCancelledError,
)
from nora.domain.gatherings import (
    GatheringNotFoundError as GatheringNotFoundError,
)
from nora.domain.gatherings import (
    StaleEventRevisionError as StaleEventRevisionError,
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
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._now = now

    async def rsvp(
        self, *, event_id: UUID, conversation_id: str, person_id: str,
        response: RSVPResponse,
        platform: str = "",
        unit_id: str = "",
    ) -> RSVP:
        current = await self._require_in_conversation(
            event_id,
            conversation_id,
            unit_id=unit_id,
            platform=platform,
        )
        if current.status in TERMINAL_STATUSES:
            # An RSVP would make a terminal event appear to be still on.
            raise GatheringCancelledError(event_id, current.status)
        row = RSVP(
            event_id=event_id, person_id=person_id, response=response,
            responded_at=self._now(),
        )
        await self._store.add_rsvp(row)
        return row

    async def list_events(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
        unit_id: str = "",
        platform: str = "",
    ) -> Sequence[Gathering]:
        """This room's events, scoped by the PERSISTED owner: the row's
        `(unit_id, platform, conversation_id)` must be this caller's. A row
        with no stored platform is quarantined — current policy is not proof of
        who owned it when it was written. The in-memory filter repeats the
        store's check, so a store that widened a read cannot leak past it."""
        rows = await self._store.list_for_conversation(
            conversation_id=conversation_id, status=status, unit_id=unit_id,
            platform=platform or None,
        )
        return [
            e
            for e in rows
            if event_in_scope(
                e,
                unit_id=unit_id,
                conversation_id=conversation_id,
                platform=platform or None,
            )
        ]

    async def search_events(
        self, *, unit_id: str, conversation_id: str, place: str | None = None,
        person: str | None = None, start: datetime | None = None,
        end: datetime | None = None,
        platform: str = "",
    ) -> Sequence[Gathering]:
        """`event_search`'s store side: unit-wide when
        `unit_id` names one (`list_events` is per room, filtered by status
        only); otherwise this room's own events, so a room with no unit
        resolved can still search its own events. A bare `event_search()` with
        none of `place`/`person`/`start`/`end` reads exactly as `list_events()`.

        The unit is the read boundary (DEC-231), so every persisted platform in
        it is in scope. A row with no stored platform is quarantined; the old
        "room ids policy assigns to this unit" widening inferred historical
        ownership and is gone."""
        if not unit_id:
            return await self.list_events(
                conversation_id=conversation_id, platform=platform,
            )
        found = await self._store.search(
            unit_id=unit_id, place=place, person=person, start=start, end=end,
        )
        return [
            e
            for e in found
            if event_in_scope(
                e,
                unit_id=unit_id,
                conversation_id=e.conversation_id,
            )
        ]

    async def get_event(self, *, event_id: UUID) -> Gathering | None:
        """A reader: an id that does not exist reads as absent."""
        return await self._store.get(event_id)

    async def list_events_for_person(
        self, *, person_id: str, conversation_ids: Collection[str] = (),
    ) -> Sequence[Gathering]:
        """The web app's ONLY read path (not conversation-scoped, unlike
        `list_events`, which the chat tools use): every event `person_id`
        created or RSVP'd to, across every conversation, plus every event held
        in one of `conversation_ids`, the rooms they are a member of. Without
        the rooms, an event Cognition records (created by `cognition:<unit>`,
        no RSVPs) is shown to nobody."""
        return await self._store.list_for_person(
            person_id=person_id, conversation_ids=conversation_ids,
        )

    async def get_event_for_person(
        self, *, event_id: UUID, person_id: str, conversation_ids: Collection[str] = (),
    ) -> Gathering | None:
        """The web app's per-event read: `None` (→ 404, never a distinct
        "forbidden") unless `person_id` created this event or has an RSVP
        on it — the same "an id from outside your scope reads as unknown"
        discipline `_require_in_conversation` already applies for the chat
        tools, applied here to event-level participation instead of
        conversation membership."""
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

    # ── internals ────────────────────────────────────────────────────────

    async def _require(self, event_id: UUID) -> Gathering:
        event = await self._store.get(event_id)
        if event is None:
            raise GatheringNotFoundError(event_id)
        return event

    async def _require_in_conversation(
        self, event_id: UUID, conversation_id: str,
        *,
        unit_id: str = "",
        platform: str = "",
    ) -> Gathering:
        """`_require`, plus the scope check `ReminderStore.cancel`
        (`nora.domain.reminders`) already documents the reasoning for: "a
        uuid carries no scope of its own — the STORE enforces the scope."
        An RSVP can carry a bare event id. An id from another room refuses exactly
        like an unknown one (`GatheringNotFoundError`), never a distinct "wrong
        room" message that would confirm a stranger's event id is real.

        The check is not conversation-only: `event_in_scope` requires the
        row's persisted `(unit_id, platform)` to be this caller's. A row with
        no stored platform (an unresolved historical row) is quarantined, not
        attributed from current `policy.toml`. An OMITTED platform is not a
        wildcard either: `platform=""` matches no persisted row, so a caller
        that has not resolved the platform fails closed."""
        event = await self._require(event_id)
        if not event_in_scope(
            event,
            unit_id=unit_id,
            conversation_id=conversation_id,
            platform=platform,
        ):
            raise GatheringNotFoundError(event_id)
        return event

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
