"""The receipt/Record writer contract, shared by SQL and the in-memory double."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.memory_graph import Admission, Evidence, Relation
from nora.infra.sql.comprehend import SqlComprehendRecords
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    COMPREHEND_RESULT,
    COMPREHEND_WEB_PAGE,
    EXTRACTION,
    EXTRACTION_CLAIM,
)
from nora.testing.comprehend import InMemoryComprehendRecords
from tests.conftest import _async_url
from tests.unit.test_comprehend import ITEM, PERSON, PLACE, SOURCE, UNIT, WORK


@pytest.fixture(scope="module")
def database(pg_admin, pg_template):
    name = "comprehend_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (pg_template,),
        )
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    yield _async_url(pg_admin, name)
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE "{name}"')


@pytest.fixture(params=["fake", "sql"])
async def store(request, database):
    if request.param == "fake":
        yield InMemoryComprehendRecords(), None
        return
    engine = create_sql_engine(database)
    # Schema is module-scoped; isolate rows, not schemas.
    async with engine.begin() as conn:
        # History tables refuse TRUNCATE; DELETE retains their audit history.
        await conn.execute(sa.delete(COMPREHEND_RESULT))
        await conn.execute(sa.delete(COMPREHEND_WEB_PAGE))
        await conn.execute(sa.delete(EXTRACTION_CLAIM))
        await conn.execute(sa.delete(EXTRACTION))
    yield (
        SqlComprehendRecords(engine, media_join=None, rooms_by_unit={}, persons_by_unit={}),
        engine,
    )
    await engine.dispose()


async def test_retained_page_is_exact_read_text_with_hash_and_tool_call(store):
    records, engine = store
    text = "Generated public venue page"
    call = {
        "name": "web_fetch",
        "toolUseId": "generated-call",
        "input": {"url": "https://example.invalid"},
    }
    key = await records.web_page(
        unit=UNIT,
        session_id=1,
        url="https://example.invalid",
        fetched_at=datetime.now(UTC),
        text=text,
        tool_call=call,
    )
    assert key[0] == "web_page"
    if engine:
        async with engine.connect() as conn:
            page = (await conn.execute(sa.select(COMPREHEND_WEB_PAGE))).mappings().one()
    else:
        page = records.pages[0]
    import hashlib

    assert page["text"] == text and page["tool_call"] == call
    assert page["content_hash"] == hashlib.sha256(text.encode()).hexdigest()


async def test_graph_receipt_is_retained_and_shadow_is_a_would_write(store):
    records, engine = store
    relation = Relation(
        "generated-relation",
        PERSON.key,
        "likes",
        PLACE.key,
        "notebook",
        UNIT,
        asserted_at=WORK.as_of,
    )
    admission = Admission(
        relations=(relation,), evidence=(Evidence(SOURCE.key, relation_id=relation.id),)
    )
    await records.graph_write(WORK, 1, admission)
    await records.shadow(WORK, 2, PERSON.key, [{"part": "trait", "text": "Generated trait"}], 1)
    if engine:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(COMPREHEND_RESULT.c.payload).where(
                        COMPREHEND_RESULT.c.part == "reshape"
                    )
                )
            ).scalar_one()
    else:
        row = records.shadows[0]
    assert row["would_writes"][0]["text"] == "Generated trait"


async def test_reading_writer_is_idempotent_per_work_hash(store):
    records, engine = store
    claims = [{"what": "Generated claim", "source_ids": [1]}]
    await records.readings(WORK, 1, ITEM, claims)
    await records.readings(WORK, 2, ITEM, claims)
    if engine:
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.select(EXTRACTION_CLAIM.c.content))).scalars().all()
        assert len(rows) == 1
        assert rows[0]["writer"] == "cognition:comprehend"
    else:
        assert len(records.claims) == 1


async def test_refusals_keep_reason_without_private_content(store):
    records, engine = store
    await records.record_refusal(1, "relation", "unseen_source")
    if engine:
        async with engine.connect() as conn:
            row = (await conn.execute(sa.select(COMPREHEND_RESULT.c.payload))).scalar_one()
        assert row == {"part": "relation", "reason": "unseen_source"}
    else:
        assert records.refusals == [(1, "relation", "unseen_source")]


async def test_load_missing_message_projection_raises_no_result_found(store):
    """An exact `("message", <id>)` source with no projection row is
    `NoResultFound` in BOTH implementations — the wake's grounded-text
    fallback (`nora.app.cognition_memory`) catches exactly that, so the fake
    cannot silently return a different failure shape than SQL. The SQL
    positive control is `test_sql_source_reader_joins_...` above."""
    records, engine = store
    from sqlalchemy.exc import NoResultFound

    from nora.domain.memory_graph import WorkItem

    work = WorkItem(
        "generated-missing-work", ("message", "404042042"), UNIT,
        "generated-hash", WORK.as_of,
    )
    with pytest.raises(NoResultFound):
        await records.load(work)
    if engine is None:
        # The fake distinguishes absent from present rather than always
        # raising: seed the key and the same call returns the item.
        records.items[work.source] = ITEM
        assert (await records.load(work)).work == work


async def test_sql_source_reader_joins_the_reply_parent_and_refuses_another_unit(store):
    records, engine = store
    if engine is None:
        # The fake's source inventory is explicit; SQL's projection join is
        # separately exercised here rather than simulated by a constant answer.
        return
    from dataclasses import replace

    from nora.domain.memory_graph import WorkItem
    from nora.domain.models import NormalizedMessage, Quote
    from nora.infra.sql.audit import SqlRecordStore

    async def enrich(rows, **kw):
        return tuple(rows)

    records.media_join = SimpleNamespace(enrich=enrich)
    records.rooms_by_unit = {UNIT: [("signal", "generated-room")]}
    record = SqlRecordStore(engine)
    msg = NormalizedMessage(
        "signal",
        "generated-room",
        "group",
        "generated-sender",
        "Generated Speaker",
        1000,
        "Generated parent",
        {},
    )
    parent = await record.append(message=msg, person_id="generated-person")
    child = await record.append(
        message=replace(
            msg,
            timestamp=2000,
            body="Generated reply",
            quote=Quote(quoted_ts=1000, author_id="generated-sender", text="Untrusted fake parent"),
        ),
        person_id="generated-person",
    )
    work = WorkItem(
        "sql-generated-work", ("message", str(child)), UNIT, "generated-hash", WORK.as_of
    )
    item = await records.load(work)
    assert item.reply_parent == ("message", str(parent))
    assert [s.text for s in item.sources] == ["Generated reply", "Generated parent"]
    assert "Untrusted fake parent" not in item.text
    assert item.snapshot["messages"][0]["reply_to"] == parent
    import json
    batch = await records.load(replace(work, source=("message_batch", json.dumps([parent, child]))))
    assert {r["id"] for r in batch.snapshot["messages"]} == {parent, child}
    assert {s.key for s in batch.sources} == {("message", str(parent)), ("message", str(child))}
    with pytest.raises(ValueError, match="unscoped source"):
        await records.load(replace(work, unit="another-unit"))


async def test_historical_source_rows_do_not_read_a_future_edit(store):
    _, engine = store
    if engine is None:
        return
    from nora.domain.models import NormalizedMessage
    from nora.infra.sql.audit import SqlRecordStore
    from nora.infra.sql.extraction import source_rows
    from nora.infra.sql.schema import MESSAGE_EDIT

    record = SqlRecordStore(engine)
    mid = await record.append(
        message=NormalizedMessage(
            "signal",
            "clock-room",
            "group",
            "generated-sender",
            "Generated Speaker",
            1000,
            "Original generated text",
            {},
        ),
        person_id="generated-person",
    )
    async with engine.begin() as conn:
        await conn.execute(
            sa.insert(MESSAGE_EDIT).values(message_id=mid, ts=3000, body="Later generated text")
        )
        old = await source_rows(conn, [mid], as_of_ms=2000)
        new = await source_rows(conn, [mid], as_of_ms=4000)
    assert old[0]["body"] == "Original generated text"
    assert new[0]["body"] == "Later generated text"
    assert new[0]["ts"] == 3000
    assert old[0]["ts"] == 1000
    from nora.infra.sql.extraction import link_replies
    reply = {"id": 999, "ts": 3500, "quoted_ts": 1000}
    link_replies([reply], new)
    assert reply["reply_to"] == mid


async def test_stimulus_failures_park_at_five_and_operator_requeue_resets(store):
    records, _ = store
    for n in range(5):
        assert await records.failures(UNIT, 17) == n
        await records.fail_stimulus(UNIT, 17, 0)
    assert await records.failures(UNIT, 17) == 5
    assert await records.failures(UNIT, 18) == 0
    assert await records.failures(UNIT, 17, reset=True) == 0


async def test_register_retry_retains_existing_row_id(store):
    records, _ = store
    assert await records.act_result("act:generated", 0) is None
    result = {"status": "ok", "note_id": "generated-existing-note"}
    await records.act_result("act:generated", 0, result)
    assert await records.act_result("act:generated", 1) == result
