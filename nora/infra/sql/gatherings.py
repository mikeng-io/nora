"""`GatheringStore` over SQLAlchemy Core. Shaped like `nora.infra.sql.reminders`:
the engine comes through `__init__`, every write is one statement (or one
transaction of a few) inside `async with self._engine.begin()`, every read
inside `async with self._engine.connect()`.

`events` is a head, a compare-and-swap cache over the append-only
`event_revision` chain (migration `0074_event_revision`), mirroring `nora.infra.sql.notes`'
`note`/`note_revision` split — `create` mints both the head and its first
revision in one transaction, `update` appends a new revision and moves the
head, optionally refusing a stale `base_revision_id` (`StaleEventRevisionError`:
a stale one is refused `stale_base`, with the current row). RSVPs
are append-only too (`event_rsvps`): `add_rsvp` INSERTs, never upserts;
`rsvps_for` projects the newest row per person, so callers see the same
"one current RSVP per person" shape.

`FakeGatheringStore` (`nora/testing/gatherings.py`) is the second
implementation; `tests/contract/test_gathering_store_contract.py` is the one
suite both pass.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace as _replace
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.gatherings import (
    RSVP,
    DuplicateGatheringError,
    EventEvidence,
    EventRevision,
    Gathering,
    GatheringStatus,
    RSVPResponse,
    StaleEventRevisionError,
    field_meta_from_json,
    field_meta_to_json,
    normalise_gathering,
)
from nora.domain.lineage import Lineage
from nora.infra.sql import memory_index
from nora.infra.sql.event_evidence import apply_evidence
from nora.infra.sql.schema import EVENT_REVISION, EVENT_RSVPS, EVENTS

# PostgreSQL SQLSTATE 23505, unique_violation — matched exactly, the same
# reasoning as `nora.infra.sql.reminders._PG_UNIQUE_VIOLATION`: this table
# also carries a CHECK constraint, and a bad status value must not be
# reported as "duplicate id".
_PG_UNIQUE_VIOLATION = "23505"


def _row_to_event(r: Row[Any]) -> Gathering:
    return normalise_gathering(
        Gathering(
            id=r.id,
            conversation_id=r.conversation_id,
            title=r.title,
            created_by=r.created_by,
            when=r.when,
            venue_text=r.venue_text,
            venue_place_id=r.venue_place_id,
            venue_name=r.venue_name,
            venue_address=r.venue_address,
            venue_rating=r.venue_rating,
            venue_price_level=r.venue_price_level,
            venue_maps_url=r.venue_maps_url,
            venue_resolved_at=r.venue_resolved_at,
            status=GatheringStatus(r.status),
            reminder_id=r.reminder_id,
            created_at=r.created_at,
            unit_id=r.unit_id,
            end=r.end,
            people=tuple(r.people or ()),
            reasoning=r.reasoning,
            confidence=r.confidence,
            lineage=Lineage.from_json(r.lineage),
            as_of=r.as_of,
            exclude_persons=tuple(r.exclude_persons or ()),
            head_revision_id=r.head_revision_id,
            field_meta=field_meta_from_json(r.field_meta),
        )
    )


def _row_to_revision(r: Row[Any]) -> EventRevision:
    return EventRevision(
        id=r.id,
        event_id=r.event_id,
        parent_revision_id=r.parent_revision_id,
        status=GatheringStatus(r.status),
        title=r.title,
        when=r.when,
        end=r.end,
        venue_text=r.venue_text,
        venue_place_id=r.venue_place_id,
        venue_name=r.venue_name,
        venue_address=r.venue_address,
        venue_rating=r.venue_rating,
        venue_price_level=r.venue_price_level,
        venue_maps_url=r.venue_maps_url,
        people=tuple(r.people or ()),
        reasoning=r.reasoning,
        confidence=r.confidence,
        lineage=Lineage.from_json(r.lineage),
        as_of=r.as_of,
        body=r.body,
        written_by=r.written_by,
        written_at=r.written_at,
        field_meta=field_meta_from_json(r.field_meta),
    )


def _row_to_rsvp(r: Row[Any]) -> RSVP:
    return RSVP(
        event_id=r.event_id,
        person_id=r.person_id,
        response=RSVPResponse(r.response),
        responded_at=r.responded_at,
    )


def _event_values(event: Gathering) -> dict[str, Any]:
    return {
        "id": event.id,
        "conversation_id": event.conversation_id,
        "title": event.title,
        "created_by": event.created_by,
        "when": event.when,
        "venue_text": event.venue_text,
        "venue_place_id": event.venue_place_id,
        "venue_name": event.venue_name,
        "venue_address": event.venue_address,
        "venue_rating": event.venue_rating,
        "venue_price_level": event.venue_price_level,
        "venue_maps_url": event.venue_maps_url,
        "venue_resolved_at": event.venue_resolved_at,
        "status": event.status.value,
        "reminder_id": event.reminder_id,
        "created_at": event.created_at,
        "unit_id": event.unit_id,
        "end": event.end,
        "people": list(event.people),
        "reasoning": event.reasoning,
        "confidence": event.confidence,
        "lineage": event.lineage.to_json(),
        "as_of": event.as_of,
        "exclude_persons": list(event.exclude_persons),
        "head_revision_id": event.head_revision_id,
        "field_meta": field_meta_to_json(event.field_meta),
    }


def _revision_values(revision: EventRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "event_id": revision.event_id,
        "parent_revision_id": revision.parent_revision_id,
        "status": revision.status.value,
        "title": revision.title,
        "when": revision.when,
        "end": revision.end,
        "venue_text": revision.venue_text,
        "venue_place_id": revision.venue_place_id,
        "venue_name": revision.venue_name,
        "venue_address": revision.venue_address,
        "venue_rating": revision.venue_rating,
        "venue_price_level": revision.venue_price_level,
        "venue_maps_url": revision.venue_maps_url,
        "people": list(revision.people),
        "reasoning": revision.reasoning,
        "confidence": revision.confidence,
        "lineage": revision.lineage.to_json(),
        "as_of": revision.as_of,
        "body": revision.body,
        "written_by": revision.written_by,
        "written_at": revision.written_at,
        "field_meta": field_meta_to_json(revision.field_meta),
    }


def _revision_of(event: Gathering, *, parent_revision_id: UUID | None) -> EventRevision:
    """The revision a write of `event` produces — the head's own columns,
    snapshotted, chained onto `parent_revision_id`."""
    return EventRevision(
        id=uuid4(), event_id=event.id, parent_revision_id=parent_revision_id,
        status=event.status, title=event.title, when=event.when, end=event.end,
        venue_text=event.venue_text, venue_place_id=event.venue_place_id,
        venue_name=event.venue_name, venue_address=event.venue_address,
        venue_rating=event.venue_rating, venue_price_level=event.venue_price_level,
        venue_maps_url=event.venue_maps_url, people=event.people,
        reasoning=event.reasoning, confidence=event.confidence,
        lineage=event.lineage, as_of=event.as_of, body=None,
        written_by="tag", written_at=event.created_at, field_meta=event.field_meta,
    )


class SqlGatheringStore:
    """Satisfies `nora.domain.gatherings.GatheringStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self, event: Gathering, *, evidence: EventEvidence | None = None,
    ) -> Gathering:
        row = normalise_gathering(event)
        try:
            async with self._engine.begin() as conn:
                if evidence is not None:
                    lineage, as_of = await apply_evidence(
                        conn, event=row, held=row.lineage, evidence=evidence,
                    )
                    row = _replace(row, lineage=lineage, as_of=as_of)
                revision = _revision_of(row, parent_revision_id=None)
                row_with_head = _replace(row, head_revision_id=revision.id)
                await conn.execute(
                    sa.insert(EVENTS).values(**_event_values(row_with_head)),
                )
                await conn.execute(
                    sa.insert(EVENT_REVISION).values(**_revision_values(revision)),
                )
                await memory_index.project(conn, "event", str(row.id))
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                raise
            raise DuplicateGatheringError(row.id) from exc
        return row_with_head

    async def get(self, event_id: UUID) -> Gathering | None:
        stmt = sa.select(EVENTS).where(EVENTS.c.id == event_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_event(row)

    async def list_for_conversation(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
    ) -> Sequence[Gathering]:
        stmt = (
            sa.select(EVENTS)
            .where(EVENTS.c.conversation_id == conversation_id)
            .order_by(EVENTS.c.created_at)
        )
        if status is not None:
            stmt = stmt.where(EVENTS.c.status == status.value)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]

    async def list_for_person(
        self, *, person_id: str, conversation_ids: Collection[str] = (),
    ) -> Sequence[Gathering]:
        rsvpd = sa.select(EVENT_RSVPS.c.event_id).where(
            EVENT_RSVPS.c.person_id == person_id,
        )
        stmt = (
            sa.select(EVENTS)
            .where(sa.or_(
                EVENTS.c.created_by == person_id,
                EVENTS.c.id.in_(rsvpd),
                EVENTS.c.conversation_id.in_(sorted(conversation_ids)),
            ))
            .order_by(EVENTS.c.created_at)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]

    async def update(
        self, event: Gathering, *, base_revision_id: UUID | None = None,
        evidence: EventEvidence | None = None,
    ) -> Gathering:
        row = normalise_gathering(event)
        async with self._engine.begin() as conn:
            current = (await conn.execute(
                sa.select(EVENTS.c.head_revision_id, EVENTS.c.lineage)
                .where(EVENTS.c.id == row.id)
                .with_for_update(),
            )).one_or_none()
            if current is None:
                return row
            if base_revision_id is not None and current.head_revision_id != base_revision_id:
                stale = _row_to_event((await conn.execute(
                    sa.select(EVENTS).where(EVENTS.c.id == row.id),
                )).one())
                raise StaleEventRevisionError(row.id, stale)
            if evidence is not None:
                # The lineage the row holds NOW (read under the lock), not the
                # caller's copy: a concurrent write's sections are not undone.
                lineage, as_of = await apply_evidence(
                    conn, event=row, held=Lineage.from_json(current.lineage),
                    evidence=evidence,
                )
                row = _replace(row, lineage=lineage, as_of=as_of)
            revision = _revision_of(row, parent_revision_id=current.head_revision_id)
            await conn.execute(
                sa.insert(EVENT_REVISION).values(**_revision_values(revision)),
            )
            values = {
                k: v for k, v in _event_values(row).items() if k != "id"
            }
            values["head_revision_id"] = revision.id
            await conn.execute(
                sa.update(EVENTS).where(EVENTS.c.id == row.id).values(**values),
            )
            await memory_index.project(conn, "event", str(row.id))
        return _replace(row, head_revision_id=revision.id)

    async def add_rsvp(self, rsvp: RSVP) -> None:
        # Append-only (`0074_event_revision`): a repeat RSVP is a new row,
        # never an UPDATE of the old one — `rsvps_for` is what projects "the current one" back out.
        stmt = sa.insert(EVENT_RSVPS).values(
            id=uuid4(),
            event_id=rsvp.event_id,
            person_id=rsvp.person_id,
            response=rsvp.response.value,
            responded_at=rsvp.responded_at,
            recorded_at=rsvp.responded_at,
        )
        async with self._engine.begin() as conn:
            # Locked as `update` locks it: the projection below re-reads the
            # whole event row, and without the lock a concurrent `update`
            # committing between that read and this commit would have its
            # node overwritten with the older values (a newly added
            # `exclude_persons` undone in the index).
            await conn.execute(
                sa.select(EVENTS.c.id).where(EVENTS.c.id == rsvp.event_id).with_for_update(),
            )
            await conn.execute(stmt)
            # The person who answered is someone the event is about.
            await memory_index.project(conn, "event", str(rsvp.event_id))

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]:
        # DISTINCT ON the newest row per person (Postgres extension; both
        # implementations of this port already assume Postgres elsewhere —
        # `nora.infra.sql.reminders` uses `ON CONFLICT`, the dialect-specific
        # precedent this follows).
        latest = (
            sa.select(EVENT_RSVPS)
            .distinct(EVENT_RSVPS.c.person_id)
            .where(EVENT_RSVPS.c.event_id == event_id)
            .order_by(
                EVENT_RSVPS.c.person_id,
                EVENT_RSVPS.c.recorded_at.desc(),
                EVENT_RSVPS.c.id.desc(),
            )
            .subquery()
        )
        stmt = sa.select(latest).order_by(latest.c.responded_at)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_rsvp(r) for r in rows]

    async def revisions_for(self, event_id: UUID) -> Sequence[EventRevision]:
        stmt = (
            sa.select(EVENT_REVISION)
            .where(EVENT_REVISION.c.event_id == event_id)
            .order_by(EVENT_REVISION.c.recorded_at, EVENT_REVISION.c.id)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_revision(r) for r in rows]

    async def search(
        self, *, unit_id: str, place: str | None = None, person: str | None = None,
        start: Any = None, end: Any = None, room_ids: Sequence[str] = (),
    ) -> Sequence[Gathering]:
        """Unit-wide: `event_search`'s store side (`place?`, `person?`,
        `interval?`). `place` matches
        `venue_name`/`venue_text` by containment; `person` matches the
        creator or an RSVP; `start`/`end` bound `events.when`.

        B3: `room_ids`, when the caller passes any, widens the match to also
        catch a pre-migration row (`unit_id=""`) whose `conversation_id` is
        one of the unit's own rooms — units live in `policy.toml`, not the
        DB, so the migration backfilled every existing event to
        `unit_id=""` and this is the only way one is found unit-wide again.
        `room_ids=()` keeps the old, exact `unit_id == unit_id` match."""
        unit_clause: Any = EVENTS.c.unit_id == unit_id
        if room_ids:
            unit_clause = sa.or_(
                unit_clause,
                sa.and_(
                    EVENTS.c.unit_id == "",
                    EVENTS.c.conversation_id.in_(room_ids),
                ),
            )
        stmt = sa.select(EVENTS).where(unit_clause)
        if place:
            needle = f"%{place}%"
            stmt = stmt.where(sa.or_(
                EVENTS.c.venue_name.ilike(needle), EVENTS.c.venue_text.ilike(needle),
            ))
        if person:
            rsvpd = sa.select(EVENT_RSVPS.c.event_id).where(
                EVENT_RSVPS.c.person_id == person,
            )
            stmt = stmt.where(sa.or_(
                EVENTS.c.created_by == person, EVENTS.c.id.in_(rsvpd),
            ))
        if start is not None:
            stmt = stmt.where(sa.or_(EVENTS.c.when.is_(None), EVENTS.c.when >= start))
        if end is not None:
            stmt = stmt.where(sa.or_(EVENTS.c.when.is_(None), EVENTS.c.when <= end))
        stmt = stmt.order_by(EVENTS.c.created_at)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]

    async def aclose(self) -> None:
        await self._engine.dispose()
