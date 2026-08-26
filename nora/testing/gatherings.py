"""In-memory `GatheringStore` for tests. Must reproduce the REAL store's
failure/edge shapes — a duplicate `create` raises `DuplicateGatheringError`
exactly like `SqlGatheringStore` does, `update`/`add_rsvp` on the same
(event_id, person_id) upserts, `list_for_conversation` sorts by
`created_at` — never a fake that answers every call the same way
(CLAUDE.md's rule)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from nora.domain.gatherings import (
    RSVP,
    DuplicateGatheringError,
    Gathering,
    GatheringStatus,
    normalise_gathering,
)


class FakeGatheringStore:
    def __init__(self) -> None:
        self._events: dict[UUID, Gathering] = {}
        self._rsvps: dict[tuple[UUID, str], RSVP] = {}

    async def create(self, event: Gathering) -> None:
        row = normalise_gathering(event)
        if row.id in self._events:
            raise DuplicateGatheringError(row.id)
        self._events[row.id] = row

    async def get(self, event_id: UUID) -> Gathering | None:
        return self._events.get(event_id)

    async def list_for_conversation(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
    ) -> Sequence[Gathering]:
        out = [
            e for e in self._events.values()
            if e.conversation_id == conversation_id
            and (status is None or e.status == status)
        ]
        return sorted(out, key=lambda e: e.created_at)

    async def list_for_person(self, *, person_id: str) -> Sequence[Gathering]:
        rsvpd_ids = {
            eid for (eid, pid) in self._rsvps if pid == person_id
        }
        out = [
            e for e in self._events.values()
            if e.created_by == person_id or e.id in rsvpd_ids
        ]
        return sorted(out, key=lambda e: e.created_at)

    async def update(self, event: Gathering) -> None:
        row = normalise_gathering(event)
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
