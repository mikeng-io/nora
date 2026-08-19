"""In-memory `EventStore` for tests. Must reproduce the REAL store's
failure/edge shapes — a duplicate `create` raises `DuplicateEventError`
exactly like `SqlEventStore` does, `update`/`add_rsvp` on the same
(event_id, person_id) upserts, `list_for_conversation` sorts by
`created_at` — never a fake that answers every call the same way
(CLAUDE.md's rule)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from nora.domain.events import (
    RSVP,
    DuplicateEventError,
    Event,
    EventStatus,
    normalise_event,
)


class FakeEventStore:
    def __init__(self) -> None:
        self._events: dict[UUID, Event] = {}
        self._rsvps: dict[tuple[UUID, str], RSVP] = {}

    async def create(self, event: Event) -> None:
        row = normalise_event(event)
        if row.id in self._events:
            raise DuplicateEventError(row.id)
        self._events[row.id] = row

    async def get(self, event_id: UUID) -> Event | None:
        return self._events.get(event_id)

    async def list_for_conversation(
        self, *, conversation_id: str, status: EventStatus | None = None,
    ) -> Sequence[Event]:
        out = [
            e for e in self._events.values()
            if e.conversation_id == conversation_id
            and (status is None or e.status == status)
        ]
        return sorted(out, key=lambda e: e.created_at)

    async def list_for_person(self, *, person_id: str) -> Sequence[Event]:
        rsvpd_ids = {
            eid for (eid, pid) in self._rsvps if pid == person_id
        }
        out = [
            e for e in self._events.values()
            if e.created_by == person_id or e.id in rsvpd_ids
        ]
        return sorted(out, key=lambda e: e.created_at)

    async def update(self, event: Event) -> None:
        row = normalise_event(event)
        if row.id not in self._events:
            return
        self._events[row.id] = row

    async def add_rsvp(self, rsvp: RSVP) -> None:
        self._rsvps[(rsvp.event_id, rsvp.person_id)] = rsvp

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]:
        out = [r for (eid, _), r in self._rsvps.items() if eid == event_id]
        return sorted(out, key=lambda r: r.responded_at)

    async def aclose(self) -> None:
        pass
