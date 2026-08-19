"""`EventStore` over SQLAlchemy Core. Shaped like `nora.infra.sql.reminders`:
the engine comes through `__init__`, every write is one statement inside
`async with self._engine.begin()`, every read inside
`async with self._engine.connect()`.

`FakeEventStore` (`nora/testing/events.py`) is the second implementation;
`tests/contract/test_event_store_contract.py` is the one suite both pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.events import (
    RSVP,
    DuplicateEventError,
    Event,
    EventStatus,
    RSVPResponse,
    normalise_event,
)
from nora.infra.sql.schema import EVENT_RSVPS, EVENTS

# PostgreSQL SQLSTATE 23505, unique_violation — matched exactly, the same
# reasoning as `nora.infra.sql.reminders._PG_UNIQUE_VIOLATION`: this table
# also carries a CHECK constraint, and a bad status value must not be
# reported as "duplicate id".
_PG_UNIQUE_VIOLATION = "23505"


def _row_to_event(r: Row[Any]) -> Event:
    return normalise_event(
        Event(
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
            status=EventStatus(r.status),
            reminder_id=r.reminder_id,
            created_at=r.created_at,
        )
    )


def _row_to_rsvp(r: Row[Any]) -> RSVP:
    return RSVP(
        event_id=r.event_id,
        person_id=r.person_id,
        response=RSVPResponse(r.response),
        responded_at=r.responded_at,
    )


def _insert_values(event: Event) -> dict[str, Any]:
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
    }


class SqlEventStore:
    """Satisfies `nora.domain.events.EventStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, event: Event) -> None:
        row = normalise_event(event)
        stmt = sa.insert(EVENTS).values(**_insert_values(row))
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                raise
            raise DuplicateEventError(row.id) from exc

    async def get(self, event_id: UUID) -> Event | None:
        stmt = sa.select(EVENTS).where(EVENTS.c.id == event_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_event(row)

    async def list_for_conversation(
        self, *, conversation_id: str, status: EventStatus | None = None,
    ) -> Sequence[Event]:
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

    async def list_for_person(self, *, person_id: str) -> Sequence[Event]:
        rsvpd = sa.select(EVENT_RSVPS.c.event_id).where(
            EVENT_RSVPS.c.person_id == person_id,
        )
        stmt = (
            sa.select(EVENTS)
            .where(sa.or_(
                EVENTS.c.created_by == person_id,
                EVENTS.c.id.in_(rsvpd),
            ))
            .order_by(EVENTS.c.created_at)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]

    async def update(self, event: Event) -> None:
        row = normalise_event(event)
        stmt = (
            sa.update(EVENTS)
            .where(EVENTS.c.id == row.id)
            .values(**{k: v for k, v in _insert_values(row).items() if k != "id"})
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def add_rsvp(self, rsvp: RSVP) -> None:
        # `on_conflict_do_update` is a postgres-dialect-insert method, not
        # `sa.insert`'s — a person can RSVP more than once (changing their
        # mind), and (event_id, person_id) is the primary key, so a repeat
        # RSVP must UPDATE, not raise `DuplicateEventError`-shaped noise.
        stmt = (
            pg_insert(EVENT_RSVPS)
            .values(
                event_id=rsvp.event_id,
                person_id=rsvp.person_id,
                response=rsvp.response.value,
                responded_at=rsvp.responded_at,
            )
            .on_conflict_do_update(
                index_elements=[EVENT_RSVPS.c.event_id, EVENT_RSVPS.c.person_id],
                set_={
                    "response": rsvp.response.value,
                    "responded_at": rsvp.responded_at,
                },
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]:
        stmt = (
            sa.select(EVENT_RSVPS)
            .where(EVENT_RSVPS.c.event_id == event_id)
            .order_by(EVENT_RSVPS.c.responded_at)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_rsvp(r) for r in rows]

    async def aclose(self) -> None:
        await self._engine.dispose()
