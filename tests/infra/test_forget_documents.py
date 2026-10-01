"""`nora person forget` reaches documents (operator ruling 2026-10-01, DEC-259,
design/artifact/00-design.md §33).

A document is the forgotten person's when they CREATED it. Each is soft-deleted through the
`DocumentStore` port (one more `document_state` row, `set_by` naming the forget), never
purged, and every read a person makes then answers it like an unknown id. A document the
person only appears in as a participant of its room is not theirs and is left alone.

One migrated database for the module (the `test_withdrawal.py` pattern); every test names
its own people, so no test sees another's rows and nothing needs truncating.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.app.documents_service import DocumentService
from nora.app.person_forget import (
    FORGET_SET_BY_PREFIX,
    forget_person,
    render_forget,
    unforget_person,
)
from nora.domain.documents import RevisionIn
from nora.infra.sql.documents import SqlDocumentStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import DOCUMENT_STATE
from nora.persons import Persons
from nora.testing import FakeMediaObjectStore
from nora.web.documents import build_document_payload, build_documents_payload

pytestmark = pytest.mark.asyncio

ROOM = ("signal", "room-1")
NOBODY = Persons(by_person={}, index={})


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_forget_docs_" + uuid4().hex
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
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
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
        await eng.dispose()


def _rev(title: str) -> RevisionIn:
    return RevisionIn(
        title=title,
        spec={"version": 1, "root": {"type": "Page", "props": {"title": title}, "children": []}},
        text=title,
        sha256="a" * 64,
        media=(),
        written_by="tag:1",
    )


async def _make(store: SqlDocumentStore, *, by: str, title: str) -> UUID:
    document_id = uuid4()
    await store.create(
        document_id=document_id, platform=ROOM[0], conversation_id=ROOM[1],
        created_by=by, revision=_rev(title),
    )
    return document_id


async def _state_rows(engine: Any, document_id: UUID) -> list[tuple[str, str]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(DOCUMENT_STATE.c.state, DOCUMENT_STATE.c.set_by)
            .where(DOCUMENT_STATE.c.document_id == document_id)
            .order_by(DOCUMENT_STATE.c.id)
        )).all()
    return [(r.state, r.set_by) for r in rows]


def _service(store: SqlDocumentStore) -> DocumentService:
    return DocumentService(
        store=store,
        records=None,  # type: ignore[arg-type]  # `read` never touches the transcript
        media=FakeMediaObjectStore((), fail=False),
        media_window=50,
        room_ids=lambda platform, cid: frozenset({cid}),
        is_person=lambda person_id: True,
    )


async def test_forget_deletes_what_the_person_created_and_leaves_everyone_elses(engine):
    ada, bea = "ada-" + uuid4().hex[:6], "bea-" + uuid4().hex[:6]
    store = SqlDocumentStore(engine)
    mine_active = await _make(store, by=ada, title="ada active")
    mine_archived = await _make(store, by=ada, title="ada archived")
    assert await store.set_state(document_id=mine_archived, state="archived", set_by=ada)
    # Bea's, made in the very room Ada is named in: Ada is only a participant of it.
    theirs = await _make(store, by=bea, title="bea in ada's room")
    rooms = frozenset({ROOM})

    report = await forget_person(
        engine, documents=store, person_id=ada, apply=True, now_ms=1,
    )

    assert report.documents == 2 and report.documents_already_deleted == 0
    for document_id in (mine_active, mine_archived):
        rows = await _state_rows(engine, document_id)
        assert rows[-1] == ("deleted", FORGET_SET_BY_PREFIX + ada)
        # Soft: the revision is still there and the service's own read still finds it.
        raw = await store.get(document_id=document_id)
        assert raw is not None and raw.summary.state == "deleted"
        assert [h.revision for h in await store.history(document_id=document_id)] == [1]
        # Every read a person makes answers it like an unknown id: by id, for the creator
        # and for a room member, and through the room service and the portal payload.
        assert await store.get_visible(document_id=document_id, person_id=ada, rooms=rooms) is None
        assert await store.get_visible(document_id=document_id, person_id=bea, rooms=rooms) is None
        read = await _service(store).read(
            platform=ROOM[0], conversation_id=ROOM[1], document_id=document_id
        )
        assert read.status == "refused" and read.findings[0].rule == "no_such_document"
        assert await build_document_payload(
            store=store, rooms_of=lambda _p: rooms, persons=NOBODY,
            person_id=bea, document_id=document_id,
        ) is None
    # NEGATIVE: the other person's document is untouched and still reads.
    assert await _state_rows(engine, theirs) == []
    got = await store.get_visible(document_id=theirs, person_id=bea, rooms=frozenset())
    assert got is not None and got.summary.state == "active"
    listed = await build_documents_payload(
        store=store, rooms_of=lambda _p: rooms, persons=NOBODY,
        person_id=bea, page=1, page_size=50,
    )
    assert [d["id"] for d in listed["documents"]] == [str(theirs)]  # type: ignore[attr-defined]
    assert render_forget(report)[2] == "  deleted 2 document(s)"


async def test_a_dry_run_counts_and_writes_nothing_and_a_rerun_is_idempotent(engine):
    ada = "ada-" + uuid4().hex[:6]
    store = SqlDocumentStore(engine)
    mine = await _make(store, by=ada, title="mine")
    await _make(store, by=ada, title="mine too")

    dry = await forget_person(engine, documents=store, person_id=ada, apply=False, now_ms=1)
    assert dry.documents == 2
    assert await _state_rows(engine, mine) == [], "a dry run must write nothing"
    assert render_forget(dry)[2] == "  would delete 2 document(s)"

    first = await forget_person(engine, documents=store, person_id=ada, apply=True, now_ms=1)
    again = await forget_person(engine, documents=store, person_id=ada, apply=True, now_ms=2)
    assert first.documents == 2
    assert again.documents == 0 and again.documents_already_deleted == 2
    assert len(await _state_rows(engine, mine)) == 1, "a re-run appends no second row"


async def test_a_document_the_person_already_deleted_keeps_their_own_set_by(engine):
    ada = "ada-" + uuid4().hex[:6]
    store = SqlDocumentStore(engine)
    own = await _make(store, by=ada, title="deleted by hand")
    assert await store.set_state(document_id=own, state="deleted", set_by=ada)

    report = await forget_person(engine, documents=store, person_id=ada, apply=True, now_ms=1)

    assert report.documents == 0 and report.documents_already_deleted == 1
    assert await _state_rows(engine, own) == [("deleted", ada)]


async def test_a_forgotten_persons_document_restores_and_unforget_leaves_it_deleted(engine):
    ada = "ada-" + uuid4().hex[:6]
    store = SqlDocumentStore(engine)
    mine = await _make(store, by=ada, title="mine")
    await forget_person(engine, documents=store, person_id=ada, apply=True, now_ms=1)

    await unforget_person(engine, person_id=ada, apply=True)
    assert (await store.get(document_id=mine)).summary.state == "deleted"  # type: ignore[union-attr]

    # Restorable: one more state row, nothing was purged.
    assert await store.set_state(document_id=mine, state="active", set_by="operator")
    restored = await store.get_visible(document_id=mine, person_id=ada, rooms=frozenset())
    assert restored is not None and restored.summary.title == "mine"


async def test_forget_never_overwrites_a_state_changed_since_its_snapshot(engine):
    """The race, end to end: between forget's read of a person's documents and its write, a
    member archives one. The conditional write finds it changed, writes nothing for it,
    does not count it, and says so; a re-run decides again from the new state."""
    ada, mei = "ada-" + uuid4().hex[:6], "mei-" + uuid4().hex[:6]
    plain = SqlDocumentStore(engine)
    contested = await _make(plain, by=ada, title="contested")
    quiet = await _make(plain, by=ada, title="quiet")

    class _Racy(SqlDocumentStore):
        async def list_created_by(self, **kw):  # type: ignore[no-untyped-def]
            seen = await super().list_created_by(**kw)
            if kw["states"] == ("active", "archived"):
                # A member archives one AFTER the snapshot was read.
                await plain.set_state(document_id=contested, state="archived", set_by=mei)
            return seen

    report = await forget_person(
        engine, documents=_Racy(engine), person_id=ada, apply=True, now_ms=1,
    )
    assert report.documents == 1 and report.documents_skipped == 1
    assert await _state_rows(engine, contested) == [("archived", mei)], "not overwritten"
    assert (await _state_rows(engine, quiet))[-1] == ("deleted", FORGET_SET_BY_PREFIX + ada)
    assert "1 changed since the read" in render_forget(report)[2]
    # The re-run decides again from what is there now, and finishes it.
    again = await forget_person(engine, documents=plain, person_id=ada, apply=True, now_ms=2)
    assert again.documents == 1 and again.documents_skipped == 0
    assert (await _state_rows(engine, contested))[-1] == ("deleted", FORGET_SET_BY_PREFIX + ada)


async def test_a_failure_part_way_leaves_a_printed_partial_report_and_a_rerun_completes_it(engine):
    ada = "ada-" + uuid4().hex[:6]
    plain = SqlDocumentStore(engine)
    made = [await _make(plain, by=ada, title=f"d{i}") for i in range(3)]

    class _Breaks(SqlDocumentStore):
        calls = 0

        async def set_state(self, **kw):  # type: ignore[no-untyped-def]
            _Breaks.calls += 1
            if _Breaks.calls == 2:
                raise RuntimeError("the database went away")
            return await super().set_state(**kw)

    report = await forget_person(
        engine, documents=_Breaks(engine), person_id=ada, apply=True, now_ms=1,
    )
    assert report.failure == "RuntimeError"
    assert report.documents == 1, "what was written is reported, not thrown away"
    lines = render_forget(report)
    assert any("deleted 1 document(s)" in line for line in lines)
    assert any("FAILED part-way (RuntimeError)" in line and "re-run" in line for line in lines)
    written = [d for d in made if await _state_rows(engine, d)]
    assert len(written) == 1
    # A re-run completes the rest, and only the rest.
    again = await forget_person(engine, documents=plain, person_id=ada, apply=True, now_ms=2)
    assert again.failure == "" and again.documents == 2
    for d in made:
        assert (await _state_rows(engine, d))[-1][0] == "deleted"
