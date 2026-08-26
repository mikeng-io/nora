"""Gathering/RSVP domain types and the `GatheringStore` port.

Mirrors `nora.domain.reminders`'s split (types + port in one module, no
separate `ports.py` entry — a domain module's own port lives with its
types, matching `ReminderStore`'s own precedent). An `Gathering` OWNS at most
one `Reminder` (`reminder_id`) when it has a `when` — Gathering never has a
recurrence concept of its own, so that reminder is always one-shot
(`Series` is never involved). Notifications are entirely the existing
`nora.domain.reminders` + `nora.app.fire` path; nothing here re-implements
scheduling.

Venue fields are resolved ONCE, at write time, through the same
`route_source`/`PlaceResolver` `nora.agent.tools`'s `place_info` tool
already uses (see `nora.app.gatherings.GatheringService`) — `venue_text` is what
was actually SAID; `venue_place_id`/`venue_name`/`venue_address`/
`venue_rating`/`venue_price_level`/`venue_maps_url` are the cached,
stable result of that one resolution. Raw `lat`/`lng` are deliberately
absent (DEC-24 — never in a model-facing schema, and `route()` never
accepts a coordinate as an argument anyway) and `open_now` is deliberately
absent (it goes stale by the hour; "is it open now" stays a fresh
on-demand check, never a cached column).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from nora.domain.reminders import ensure_utc


class GatheringStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class RSVPResponse(StrEnum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"


class DuplicateGatheringError(Exception):
    """Raised by `GatheringStore.create` on a primary-key collision — mirrors
    `DuplicateReminderError`'s shape so callers handle both the same way."""

    def __init__(self, event_id: UUID) -> None:
        super().__init__(f"event {event_id} already exists")
        self.event_id = event_id


@dataclass(frozen=True, slots=True)
class Gathering:
    id: UUID
    conversation_id: str
    title: str
    created_by: str  # person_id
    when: datetime | None
    venue_text: str | None
    venue_place_id: str | None
    venue_name: str | None
    venue_address: str | None
    venue_rating: float | None
    venue_price_level: str
    venue_maps_url: str | None
    venue_resolved_at: datetime | None
    status: GatheringStatus
    reminder_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RSVP:
    event_id: UUID
    person_id: str
    response: RSVPResponse
    responded_at: datetime


@dataclass(frozen=True, slots=True)
class VenueCandidate:
    """One `suggest_venue` candidate. `source` names WHY it was suggested —
    "mention_frequency" or "explicit_callback" — so a reply can say why,
    not just what."""

    venue_text: str
    place_id: str | None
    name: str | None
    address: str | None
    rating: float | None
    price_level: str
    maps_url: str | None
    source: str


@dataclass(frozen=True, slots=True)
class VenueSuggestions:
    """`status` is the R-27 three-state shape: `ok` / `nothing_found` /
    `unavailable` — an ERROR is never a PASS. `route_hints` is the reserved
    seam from spec §5.1: always `None` in this version; a later revision
    fills it via `saved_places()` + `route()` per attendee under the R-28
    guard those tools already enforce — the type does not change when it
    does."""

    status: str
    candidates: tuple[VenueCandidate, ...] = ()
    route_hints: None = None


class GatheringNotFoundError(Exception):
    """Raised by `GatheringService` (`nora.app.gatherings`) whenever an `event_id`
    does not resolve. Lives here, not on `GatheringService` itself, so
    `nora.agent` — which sits BELOW `nora.app` in the layered-architecture
    import-linter contract and may not import it — can still catch it: a
    caller several layers up mints the concrete `GatheringService`, but the
    exception TYPE has to be reachable from every layer that calls a method
    which can raise it."""

    def __init__(self, event_id: UUID) -> None:
        super().__init__(f"event {event_id} not found")
        self.event_id = event_id


class GatheringCancelledError(Exception):
    """Raised by `GatheringService` (`nora.app.gatherings`) when `update_event`/`rsvp`
    is asked to act on an event whose `status` is already `CANCELLED` — a new
    `when` would otherwise re-arm the event's reminder for a gathering that
    was called off (Fix I2). Lives here for the same layering reason as
    `GatheringNotFoundError`: the exception TYPE must be reachable from
    `nora.agent`, which sits below `nora.app` and may not import it."""

    def __init__(self, event_id: UUID) -> None:
        super().__init__(f"event {event_id} is cancelled")
        self.event_id = event_id


class AmbiguousOpenGatheringsError(Exception):
    """More than one proposed event exists for a conversation — spec
    §3.1's fail-closed-on-ambiguity case. The caller (the `create_event`
    tool) is expected to catch this and ask which event is meant, never
    guess. Same layering reason as `GatheringNotFoundError` above for why this
    lives in `nora.domain` rather than alongside `GatheringService`."""

    def __init__(self, conversation_id: str, open_events: Sequence[Gathering]) -> None:
        super().__init__(
            f"{len(open_events)} open events for {conversation_id}"
        )
        self.conversation_id = conversation_id
        self.open_events = tuple(open_events)


@runtime_checkable
class GatheringStore(Protocol):
    async def create(self, event: Gathering) -> None: ...

    async def get(self, event_id: UUID) -> Gathering | None: ...

    async def list_for_conversation(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
    ) -> Sequence[Gathering]: ...

    async def list_for_person(self, *, person_id: str) -> Sequence[Gathering]: ...

    async def update(self, event: Gathering) -> None: ...

    async def add_rsvp(self, rsvp: RSVP) -> None: ...

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]: ...

    async def aclose(self) -> None: ...


def normalise_gathering(event: Gathering) -> Gathering:
    """`ensure_utc` over every one of an event's datetimes — the port's
    rule, in one place, exactly matching `normalise_reminder`'s shape."""
    from dataclasses import replace

    return replace(
        event,
        when=(
            None if event.when is None
            else ensure_utc(event.when, field="when")
        ),
        venue_resolved_at=(
            None if event.venue_resolved_at is None
            else ensure_utc(event.venue_resolved_at, field="venue_resolved_at")
        ),
        created_at=ensure_utc(event.created_at, field="created_at"),
    )
