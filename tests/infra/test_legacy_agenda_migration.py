"""Read back commitment-head coverage from generated legacy and graph rows."""

from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.domain.ticks import due_tick_of_at
from nora.infra.sql.cognitive_graph_schema import INTENTION
from nora.infra.sql.schema import (
    EVENTS,
    LEDGER_COMMITMENT,
    LEDGER_COMMITMENT_REVISION,
    NOTE,
    NOTE_REVISION,
    REMINDERS,
)
from nora.memory import ReadingRoom, wire
from nora.memory.adapters.legacy_agenda import migrated_id
from tests.infra.test_proactive_claims import DUE
from tests.infra.test_retrieval_scope import AT, BOUNDS, DM, OTHER, PERSON, ROOM, UNIT, references
from tests.infra.test_retrieval_scope import scenario as scenario


async def legacy_reminder(conn, state="pending", **fields):
    row_id = uuid4()
    await conn.execute(sa.insert(REMINDERS).values({
        "id": row_id, "requester_person_id": PERSON, "platform": "signal",
        "conversation_id": ROOM, "conversation_type": "group", "text": "Generated appointment",
        "due_at": DUE, "due_tick": due_tick_of_at(DUE), "created_at": AT, "state": state,
        **fields,
    }))
    return row_id


async def legacy_heads(conn):
    opened, closed = set(), set()
    for state in ("pending", "firing", "done", "cancelled", "failed"):
        row_id = await legacy_reminder(conn, state)
        target = opened if state in {"pending", "firing"} else closed
        target.add(migrated_id("reminders", row_id))
    for state in ("staged", "proposed", "confirmed", "happened", "cancelled", "lapsed", "evicted"):
        row_id = uuid4()
        await conn.execute(sa.insert(EVENTS).values(
            id=row_id, unit_id=UNIT, platform="signal", conversation_id=ROOM,
            title="Generated gathering", created_by=PERSON, created_at=AT, status=state,
            when=DUE if state == "confirmed" else None,
        ))
        target = opened if state in {"staged", "proposed", "confirmed"} else closed
        target.add(migrated_id("events", row_id))
    ms = int(AT.timestamp() * 1000)
    for retired_at in (None, ms):
        row_id = str(uuid4())
        head_id = str(uuid4())
        await conn.execute(sa.insert(NOTE).values(
            id=row_id, unit=UNIT, title="Generated note", description="Generated question",
            uncertainty="Unresolved", closing_condition="The venue is decided",
            anchor_date=20261128,
            established_in=f"signal:{ROOM}", created_at=ms, head_revision_id=head_id,
            retired_at=retired_at,
        ))
        await conn.execute(sa.insert(NOTE_REVISION).values(
            id=head_id, note_id=row_id, body="Generated revision", written_at=ms,
            established_by="fixture", citations=[],
        ))
        (opened if retired_at is None else closed).add(migrated_id("note", row_id))
    for state in ("open", "done", "released"):
        row_id = str(uuid4())
        values = {
            "id": row_id, "unit": UNIT, "scope": "unit", "platform": "", "conversation_id": "",
            "status": state, "revision": 1, "subject": "Generated commitment",
            "body": "Return the item", "made_at": ms, "last_revised_at": ms, "owed_to": [PERSON],
        }
        await conn.execute(sa.insert(LEDGER_COMMITMENT).values(values))
        await conn.execute(sa.insert(LEDGER_COMMITMENT_REVISION).values(values))
        (opened if state == "open" else closed).add(migrated_id("standing_commitment", row_id))
    return opened, closed


async def test_each_open_legacy_head_maps_once_and_closed_heads_mint_nothing(scenario):
    async with scenario.engine.begin() as conn:
        opened, closed = await legacy_heads(conn)
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy)
    deadline = DUE + timedelta(days=7)

    assert await migration.run(review_deadline=deadline) == {
        "reminders": 2, "events": 3, "note": 1, "standing_commitment": 1,
    }
    assert await migration.run(review_deadline=deadline) == dict.fromkeys(
        ("reminders", "events", "note", "standing_commitment"), 0,
    )

    async with scenario.engine.connect() as conn:
        rows = (await conn.execute(sa.select(INTENTION.c.id, INTENTION.c.state))).all()
    mapped = {graph_id for graph_id, state in rows if state == "open"}
    assert len(opened - mapped) == 0  # unmapped open rows
    assert len(closed & {row.id for row in rows}) == 0  # intentions from closed rows
    assert len(rows) == len(opened)


async def test_unresolved_legacy_room_aborts_the_transaction(scenario):
    async with scenario.engine.begin() as conn:
        await legacy_reminder(conn)
        await legacy_reminder(conn, conversation_id="unlisted-fixture")
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy)
    with pytest.raises(ValueError, match="no unique room"):
        await migration.run(review_deadline=DUE + timedelta(days=7))
    async with scenario.engine.connect() as conn:
        assert await conn.scalar(sa.select(sa.func.count()).select_from(INTENTION)) == 0


async def test_migrated_reminder_wording_stays_in_its_direct_room(scenario):
    async with scenario.engine.begin() as conn:
        row_id = await legacy_reminder(
            conn, conversation_id=DM, conversation_type="dm", requester_person_id=OTHER,
        )
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy)
    await migration.run(review_deadline=DUE + timedelta(days=7))
    intention = migrated_id("reminders", row_id)
    claim = intention.replace("intention:", "claim:", 1)
    query = references(intention, claim)

    there = await scenario.service().retrieve(ReadingRoom("signal", DM), query, BOUNDS)
    assert {row.graph_id for row in there.rows} == {intention, claim}
    here = await scenario.service().retrieve(ReadingRoom("signal", ROOM), query, BOUNDS)
    assert here.status == "nothing_found"
    assert here.rows == ()
