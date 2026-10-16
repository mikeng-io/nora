"""Lineage on the derived stores, against Postgres (migration `0073_lineage`).

Two halves. The BACKFILL: rows written before lineage existed get it from their
own citations, and origin-room-only lineage where a citation cannot be
resolved. The WRITERS: every new note, Standing and culture row gets the same
rule from its store, so the column is never left to a default a reader would
have to guess about.

Fixtures are generated: room ids, person ids and bodies are placeholders.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command

from nora.domain.lineage import (
    BODY,
    ROW,
    UNRESOLVED,
    WORKING_SET,
    LineageEntry,
    message,
    unresolved,
)
from nora.domain.standing import Standing, WorkingSetItem
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import alembic_config, run_migrations, sync_url_for
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.standing import SqlStandingStore

ROOM_A = "room-a"
ROOM_B = "room-b"
ROOM_C = "room-c"


def _insert_message(conn, *, room: str, ts: int, body: str = "a line") -> int:
    return conn.execute(sa.text(
        "INSERT INTO messages (platform, conversation_id, conversation_type, "
        "sender_id, ts, body) VALUES ('signal', :room, 'group', 'sender-1', :ts, :body) "
        "RETURNING id"
    ), {"room": room, "ts": ts, "body": body}).scalar_one()


def _insert_note(conn, *, note_id: str, origin: str, revisions: list[tuple[str, str, int]]) -> None:
    """A note and its chain, the head at the last revision, as a pre-0070
    writer left them."""
    head = revisions[-1][0]
    conn.execute(sa.text(
        "INSERT INTO note (id, unit, title, description, uncertainty, "
        "closing_condition, anchor_date, established_in, created_at, head_revision_id) "
        "VALUES (:id, 'unit-1', :title, 'd', 'u', 'c', 20260929, :origin, 1, :head)"
    ), {"id": note_id, "title": f"note {note_id[:4]}", "origin": origin, "head": head})
    parent = None
    for rev_id, body, written_at in revisions:
        conn.execute(sa.text(
            "INSERT INTO note_revision (id, note_id, body, parent_revision_id, "
            "written_at, established_by) VALUES (:id, :note, :body, :parent, :at, 'tag:p1')"
        ), {"id": rev_id, "note": note_id, "body": body, "parent": parent, "at": written_at})
        parent = rev_id


def _entries(raw, section: str) -> set[tuple]:
    """The stored triples of one section: the array itself on a per-row
    store, the section's array on a per-section one."""
    triples = raw if isinstance(raw, list) else raw[section]
    return {tuple(e) for e in triples}


KEY_A, KEY_B, KEY_C = "signal:room-a", "signal:room-b", "signal:room-c"


def test_the_backfill_derives_lineage_from_citations_and_unresolved_otherwise(pg_url):
    run_migrations(pg_url)
    alembic_command.downgrade(alembic_config(pg_url), "0072_row_history_search_path")
    engine = sa.create_engine(sync_url_for(pg_url))
    n1 = "00000000-0000-0000-0000-000000000001"
    n2 = "00000000-0000-0000-0000-000000000002"
    n3 = "00000000-0000-0000-0000-000000000003"
    try:
        with engine.begin() as conn:
            in_a = _insert_message(conn, room=ROOM_A, ts=1_000)
            in_b = _insert_message(conn, room=ROOM_B, ts=5_000)
            # n1: raised in A, first revision cites a line said in B, the
            # second (a legacy body) cites nothing from a room nobody recorded.
            _insert_note(conn, note_id=n1, origin=ROOM_A, revisions=[
                ("00000000-0000-0000-0001-000000000001", f"- a plan [{in_b}]", 10),
                ("00000000-0000-0000-0001-000000000002", "legacy prose", 20),
            ])
            # n2: raised in A, cites nothing: it rests on the room it was raised in.
            _insert_note(conn, note_id=n2, origin=ROOM_A, revisions=[
                ("00000000-0000-0000-0002-000000000001", "legacy prose", 30),
            ])
            # n3: cites a message since pruned.
            _insert_note(conn, note_id=n3, origin=ROOM_A, revisions=[
                ("00000000-0000-0000-0003-000000000001", f"- x [{in_a}, 999999]", 40),
            ])
            conn.execute(sa.text(
                "INSERT INTO standing (platform, conversation_id, body, updated_at, "
                "last_folded_row_id) VALUES ('signal', :room, 'a position', 9000, :last)"
            ), {"room": ROOM_A, "last": in_b})
            conn.execute(sa.text(
                "INSERT INTO culture_revision (id, culture_key, body, written_at, "
                "established_by) VALUES ('00000000-0000-0000-0009-000000000001', "
                ":room, 'an account', 777, 'dreamer:culture')"
            ), {"room": ROOM_A})
    finally:
        engine.dispose()

    run_migrations(pg_url)

    engine = sa.create_engine(sync_url_for(pg_url))
    try:
        with engine.connect() as conn:
            revs = {r.id: r for r in conn.execute(sa.text(
                "SELECT id::text, lineage, as_of FROM note_revision"
            ))}
            notes = {r.id: r for r in conn.execute(sa.text(
                "SELECT id::text, lineage, as_of FROM note"
            ))}
            standing = conn.execute(sa.text(
                "SELECT lineage, as_of FROM standing")).one()
            culture = conn.execute(sa.text(
                "SELECT lineage, as_of FROM culture_revision")).one()
    finally:
        engine.dispose()

    first = revs["00000000-0000-0000-0001-000000000001"]
    assert isinstance(first.lineage, list)   # per row: step 2a's array of triples
    assert _entries(first.lineage, ROW) == {
        ("message", None, KEY_B), ("message", None, KEY_A),
    }
    assert first.as_of == 5_000  # the cited message's time, not the write's
    later = revs["00000000-0000-0000-0001-000000000002"]
    assert _entries(later.lineage, ROW) == {(UNRESOLVED, None, KEY_A)}
    assert later.as_of == 20
    assert _entries(notes[n1].lineage, ROW) == {
        ("message", None, KEY_B), ("message", None, KEY_A), (UNRESOLVED, None, KEY_A),
    }
    assert notes[n1].as_of == 5_000
    assert _entries(notes[n2].lineage, ROW) == {("message", None, KEY_A)}
    assert _entries(notes[n3].lineage, ROW) == {
        ("message", None, KEY_A), (UNRESOLVED, None, KEY_A),
    }
    own = {("message", None, KEY_A), (UNRESOLVED, None, KEY_A)}
    assert _entries(standing.lineage, BODY) == own
    assert _entries(standing.lineage, WORKING_SET) == own
    assert standing.as_of == 5_000  # the newest message the fold read
    assert _entries(culture.lineage, BODY) == own
    assert culture.as_of == 777


@pytest.fixture
async def engine(pg_url):
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _message(engine, *, room: str, ts: int) -> int:
    async with engine.begin() as conn:
        return (await conn.execute(sa.text(
            "INSERT INTO messages (platform, conversation_id, conversation_type, "
            "sender_id, ts, body) VALUES ('signal', :room, 'group', 's', :ts, 'x') "
            "RETURNING id"
        ), {"room": room, "ts": ts})).scalar_one()


async def test_a_new_note_rests_on_its_room_and_every_room_it_cites(engine):
    await _message(engine, room=ROOM_A, ts=3_000)
    in_b = await _message(engine, room=ROOM_B, ts=4_000)
    store = SqlNoteStore(engine)
    note = await store.create(
        unit="unit-1", title="a plan", description="d", uncertainty="u",
        closing_condition="c", established_in=ROOM_A, body=f"- a plan [{in_b}]",
        established_by="tag:p1", now_ms=9_000, anchor_date=20260929,
    )
    read = await store.read(unit="unit-1", note_id=note.id)
    assert read is not None
    head, _rev = read
    assert set(head.lineage.section(ROW)) == {message(KEY_A), message(KEY_B)}
    async with engine.connect() as conn:
        as_of = (await conn.execute(sa.text(
            "SELECT as_of FROM note WHERE id = :id"), {"id": note.id})).scalar_one()
    assert as_of == 4_000


async def test_a_revision_from_another_room_widens_the_note_and_an_uncited_one_is_unresolved(
    engine,
):
    await _message(engine, room=ROOM_A, ts=500)   # room A's Record names its platform
    store = SqlNoteStore(engine)
    note = await store.create(
        unit="unit-1", title="a plan", description="d", uncertainty="u",
        closing_condition="c", established_in=ROOM_A, body="legacy",
        established_by="tag:p1", now_ms=1_000, anchor_date=20260929,
    )
    rev = await store.append_revision(
        unit="unit-1", note_id=note.id, body="legacy again",
        parent_revision_id=note.head_revision_id, established_by="tag:p2",
        now_ms=2_000, evidence_room=KEY_C,
    )
    head, _ = await store.read(unit="unit-1", note_id=note.id)  # type: ignore[misc]
    assert set(head.lineage.section(ROW)) == {message(KEY_A), message(KEY_C)}
    await store.append_revision(
        unit="unit-1", note_id=note.id, body="no room, no citation",
        parent_revision_id=rev.id, established_by="dreamer:x", now_ms=3_000,
    )
    head, _ = await store.read(unit="unit-1", note_id=note.id)  # type: ignore[misc]
    assert set(head.lineage.section(ROW)) == {
        message(KEY_A), message(KEY_C), unresolved(KEY_A),
    }


async def test_standing_and_culture_writes_rest_on_their_own_room(engine):
    last = await _message(engine, room=ROOM_A, ts=6_000)
    standing_store = SqlStandingStore(engine)
    await standing_store.write(standing=Standing(
        platform="signal", conversation_id=ROOM_A, body="a position",
        fold_count=1, updated_at=8_000, last_folded_row_id=last,
        working_set=(WorkingSetItem(what="an option", source="web_search", found_at=8_000),),
    ))
    held = await standing_store.read(platform="signal", conversation_id=ROOM_A)
    assert held is not None
    own = {message(KEY_A), unresolved(KEY_A)}
    assert set(held.lineage.section(BODY)) == own
    assert set(held.lineage.section(WORKING_SET)) == own
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT as_of, lineage FROM standing UNION ALL "
            "SELECT as_of, lineage FROM standing_revision"))).all()
    assert [r.as_of for r in rows] == [6_000, 6_000]
    assert rows[0].lineage == rows[1].lineage

    culture = SqlCultureStore(engine)
    await culture.append(key=ROOM_A, body="an account", written_at=9_000,
                         established_by="dreamer:culture")
    head = await culture.head(key=ROOM_A)
    assert head is not None
    assert set(head.lineage.section(BODY)) == own


def test_lineage_json_round_trips_and_a_malformed_entry_reads_as_unresolved():
    from nora.domain.lineage import Lineage

    good = Lineage.of({ROW: [message(KEY_B), message(KEY_A)]})
    assert good.to_json() == [["message", None, KEY_A], ["message", None, KEY_B]]
    assert Lineage.from_json(json.loads(json.dumps(good.to_json()))) == good
    bad = Lineage.from_json([[3, None, None], "nonsense"], origin=ROOM_A)
    assert set(bad.section(ROW)) == {LineageEntry(kind=UNRESOLVED, origin=ROOM_A)}
    assert Lineage.from_json(None).sections == ()



async def test_a_room_with_nothing_on_record_gets_only_unresolved(engine):
    """A note or culture account whose own room has no message on record has
    no platform to key it by: it rests on `unresolved` at the bare id, shown
    only there, never on a `message` entry naming a room it cannot key."""
    store = SqlNoteStore(engine)
    note = await store.create(
        unit="unit-1", title="quiet", description="d", uncertainty="u",
        closing_condition="c", established_in="room-silent", body="legacy",
        established_by="tag:p1", now_ms=1_000, anchor_date=20260929,
    )
    head, _ = await store.read(unit="unit-1", note_id=note.id)  # type: ignore[misc]
    assert set(head.lineage.section(ROW)) == {unresolved("room-silent")}
    culture = SqlCultureStore(engine)
    await culture.append(key="room-silent", body="an account", written_at=9_000,
                         established_by="dreamer:culture")
    acc = await culture.head(key="room-silent")
    assert acc is not None and set(acc.lineage.section(BODY)) == {unresolved("room-silent")}
