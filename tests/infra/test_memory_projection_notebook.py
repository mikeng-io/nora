"""The Notebook's projection into the memory index: every note, memo, lesson,
event and culture or Character write keeps its entry's node and edges, in the
store's own transaction, at the scope the store itself reads it by.

Module-scoped database per `test_memory_projection_record.py`'s pattern (one
migrated database, truncated between tests)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.gatherings import RSVP, Gathering, GatheringStatus, RSVPResponse
from nora.domain.models import NormalizedMessage
from nora.domain.notes import Note, NoteSupersedeReason
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import (
    CULTURE_REVISION,
    MEMORY_EDGE,
    MEMORY_NODE,
    MEMORY_PROJECTION_QUEUE,
    NOTE,
    NOTE_REVISION,
)
from nora.subject.knowledge import _seed_note_with_id

pytestmark = pytest.mark.asyncio

_ROOM = "signal:g1"
_UNIT = "unit-a"


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_memory_notebook_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in (
                "messages", "note", "note_revision", "memo", "memo_revision", "lesson",
                "lesson_revision", "events", "event_revision", "event_rsvps", "culture",
                "culture_revision", "reminders", "row_history", "memory_node",
                "memory_edge", "memory_projection_queue",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


async def _said(engine: Any, body: str = "dinner on friday?") -> str:
    """A message in the room, so the Record can name `g1`'s room key."""
    mid = await SqlRecordStore(engine).append(
        message=NormalizedMessage(
            "signal", "g1", "group", "+10000000001", "Member A", 1000, body, {},
        ),
        person_id="member-a",
    )
    return str(mid)


async def _nodes(engine: Any) -> dict[tuple[str, str], Any]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(MEMORY_NODE))).all()
    return {(r.kind, r.source_id): r for r in rows}


async def _edges(engine: Any, kind: str, sid: str) -> set[tuple[str, str, str, str, str]]:
    """Every edge into or out of `(kind, sid)`."""
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id, MEMORY_EDGE.c.rel,
            MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id,
        ).where(sa.or_(
            sa.and_(MEMORY_EDGE.c.src_kind == kind, MEMORY_EDGE.c.src_id == sid),
            sa.and_(MEMORY_EDGE.c.dst_kind == kind, MEMORY_EDGE.c.dst_id == sid),
        )))).all()
    return {tuple(r) for r in rows}


async def _citations(engine: Any, revision_id: str) -> list[str]:
    async with engine.connect() as conn:
        return list((await conn.execute(
            sa.select(NOTE_REVISION.c.citations).where(NOTE_REVISION.c.id == revision_id)
        )).scalar_one())


async def _note(store: SqlNoteStore, title: str, body: str, now_ms: int = 10) -> Note:
    return await store.create(
        unit=_UNIT, title=title, description="an open question",
        uncertainty="not settled", closing_condition="someone confirms",
        established_in="g1", body=body, established_by="tag:member-a", now_ms=now_ms,
        anchor_date=20261020, anchor_entity_ids=("Member B",),
    )


async def test_a_note_is_projected_through_its_revisions_and_retirement(engine):
    await _said(engine)
    store = SqlNoteStore(engine)
    note = await _note(store, "Friday dinner", "- where to eat [1]")
    event_id = str(uuid4())
    async with engine.begin() as conn:
        await conn.execute(sa.update(NOTE).where(NOTE.c.id == note.id).values(
            event_id=event_id, exclude_persons=["member-c"],
        ))

    node = (await _nodes(engine))[("note", note.id)]
    assert (node.unit, node.origin_room, node.persons, node.state) == (
        _UNIT, None, [], "live",
    )
    assert node.text == "Friday dinner\n- where to eat [1]"

    revision = await store.append_revision(
        unit=_UNIT, note_id=note.id, body="- the noodle place [1]",
        parent_revision_id=note.head_revision_id, established_by="tag:member-a",
        now_ms=20,
    )
    node = (await _nodes(engine))[("note", note.id)]
    assert node.text == "Friday dinner\n- the noodle place [1]"
    assert (node.at_ms, node.exclude_persons) == (20, ["member-c"])
    # The free-text anchor is not a resolved person: no `about` edge from it.
    assert await _edges(engine, "note", note.id) == {
        ("note", note.id, "in_room", "room", _ROOM),
        ("note", note.id, "tracks", "event", event_id),
        ("note", note.id, "cites", "message", "1"),
    }

    successor = await _note(store, "Friday dinner, booked", "- booked [1]")
    assert await store.supersede(
        key=_UNIT, target=note.id, replaced_by=successor.id,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=30,
        expected_head=revision.id,
    ) == 1
    assert (await _nodes(engine))[("note", note.id)].state == "superseded"
    assert ("note", successor.id, "supersedes", "note", note.id) in await _edges(
        engine, "note", note.id,
    )
    # Re-projecting the successor keeps the edge its predecessor's column owns.
    await store.append_revision(
        unit=_UNIT, note_id=successor.id, body="- booked for eight [1]",
        parent_revision_id=successor.head_revision_id, established_by="tag:member-a",
        now_ms=40,
    )
    assert ("note", successor.id, "supersedes", "note", note.id) in await _edges(
        engine, "note", note.id,
    )

    lone = await _note(store, "Saturday hike", "- maybe [1]")
    await store.supersede(
        key=_UNIT, target=lone.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_EXPIRED, source=None, now_ms=50,
    )
    assert (await _nodes(engine))[("note", lone.id)].state == "retired"


async def test_a_merge_projects_the_survivor_and_the_folded_duplicate(engine):
    store = SqlNoteStore(engine)
    survivor = await _note(store, "Trip plan", "- dates [1]")
    duplicate = await _note(store, "Trip plans", "- budget [1]")
    await store.merge_into(
        unit=_UNIT, survivor_id=survivor.id, survivor_head=survivor.head_revision_id,
        duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
        body="- dates [1]\n- budget [1]", anchor_date=20261020, revisit_at=99,
        reset_strikes=False, established_by="cognition", now_ms=60,
    )
    nodes = await _nodes(engine)
    assert nodes[("note", survivor.id)].text == "Trip plan\n- dates [1]\n- budget [1]"
    assert nodes[("note", duplicate.id)].state == "superseded"
    assert ("note", survivor.id, "supersedes", "note", duplicate.id) in await _edges(
        engine, "note", duplicate.id,
    )


async def test_a_note_cites_what_its_head_revision_cites(engine):
    """Step 4 of the unified-memory sequence: a note revision stores its
    citations as a memo revision does, and the index draws `cites` edges from
    the HEAD's, so a walk out of a cited message reaches the note. A revision
    that stops citing a message takes its edge away; a merge's folded body
    cites what both notes rested on."""
    first, second = await _said(engine), await _said(engine, "or saturday?")
    store = SqlNoteStore(engine)
    note = await _note(
        store, "Weekend dinner",
        f"## Decided\n## Open\n- which day [{first}, rollup(outing|x|a/b)]",
    )
    assert await _citations(engine, note.head_revision_id) == [first, "rollup(outing|x|a/b)"]
    cites = {e for e in await _edges(engine, "note", note.id) if e[2] == "cites"}
    assert cites == {
        ("note", note.id, "cites", "message", first),
        ("note", note.id, "cites", "outing", "x|a/b"),
    }

    revision = await store.append_revision(
        unit=_UNIT, note_id=note.id, body=f"## Decided\n## Open\n- which day [{second}]",
        parent_revision_id=note.head_revision_id, established_by="tag:member-a", now_ms=20,
    )
    assert await _citations(engine, revision.id) == [second]
    cites = {e for e in await _edges(engine, "note", note.id) if e[2] == "cites"}
    assert cites == {("note", note.id, "cites", "message", second)}

    other = await _note(store, "Weekend dinners", f"## Decided\n## Open\n- who [{first}]")
    folded = await store.merge_into(
        unit=_UNIT, survivor_id=note.id, survivor_head=revision.id,
        duplicate_id=other.id, duplicate_head=other.head_revision_id,
        body=f"## Decided\n## Open\n- which day [{second}]\n- who [{first}]",
        anchor_date=20261020, revisit_at=99, reset_strikes=False,
        established_by="cognition", now_ms=30,
    )
    assert await _citations(engine, folded.id) == [second, first]
    cites = {e for e in await _edges(engine, "note", note.id) if e[2] == "cites"}
    assert cites == {
        ("note", note.id, "cites", "message", second),
        ("note", note.id, "cites", "message", first),
    }


async def test_a_seeded_sandbox_note_is_projected(engine):
    note = Note(
        id=str(uuid4()), unit=_UNIT, title="Seeded question", description="seeded",
        uncertainty="open", closing_condition="answered", established_in="g1",
        created_at=5, head_revision_id=str(uuid4()), anchor_date=20261020,
    )
    await _seed_note_with_id(
        engine, note=note, body="- seeded body [1]", established_by="seed", written_at=5,
    )
    node = (await _nodes(engine))[("note", note.id)]
    assert (node.unit, node.text) == (_UNIT, "Seeded question\n- seeded body [1]")
    assert await _citations(engine, note.head_revision_id) == ["1"]
    assert ("note", note.id, "cites", "message", "1") in await _edges(engine, "note", note.id)


async def test_a_memo_cites_messages_and_is_retired_when_withdrawn(engine):
    mid = await _said(engine)
    store = SqlMemoStore(engine)
    memo = await store.create(
        unit=_UNIT, platform="signal", conversation_id="g1", title="Wifi",
        body="the code is on the fridge", citations=(mid, "rollup(outing|x|a/b)"),
        mode="told", established_by="cognition", model=None, now_ms=10,
    )
    sid = str(memo.id)
    node = (await _nodes(engine))[("memo", sid)]
    assert (node.unit, node.origin_room, node.text, node.state) == (
        _UNIT, None, "Wifi\nthe code is on the fridge", "live",
    )
    # An episode's Rollup key is an edge to that key as cited: it reaches the
    # outing's node whenever the current build holds that happening.
    assert await _edges(engine, "memo", sid) == {
        ("memo", sid, "in_room", "room", _ROOM),
        ("memo", sid, "cites", "message", mid),
        ("memo", sid, "cites", "outing", "x|a/b"),
    }

    await store.append(
        unit=_UNIT, memo_id=sid, base_revision_id=str(memo.head.id), title="Wifi",
        body="the code changed", citations=(mid,), mode="told", state="withdrawn",
        established_by="cognition", model=None, now_ms=20,
    )
    node = (await _nodes(engine))[("memo", sid)]
    assert (node.text, node.state, node.at_ms) == ("Wifi\nthe code changed", "retired", 20)


async def test_a_lesson_cites_messages_and_is_retired(engine):
    mid = await _said(engine)
    store = SqlLessonStore(engine)
    lesson = await store.create(
        unit=_UNIT, body="ask before booking", citations=(mid, "response:7"),
        written_at=10, established_by="lessons",
    )
    sid = str(lesson.id)
    node = (await _nodes(engine))[("lesson", sid)]
    assert (node.unit, node.text, node.state) == (_UNIT, "ask before booking", "live")
    assert await _edges(engine, "lesson", sid) == {("lesson", sid, "cites", "message", mid)}

    await store.append(
        lesson_id=sid, expected_seq=1, body="ask before booking", citations=(mid,),
        state="retired", retired_by="session", relearn_after=None, note=None,
        written_at=20, established_by="lessons",
    )
    assert (await _nodes(engine))[("lesson", sid)].state == "retired"


def _event(**overrides: Any) -> Gathering:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    defaults: dict[str, Any] = {
        "id": uuid4(), "conversation_id": "g1", "title": "Dinner",
        "created_by": "member-a", "when": now + timedelta(days=3),
        "venue_text": "the noodle place", "venue_place_id": "place-1",
        "venue_name": "Noodle House", "venue_address": "1 Example Road",
        "venue_rating": 4.5, "venue_price_level": "", "venue_maps_url": None,
        "venue_resolved_at": now, "status": GatheringStatus.PROPOSED,
        "reminder_id": None, "created_at": now, "unit_id": _UNIT,
        "people": ("member-a", "member-b"), "exclude_persons": ("member-c",),
    }
    defaults.update(overrides)
    return Gathering(**defaults)


async def test_an_event_its_people_and_its_venue_are_projected(engine):
    await _said(engine)
    store = SqlGatheringStore(engine)
    event = await store.create(_event())
    sid = str(event.id)

    nodes = await _nodes(engine)
    node = nodes[("event", sid)]
    assert (node.unit, node.origin_room, node.exclude_persons, node.state) == (
        _UNIT, None, ["member-c"], "live",
    )
    assert node.text == "Dinner\nNoodle House"
    assert node.at_ms == int(event.when.timestamp() * 1000)
    # The place hub is named, and never located: no address, rating or link.
    place = nodes[("place", "place-1")]
    assert place.text == "Noodle House"
    assert (place.unit, place.origin_room, place.persons) == (None, None, [])
    assert "Example Road" not in place.text + node.text
    assert await _edges(engine, "event", sid) == {
        ("event", sid, "in_room", "room", _ROOM),
        ("event", sid, "about", "person", "member-a"),
        ("event", sid, "about", "person", "member-b"),
        ("event", sid, "about", "place", "place-1"),
    }

    await store.add_rsvp(RSVP(
        event_id=event.id, person_id="member-d", response=RSVPResponse.YES,
        responded_at=datetime(2026, 10, 2, tzinfo=UTC),
    ))
    assert ("event", sid, "about", "person", "member-d") in await _edges(engine, "event", sid)

    await store.update(replace(event, title="Dinner, moved", status=GatheringStatus.CANCELLED))
    node = (await _nodes(engine))[("event", sid)]
    assert (node.text, node.state) == ("Dinner, moved\nNoodle House", "retired")
    assert ("event", sid, "about", "person", "member-d") in await _edges(engine, "event", sid)


async def _all_edges(engine: Any) -> set[tuple[str, str, str, str, str]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id, MEMORY_EDGE.c.rel,
            MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id,
        ))).all()
    return {tuple(r) for r in rows}


_MK = "district:旺角 / Mong Kok"
_KLN = "city:九龍 / Kowloon"


async def test_a_venue_is_located_in_its_district_and_city_and_goes_when_nothing_names_it(
    engine,
):
    """Step 3a: the venue's stored areas become place hubs named by them and
    a `located_in` chain venue -> district -> city. A second event at the same
    venue with no area (resolved before 0118) leaves the chain alone. When no
    event names the venue any more, it goes, and the district and city with
    it: nothing else lies in them. A rebuild from empty writes exactly what
    live projection wrote."""
    await _said(engine)
    store = SqlGatheringStore(engine)
    first = await store.create(_event(
        venue_district="旺角 / Mong Kok", venue_city="九龍 / Kowloon",
    ))
    second = await store.create(_event(people=(), exclude_persons=()))
    nodes = await _nodes(engine)
    assert (nodes[("place", _MK)].text, nodes[("place", _KLN)].text) == (
        "旺角 / Mong Kok", "九龍 / Kowloon",
    )
    assert all(
        (nodes[("place", p)].unit, nodes[("place", p)].origin_room, nodes[("place", p)].persons)
        == (None, None, [])
        for p in (_MK, _KLN)
    )
    chain = {
        ("place", "place-1", "located_in", "place", _MK),
        ("place", _MK, "located_in", "place", _KLN),
    }
    assert chain <= await _all_edges(engine)

    live_nodes = {k: n.text for k, n in (await _nodes(engine)).items()}
    live_edges = await _all_edges(engine)
    async with engine.begin() as conn:
        await conn.execute(sa.delete(MEMORY_EDGE))
        await conn.execute(sa.delete(MEMORY_NODE))
    await memory_index.rebuild(engine)
    assert {k: n.text for k, n in (await _nodes(engine)).items()} == live_nodes
    assert await _all_edges(engine) == live_edges

    for event in (first, second):
        await store.update(replace(
            event, venue_place_id="place-2", venue_name="Elsewhere",
            venue_district=None, venue_city=None,
        ))
    nodes = await _nodes(engine)
    assert ("place", "place-2") in nodes
    assert not {("place", "place-1"), ("place", _MK), ("place", _KLN)} & set(nodes)
    assert not {e for e in await _all_edges(engine) if e[2] == "located_in"}


async def test_an_event_with_no_unit_is_scoped_to_its_room(engine):
    """A row from before `0074` carries `unit_id = ''`: its room, not every
    unit's, and a room the Record cannot name matches no read."""
    await _said(engine)
    store = SqlGatheringStore(engine)
    known = await store.create(_event(unit_id="", venue_place_id=None))
    unknown = await store.create(_event(unit_id="", conversation_id="nowhere"))
    nodes = await _nodes(engine)
    assert (nodes[("event", str(known.id))].unit,
            nodes[("event", str(known.id))].origin_room) == (None, _ROOM)
    assert nodes[("event", str(unknown.id))].origin_room == "nowhere"


async def test_culture_and_character_accounts_take_their_keys_scope(engine):
    mid = await _said(engine)
    store = SqlCultureStore(engine)
    person_key = character_key(_UNIT, "member-b")
    for key in ("g1", unit_culture_key(_UNIT), nora_character_key(_UNIT), person_key):
        await store.append(
            key=key, body=f"account of {key}", written_at=10, established_by="dreamer",
            citations=(mid, "response:3"),
        )
    nodes = await _nodes(engine)

    room = nodes[("character", "g1")]
    assert (room.unit, room.origin_room, room.persons) == (None, _ROOM, [])
    assert ("character", "g1", "about", "room", _ROOM) in await _edges(engine, "character", "g1")

    unit = nodes[("character", unit_culture_key(_UNIT))]
    assert (unit.unit, unit.origin_room, unit.persons) == (_UNIT, None, [])
    assert ("character", unit_culture_key(_UNIT), "about", "unit", _UNIT) in await _edges(
        engine, "character", unit_culture_key(_UNIT),
    )
    assert ("character", nora_character_key(_UNIT), "about", "nora", "nora") in await _edges(
        engine, "character", nora_character_key(_UNIT),
    )

    # A person's account: the unit AND the person, so only a room that lists
    # them can read it.
    person = nodes[("character", person_key)]
    assert (person.unit, person.origin_room, person.persons, person.text) == (
        _UNIT, None, ["member-b"], f"account of {person_key}",
    )
    assert await _edges(engine, "character", person_key) == {
        ("character", person_key, "about", "person", "member-b"),
        ("character", person_key, "cites", "message", mid),
    }

    await store.append(
        key=person_key, body="a revised account", written_at=20, established_by="dreamer",
        citations=(mid,),
    )
    assert (await _nodes(engine))[("character", person_key)].text == "a revised account"


async def test_an_old_room_keyed_person_account_is_never_indexed(engine):
    """`<room primary_id>#character:<p>` was written from one room's messages;
    a unit scope would show it in every room of the unit."""
    await _said(engine)
    old_key = "g1#character:member-b"
    await SqlCultureStore(engine).append(
        key=old_key, body="an old per-room account", written_at=10, established_by="dreamer",
    )
    nodes = await _nodes(engine)
    assert ("character", old_key) not in nodes
    assert not any(n.text == "an old per-room account" for n in nodes.values())
    assert await _edges(engine, "character", old_key) == set()


async def test_a_room_keyed_account_with_no_trace_of_its_room_is_never_indexed(engine):
    """The room has no message left and no culture of its own, so nothing
    says it is a room; nothing says it is a unit either, and only a proven
    unit is indexed."""
    old_key = "g9#character:member-b"
    await SqlCultureStore(engine).append(
        key=old_key, body="an orphaned per-room account", written_at=10,
        established_by="dreamer",
    )
    assert ("character", old_key) not in await _nodes(engine)
    assert await _edges(engine, "character", old_key) == set()


async def test_a_character_accounts_exclude_persons_is_copied(engine):
    """`culture_revision.exclude_persons` exists (`0073_lineage`) but
    `SqlCultureStore.append` never sets it, so a written account's node holds
    `[]`; a head that does carry one has it copied onto the node."""
    store = SqlCultureStore(engine)
    await store.append(
        key=unit_culture_key(_UNIT), body="the unit", written_at=5, established_by="dreamer",
    )
    key = character_key(_UNIT, "member-b")
    await store.append(key=key, body="an account", written_at=10, established_by="dreamer")
    assert (await _nodes(engine))[("character", key)].exclude_persons == []

    async with engine.begin() as conn:
        await conn.execute(
            sa.update(CULTURE_REVISION).where(CULTURE_REVISION.c.culture_key == key)
            .values(exclude_persons=["member-c"])
        )
        await memory_index.project(conn, "character", key)
    assert (await _nodes(engine))[("character", key)].exclude_persons == ["member-c"]


async def test_a_failed_notebook_projection_queues_and_the_entry_stands(engine, monkeypatch):
    async def broken(conn: Any, source_id: str) -> None:
        raise RuntimeError("index down")

    monkeypatch.setitem(memory_index._PROJECTORS, "lesson", broken)
    lesson = await SqlLessonStore(engine).create(
        unit=_UNIT, body="still written", citations=("1",), written_at=10,
        established_by="lessons",
    )
    assert (await SqlLessonStore(engine).get(lesson_id=str(lesson.id))) is not None
    async with engine.connect() as conn:
        queued = (await conn.execute(sa.select(
            MEMORY_PROJECTION_QUEUE.c.kind, MEMORY_PROJECTION_QUEUE.c.source_id,
            MEMORY_PROJECTION_QUEUE.c.last_error,
        ))).all()
    assert [tuple(q) for q in queued] == [("lesson", str(lesson.id), "RuntimeError")]
    assert ("lesson", str(lesson.id)) not in await _nodes(engine)
