"""Seed historical event heads and RSVPs for real-store read tests."""

from dataclasses import replace
from uuid import uuid4

import sqlalchemy as sa

from nora.domain.gatherings import RSVP, Gathering, normalise_gathering
from nora.infra.sql import memory_index
from nora.infra.sql.gatherings import (
    SqlGatheringStore,
    _event_values,
    _revision_of,
    _revision_values,
)
from nora.infra.sql.schema import EVENT_REVISION, EVENT_RSVPS, EVENTS


class SeededGatheringStore(SqlGatheringStore):
    async def create(self, event: Gathering) -> Gathering:
        event = normalise_gathering(event)
        revision = _revision_of(event, parent_revision_id=None)
        event = replace(event, head_revision_id=revision.id)
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(EVENTS).values(**_event_values(event)))
            await conn.execute(sa.insert(EVENT_REVISION).values(**_revision_values(revision)))
            await memory_index.project(conn, "event", str(event.id))
        return event

    async def add_rsvp(self, rsvp: RSVP) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(EVENT_RSVPS).values(
                id=uuid4(), event_id=rsvp.event_id, person_id=rsvp.person_id,
                response=rsvp.response.value, responded_at=rsvp.responded_at,
                recorded_at=rsvp.responded_at,
            ))
            await memory_index.project(conn, "event", str(rsvp.event_id))
