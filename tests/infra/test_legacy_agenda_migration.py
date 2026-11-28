"""Read back commitment-head coverage from generated legacy and graph rows."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.domain.lineage import LiveRoom, message
from nora.domain.ticks import due_tick_of_at
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, INTENTION
from nora.infra.sql.schema import (
    EVENT_REVISION,
    EVENTS,
    LEDGER_COMMITMENT,
    LEDGER_COMMITMENT_REVISION,
    LEDGER_DECISION,
    LEDGER_DECISION_REVISION,
    NOTE,
    NOTE_REVISION,
    REMINDERS,
)
from nora.memory import ReadingRoom, wire
from nora.memory.adapters.legacy_agenda import migrated_id
from nora.policy import Policy
from tests.infra.test_proactive_claims import DUE
from tests.infra.test_retrieval_scope import AT, BOUNDS, DM, OTHER, PERSON, ROOM, UNIT, references
from tests.infra.test_retrieval_scope import scenario as scenario

SERIES_ANCHOR = datetime(2026, 12, 31, 17, tzinfo=UTC)
SERIES_PENDING = datetime(2027, 2, 28, 17, tzinfo=UTC)
SERIES_END = datetime(2027, 4, 30, 16, tzinfo=UTC)
DECISION_BODY = (
    "## Options\n- Use the library [1]\n- Use the garden [2]\n"
    "## Leaning\n- none yet\n## Reopen if\n- The library closes [3]"
)


async def legacy_reminder(conn, state="pending", **fields):
    row_id = uuid4()
    due_at = fields.pop("due_at", DUE)
    await conn.execute(sa.insert(REMINDERS).values({
        "id": row_id, "requester_person_id": PERSON, "platform": "signal",
        "conversation_id": ROOM, "conversation_type": "group", "text": "Generated appointment",
        "created_at": AT, "state": state, "due_at": due_at, "due_tick": due_tick_of_at(due_at),
        **fields,
    }))
    return row_id


async def recurring_reminder(conn, *, count=4):
    return await legacy_reminder(
        conn, due_at=SERIES_PENDING, recurrence="monthly", recurrence_tz="America/New_York",
        recurrence_interval=2, recurrence_count=count, occurrence=1, recurrence_until=SERIES_END,
        series_anchor_at=SERIES_ANCHOR, series_id=uuid4(),
    )


def ledger_values(table, *, state="open", **fields):
    ms = int(AT.timestamp() * 1000)
    values = {
        "id": str(uuid4()), "unit": UNIT, "scope": "unit", "platform": "", "conversation_id": "",
        "status": state, "revision": 1, "subject": "Generated standing entry",
        "last_revised_at": ms,
        "lineage": [message(f"signal:{ROOM}").to_json()],
    }
    if table is LEDGER_DECISION:
        values.update(body=DECISION_BODY, opened_at=ms, closed_at=None if state == "open" else ms)
    else:
        values.update(body="Return the item", made_at=ms, owed_to=[PERSON])
    return {**values, **fields}


async def legacy_heads(conn):
    opened, closed = set(), set()
    for state in ("pending", "firing", "done", "cancelled", "failed"):
        row_id = await legacy_reminder(conn, state)
        target = opened if state in {"pending", "firing"} else closed
        target.add(migrated_id("reminders", row_id))
    opened.add(migrated_id("reminders", await recurring_reminder(conn)))
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
    for table, revision, states in (
        (LEDGER_COMMITMENT, LEDGER_COMMITMENT_REVISION, ("open", "done", "released")),
        (LEDGER_DECISION, LEDGER_DECISION_REVISION, ("open", "closed", "dropped")),
    ):
        for state in states:
            values = ledger_values(table, state=state)
            await conn.execute(sa.insert(table).values(values))
            await conn.execute(sa.insert(revision).values(values))
            (opened if state == "open" else closed).add(migrated_id(table.name, values["id"]))
    return opened, closed


async def test_each_open_legacy_head_maps_once_and_closed_heads_mint_nothing(scenario):
    async with scenario.engine.begin() as conn:
        opened, closed = await legacy_heads(conn)
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy, scenario.persons)
    deadline = DUE + timedelta(days=7)

    assert await migration.run(review_deadline=deadline) == {
        "reminders": 3, "events": 3, "note": 1, "standing_commitment": 1, "standing_decision": 1,
    }
    assert await migration.run(review_deadline=deadline) == dict.fromkeys(
        ("reminders", "events", "note", "standing_commitment", "standing_decision"), 0,
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
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy, scenario.persons)
    with pytest.raises(ValueError, match="no unique room"):
        await migration.run(review_deadline=DUE + timedelta(days=7))
    async with scenario.engine.connect() as conn:
        assert await conn.scalar(sa.select(sa.func.count()).select_from(INTENTION)) == 0


async def test_migrated_reminder_wording_stays_in_its_direct_room(scenario):
    async with scenario.engine.begin() as conn:
        row_id = await legacy_reminder(
            conn, conversation_id=DM, conversation_type="dm", requester_person_id=OTHER,
        )
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy, scenario.persons)
    await migration.run(review_deadline=DUE + timedelta(days=7))
    intention = migrated_id("reminders", row_id)
    claim = intention.replace("intention:", "claim:", 1)
    query = references(intention, claim)

    there = await scenario.service().retrieve(ReadingRoom("signal", DM), query, BOUNDS)
    assert {row.graph_id for row in there.rows} == {intention, claim}
    here = await scenario.service().retrieve(ReadingRoom("signal", ROOM), query, BOUNDS)
    assert here.status == "nothing_found"
    assert here.rows == ()


@pytest.mark.parametrize(
    "table", [LEDGER_COMMITMENT, LEDGER_DECISION], ids=lambda table: table.name,
)
async def test_unit_head_keeps_dm_lineage_and_exclusion(scenario, table):
    rooms = tuple(replace(room, persons=frozenset({OTHER})) for room in scenario.policy.rooms())
    scenario = replace(scenario, policy=Policy(
        rooms, _by_id={(room.platform, room.primary_id): room for room in rooms},
    ))
    citation = message(f"signal:{DM}")
    values = ledger_values(table, lineage=[citation.to_json()], exclude_persons=[PERSON])
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.insert(table).values(values))
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy, scenario.persons)
    await migration.run(review_deadline=DUE + timedelta(days=7))
    graph_id = migrated_id(table.name, values["id"])
    claim_id = graph_id.replace("intention:", "claim:")
    query = references(graph_id, claim_id)
    service = scenario.service()
    live = LiveRoom(frozenset({OTHER}))
    dm = ReadingRoom("signal", DM, live)

    visible = await service.retrieve(dm, query, BOUNDS)
    expected = {graph_id, claim_id}
    if table is LEDGER_DECISION:
        expected.add(f"{claim_id}:reopen")
    assert {row.graph_id for row in visible.rows} == expected
    group = await service.retrieve(ReadingRoom("signal", ROOM, live), query, BOUNDS)
    assert group.status == "nothing_found"
    excluded = await service.retrieve(dm, query, BOUNDS, concerns=frozenset({PERSON}))
    assert excluded.status == "nothing_found"
    async with scenario.engine.connect() as conn:
        saved = (await conn.execute(sa.select(INTENTION).where(INTENTION.c.id == graph_id))).one()
    assert citation.to_json() in saved.lineage["body"]
    assert saved.excluded_identities == [PERSON]


@pytest.mark.parametrize("lineage", [[], [["unresolved", None, f"signal:{ROOM}"]],
                                     [["message", None, "unlisted-fixture"]]])
async def test_unresolved_unit_head_leaves_no_migration_writes(scenario, lineage):
    async with scenario.engine.begin() as conn:
        await legacy_reminder(conn)
        await conn.execute(sa.insert(LEDGER_COMMITMENT).values(
            ledger_values(LEDGER_COMMITMENT, lineage=lineage),
        ))
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy, scenario.persons)

    with pytest.raises(ValueError, match="empty or unresolved lineage"):
        await migration.run(review_deadline=DUE + timedelta(days=7))
    async with scenario.engine.connect() as conn:
        assert await conn.scalar(sa.select(sa.func.count()).select_from(INTENTION)) == 0


async def _basis_texts(scenario, graph_id):
    async with scenario.engine.connect() as conn:
        return set(await conn.scalars(sa.select(CLAIM.c.content["text"].astext).join(
            EDGE, (EDGE.c.unit == CLAIM.c.unit) & (EDGE.c.dst_id == CLAIM.c.id),
        ).where(EDGE.c.src_id == graph_id, EDGE.c.type == "rests_on")))


async def test_open_decision_carries_options_and_reopen_condition(scenario):
    values = ledger_values(LEDGER_DECISION)
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.insert(LEDGER_DECISION).values(values))
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy, scenario.persons)
    await migration.run(review_deadline=DUE + timedelta(days=7))

    texts = await _basis_texts(scenario, migrated_id("standing_decision", values["id"]))
    assert texts == {
        f'{values["subject"]}\n{DECISION_BODY}', "Reopen if: The library closes [3]",
    }


async def test_event_carries_venue_people_end_and_latest_authored_notes(scenario):
    row_id, revision, head = uuid4(), uuid4(), uuid4()
    end = DUE + timedelta(hours=2)
    notes = "## Plan\n- Bring a generated map"
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.insert(EVENTS).values(
            id=row_id, unit_id=UNIT, platform="signal", conversation_id=ROOM,
            title="Generated event", created_by=PERSON, created_at=AT, status="confirmed",
            when=DUE, end=end, venue_text="Generated hall", venue_address="Generated road",
            people=[PERSON, OTHER], head_revision_id=head,
        ))
        await conn.execute(sa.insert(EVENT_REVISION).values(
            id=revision, event_id=row_id, title="Generated event", status="confirmed",
            written_at=AT, recorded_at=AT, body=notes,
        ))
        await conn.execute(sa.insert(EVENT_REVISION).values(
            id=head, event_id=row_id, parent_revision_id=revision, title="Generated event",
            status="confirmed", written_at=DUE, recorded_at=DUE,
        ))
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy, scenario.persons)
    await migration.run(review_deadline=DUE + timedelta(days=7))

    texts = await _basis_texts(scenario, migrated_id("events", row_id))
    assert texts == {
        f"Generated event\nVenue: Generated hall\nVenue address: Generated road\n"
        f"People: {PERSON}, {OTHER}\nEnd: {end.isoformat()}\n{notes}",
    }
