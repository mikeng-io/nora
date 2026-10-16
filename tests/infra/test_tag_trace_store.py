"""`SqlTagTraceStore` — write-once archival retention for `TagTrace`.
Single-store file, not a contract suite (there is only one
implementation), but the database setup follows
`tests/contract/test_note_store_contract.py::_notes_db`'s MODULE-scoped
pattern rather than the function-scoped `pg_url` fixture directly — CLAUDE.md's
rule that a suite doing real-Postgres work at this grain scopes the database
to the MODULE (one `CREATE DATABASE` + migration chain for the whole file,
TRUNCATE between tests) rather than rebuilding the world per test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.tag_trace import TagTrace
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.tag_traces import SqlTagTraceStore

NOW = 1_723_000_000_000


@pytest.fixture(scope="module")
def _tag_trace_db(pg_admin: Any) -> Iterator[str]:
    """ONE database and ONE migration run for this whole file — see
    `_notes_db`'s own docstring for why: rebuilding a fresh database (and
    replaying the full migration chain) per test measurably degrades the
    shared testcontainer and the cost lands on an unrelated wall-clock test
    elsewhere in the suite."""
    dbname = "t_tag_traces_" + uuid4().hex
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
async def tag_trace_engine(_tag_trace_db: str) -> AsyncIterator[Any]:
    """The ENGINE is per test (cheap); the DATABASE and its migrations are
    module-scoped (see `_tag_trace_db`)."""
    engine = create_sql_engine(_tag_trace_db)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("truncate tag_trace"))
        await engine.dispose()


def _row(ts: int = 1_722_999_999_000) -> TranscriptRow:
    """A real `TranscriptRow` carrying a `DeliveryStatus` — the field that is
    NOT a JSON primitive and so is the one a naive `row.__dict__` write into a
    JSONB column would fail (or silently mis-round-trip) on."""
    return TranscriptRow(
        id=1, sender_id="s1", sender_name="Alice", ts=ts, body="hi",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )


async def test_save_then_recent_roundtrips(tag_trace_engine: Any) -> None:
    store = SqlTagTraceStore(tag_trace_engine)
    trace = TagTrace(
        tag_id="t1", conversation_id="c1",
        messages=({"role": "user", "content": [{"text": "hi"}]},),
        transcript=(), outcome="send_reply", created_at=NOW,
    )
    await store.save(trace)
    got = await store.recent(conversation_id="c1", limit=10)
    assert len(got) == 1
    assert got[0].tag_id == "t1"
    assert got[0].messages == trace.messages


async def test_transcript_round_trips_including_the_delivery_status_enum(
    tag_trace_engine: Any,
) -> None:
    """Serialising `transcript` with `row.__dict__` would, for a REAL
    `TranscriptRow`, carry a `DeliveryStatus` Enum member, not a JSON
    primitive. A store that writes that verbatim either fails the JSONB encode
    outright or (if it happens to succeed) hands back a bare string on read
    where the domain type promises an Enum. Either way,
    `got[0].transcript == trace.transcript` — a real equality check on the
    round-tripped dataclass, not just a length or id check — is what catches
    it."""
    store = SqlTagTraceStore(tag_trace_engine)
    row = _row()
    trace = TagTrace(
        tag_id="t2", conversation_id="c-transcript", messages=(),
        transcript=(row,), outcome="send_reply", created_at=NOW,
    )

    await store.save(trace)
    got = await store.recent(conversation_id="c-transcript", limit=10)

    assert len(got) == 1
    assert got[0].transcript == (row,)
    assert isinstance(got[0].transcript[0].delivery_status, DeliveryStatus)


async def test_an_edited_row_round_trips_with_its_edit(tag_trace_engine: Any) -> None:
    """An edited row carries a `RowEdit`, which is no more a JSON primitive than
    the Enum above. A tag whose window holds an edited message must still save
    (the save is spawned, so a failure would make the trace simply vanish), and
    a read must rebuild `edit` as a `RowEdit`, not a dict."""
    import dataclasses

    from nora.domain.models import RowEdit

    store = SqlTagTraceStore(tag_trace_engine)
    row = dataclasses.replace(
        _row(), body="dinner at 8",
        edit=RowEdit(ts=1_723_000_000_000, was="dinner at 7", raw_json='{"e": 1}'),
    )
    await store.save(TagTrace(
        tag_id="t-edit", conversation_id="c-edit", messages=(),
        transcript=(row,), outcome="send_reply", created_at=NOW,
    ))
    got = await store.recent(conversation_id="c-edit", limit=10)
    assert got[0].transcript == (row,)
    assert isinstance(got[0].transcript[0].edit, RowEdit)


async def test_recent_orders_newest_first(tag_trace_engine: Any) -> None:
    store = SqlTagTraceStore(tag_trace_engine)
    for i, ts in enumerate([1000, 3000, 2000]):
        await store.save(TagTrace(
            tag_id=f"t{i}", conversation_id="c-order", messages=(), transcript=(),
            outcome="silent", created_at=ts,
        ))
    got = await store.recent(conversation_id="c-order", limit=10)
    assert [t.created_at for t in got] == [3000, 2000, 1000]


async def test_recent_respects_the_conversation_boundary(tag_trace_engine: Any) -> None:
    store = SqlTagTraceStore(tag_trace_engine)
    await store.save(TagTrace(
        tag_id="t1", conversation_id="other", messages=(), transcript=(),
        outcome="silent", created_at=1000,
    ))
    got = await store.recent(conversation_id="c-boundary", limit=10)
    assert got == ()


async def test_recent_respects_the_limit(tag_trace_engine: Any) -> None:
    store = SqlTagTraceStore(tag_trace_engine)
    for i, ts in enumerate([1000, 2000, 3000]):
        await store.save(TagTrace(
            tag_id=f"t{i}", conversation_id="c-limit", messages=(), transcript=(),
            outcome="silent", created_at=ts,
        ))
    got = await store.recent(conversation_id="c-limit", limit=2)
    assert [t.created_at for t in got] == [3000, 2000]


async def test_recent_with_a_non_positive_limit_returns_nothing(
    tag_trace_engine: Any,
) -> None:
    """The same clamp `SqlFactStore.standing_facts`/`recent_facts` apply: a
    non-positive `limit` never reaches Postgres (which errors outright on a
    negative `LIMIT`), it returns `()`."""
    store = SqlTagTraceStore(tag_trace_engine)
    await store.save(TagTrace(
        tag_id="t1", conversation_id="c-zero", messages=(), transcript=(),
        outcome="silent", created_at=1000,
    ))
    assert await store.recent(conversation_id="c-zero", limit=0) == ()
    assert await store.recent(conversation_id="c-zero", limit=-1) == ()


async def test_an_invalid_outcome_is_refused_before_any_write(
    tag_trace_engine: Any,
) -> None:
    """`TagTrace.__post_init__` already refuses this — asserted here
    only to confirm the store has no path that reaches the database with a
    domain object that should never have been constructible, the same
    build-before-insert discipline `SqlNoteStore.create` documents."""
    with pytest.raises(ValueError, match="outcome"):
        TagTrace(
            tag_id="t1", conversation_id="c-bad", messages=(), transcript=(),
            outcome="not-a-real-outcome", created_at=1000,
        )
