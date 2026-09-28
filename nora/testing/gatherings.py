"""In-memory `GatheringStore` for tests. Must reproduce the REAL store's
failure/edge shapes — a duplicate `create` raises `DuplicateGatheringError`
exactly like `SqlGatheringStore` does, `update` appends a revision and moves
the head (refusing a stale `base_revision_id` with `StaleEventRevisionError`),
`add_rsvp` is append-only and `rsvps_for` projects the newest row per person,
`list_for_conversation` sorts by `created_at` — never a fake that answers
every call the same way (CLAUDE.md's rule)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID, uuid4

from nora.domain.gatherings import (
    RSVP,
    DuplicateGatheringError,
    EventRevision,
    Gathering,
    GatheringStatus,
    StaleEventRevisionError,
    normalise_gathering,
)


def _revision_of(event: Gathering, *, parent_revision_id: UUID | None) -> EventRevision:
    return EventRevision(
        id=uuid4(), event_id=event.id, parent_revision_id=parent_revision_id,
        status=event.status, title=event.title, when=event.when, end=event.end,
        venue_text=event.venue_text, venue_place_id=event.venue_place_id,
        venue_name=event.venue_name, venue_address=event.venue_address,
        venue_rating=event.venue_rating, venue_price_level=event.venue_price_level,
        venue_maps_url=event.venue_maps_url, people=event.people,
        reasoning=event.reasoning, confidence=event.confidence,
        lineage=event.lineage, as_of=event.as_of, body=None,
        written_by="tag", written_at=event.created_at,
    )


class FakeGatheringStore:
    def __init__(self) -> None:
        self._events: dict[UUID, Gathering] = {}
        self._rsvps: list[RSVP] = []
        self._revisions: dict[UUID, list[EventRevision]] = {}

    async def create(self, event: Gathering) -> Gathering:
        row = normalise_gathering(event)
        if row.id in self._events:
            raise DuplicateGatheringError(row.id)
        revision = _revision_of(row, parent_revision_id=None)
        stored = replace(row, head_revision_id=revision.id)
        self._events[row.id] = stored
        self._revisions[row.id] = [revision]
        return stored

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
        rsvpd_ids = {r.event_id for r in self._rsvps if r.person_id == person_id}
        out = [
            e for e in self._events.values()
            if e.created_by == person_id or e.id in rsvpd_ids
        ]
        return sorted(out, key=lambda e: e.created_at)

    async def update(
        self, event: Gathering, *, base_revision_id: UUID | None = None,
    ) -> Gathering:
        row = normalise_gathering(event)
        current = self._events.get(row.id)
        if current is None:
            return row
        if base_revision_id is not None and current.head_revision_id != base_revision_id:
            raise StaleEventRevisionError(row.id, current)
        revision = _revision_of(row, parent_revision_id=current.head_revision_id)
        stored = replace(row, head_revision_id=revision.id)
        self._events[row.id] = stored
        self._revisions.setdefault(row.id, []).append(revision)
        return stored

    async def add_rsvp(self, rsvp: RSVP) -> None:
        self._rsvps.append(rsvp)

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]:
        # The newest row per person — append-only storage, current-value read.
        latest: dict[str, RSVP] = {}
        for r in sorted(
            (r for r in self._rsvps if r.event_id == event_id),
            key=lambda r: r.responded_at,
        ):
            latest[r.person_id] = r
        return sorted(latest.values(), key=lambda r: r.responded_at)

    async def revisions_for(self, event_id: UUID) -> Sequence[EventRevision]:
        return list(self._revisions.get(event_id, ()))

    async def search(
        self, *, unit_id: str, place: str | None = None, person: str | None = None,
        start=None, end=None,
    ) -> Sequence[Gathering]:
        rsvpd_ids = (
            {r.event_id for r in self._rsvps if r.person_id == person}
            if person else set()
        )
        out = []
        for e in self._events.values():
            if e.unit_id != unit_id:
                continue
            if place and place.lower() not in (
                (e.venue_name or "") + (e.venue_text or "")
            ).lower():
                continue
            if person and e.created_by != person and e.id not in rsvpd_ids:
                continue
            if start is not None and e.when is not None and e.when < start:
                continue
            if end is not None and e.when is not None and e.when > end:
                continue
            out.append(e)
        return sorted(out, key=lambda e: e.created_at)

    async def aclose(self) -> None:
        pass
