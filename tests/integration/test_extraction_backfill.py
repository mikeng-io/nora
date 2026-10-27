"""Synthetic history through the real queue, policy and unchanged snapshot builder."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.app.composition import _note_unit_of
from nora.domain.extraction import validate_claims
from nora.domain.models import DeliveryStatus
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.extraction import SqlExtraction, admission
from nora.infra.sql.schema import EXTRACTION as BATCH
from nora.infra.sql.schema import EXTRACTION_ATTEMPT as ATTEMPT
from nora.infra.sql.schema import EXTRACTION_CLAIM as CLAIM
from nora.infra.sql.schema import EXTRACTION_QUEUE as QUEUE
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGES
from nora.policy import load_policy
from scripts.extraction_backfill import backfill, status
from tests.conftest import _async_url
from tests.contract.test_extraction_contract import finish, rows, take
from tests.contract.test_extraction_contract import message as source_message
from tests.unit.test_extraction import ASSERTED, claim, parsed

HISTORY = ASSERTED - 10 * 365 * 86400000
BEFORE = HISTORY + 1000


def message(n=1, **kw):
    return replace(source_message(n, **kw), timestamp=HISTORY + n)


@pytest.fixture(scope="module")
def history_db(pg_admin, pg_template):
    name = "t_history_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    try:
        yield _async_url(pg_admin, name)
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                         "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            conn.execute(f'DROP DATABASE "{name}"')


@pytest.fixture
async def db(history_db):
    engine = create_sql_engine(history_db)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("TRUNCATE messages, extraction CASCADE"))
        await engine.dispose()


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text('''default = "deny"
[[conversations]]
platform = "signal"
id = "room-a"
label = "Synthetic A"
also_ids = ["old-room-a"]
unit = "unit-current"
persons = ["synthetic-person"]
[[conversations]]
platform = "signal"
id = "room-b"
label = "Synthetic B"
unit = "unit-other"
persons = ["synthetic-person"]
''')
    return load_policy(path)


async def test_dry_run_is_read_only_then_enqueue_is_idempotent(db, policy):
    record = SqlRecordStore(db)
    source = await record.append(message=replace(message(), message_id=""))
    # Includes every table the command could alter, including the row-history trigger.
    tables = [MESSAGES, QUEUE, BATCH, ATTEMPT, CLAIM]
    before = [await rows(db, table) for table in tables]
    preview = await backfill(db, policy, before_ms=BEFORE, dry_run=True)
    assert preview["eligible"] == preview["would_enqueue"] == 1
    assert preview["enqueued"] == 0 and preview["skipped"] == {}
    assert before == [await rows(db, table) for table in tables]
    applied = await backfill(db, policy, before_ms=BEFORE)
    assert applied["enqueued"] == 1
    again = await backfill(db, policy, before_ms=BEFORE)
    assert again["already_queued"] == 1 and again["would_enqueue"] == again["enqueued"] == 0
    queue = await rows(db, QUEUE)
    assert len(queue) == 1 and queue[0]["message_id"] == source and queue[0]["historical"]
    # A genuine concurrent second operator cannot beat either uniqueness constraint.
    async with db.begin() as conn:
        await conn.execute(sa.delete(QUEUE))
    runs = await asyncio.gather(*(backfill(db, policy, before_ms=BEFORE) for _ in range(2)))
    assert sum(r["enqueued"] for r in runs) == 1
    assert len(await rows(db, QUEUE)) == 1


async def test_history_and_live_share_admission_and_current_ownership(db, policy):
    historical = SqlRecordStore(db)
    live = SqlRecordStore(db, extraction_unit=_note_unit_of(policy))
    wanted = []
    for n, room in [(1, "old-room-a"), (2, "room-b")]:
        wanted.append(await historical.append(message=message(n, room=room)))
        # Unattributed humans are eligible: identity/category filters would lose evidence.
        await live.append(message=message(n + 20, room=room))
    nora = await historical.append(message=message(3), is_nora=True)
    hidden = await historical.append(message=message(4))
    deleted = await historical.append(message=message(5))
    await historical.append(message=message(6, room="unmapped"))
    await historical.append(message=message(7, platform="discord"))
    # Both edges are outside the half-open 61-day window.
    await historical.append(message=replace(message(8), timestamp=BEFORE))
    await historical.append(message=replace(message(9), timestamp=HISTORY - 62 * 86400000))
    async with db.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.id == hidden).values(hidden_at=1))
        await conn.execute(sa.insert(MESSAGE_DELETE).values(
            message_id=deleted, ts=ASSERTED + 100, sender_id="synthetic-sender", raw_json="{}",
        ))
    result = await backfill(db, policy, before_ms=BEFORE)
    assert result["eligible"] == 4 and result["already_queued"] == result["enqueued"] == 2
    assert result["context_eligible"] == result["context_enqueued"] == 1
    assert result["skipped"] == {"hidden": 1, "deleted": 1, "unmapped": 2}
    queue = await rows(db, QUEUE)
    nora_row = next(r for r in queue if r["message_id"] == nora)
    assert nora_row["historical"] and not nora_row["is_source"]
    for source, unit in zip(wanted, ["unit-current", "unit-other"], strict=True):
        assert next(r for r in queue if r["message_id"] == source)["unit"] == unit
    assert {r["unit"] for r in queue if not r["historical"]} == {"unit-current", "unit-other"}
    # The live path calls this exact helper; each rejected stored shape stays rejected.
    for changes, reason in [({"hidden_at": 1}, "hidden"), ({"deleted": True}, "deleted"),
                            ({"conversation_id": "unmapped"}, "unmapped")]:
        row = {"platform": "signal", "conversation_id": "room-a", "is_nora": False, **changes}
        assert admission(row, _note_unit_of(policy)) == (None, reason)


async def test_live_arrival_precedes_history_even_in_the_same_room(db, policy):
    record = SqlRecordStore(db)
    # Arrival ids deliberately disagree with event chronology.
    newer = await record.append(message=message(3), person_id="subject-a")
    older = await record.append(message=message(1), person_id="subject-a")
    assert (await backfill(db, policy, before_ms=BEFORE))["enqueued"] == 2
    live_record = SqlRecordStore(db, extraction_unit=_note_unit_of(policy))
    live = await live_record.append(message=message(30), person_id="subject-a")
    store = SqlExtraction(db)
    first = await take(store, messages_per_call=1)
    assert first["source_ids"] == [live]
    assert len([r for r in await rows(db, QUEUE) if r["extraction_id"] is None]) == 2
    assert await finish(store, first) == 1
    second = await take(store, messages_per_call=1)
    assert second["source_ids"] == [older]
    assert await finish(store, second) == 1
    assert (await take(store))["source_ids"] == [newer]


async def test_live_arrival_precedes_a_due_historical_retry(db, policy):
    await SqlRecordStore(db).append(message=message(), person_id="subject-a")
    await backfill(db, policy, before_ms=BEFORE)
    store = SqlExtraction(db)
    historical = await take(store)
    await store.fail(historical, stage="request")
    async with db.begin() as conn:
        await conn.execute(sa.update(BATCH).values(due_at=sa.func.now()))
    live_record = SqlRecordStore(db, extraction_unit=_note_unit_of(policy))
    live = await live_record.append(message=message(30), person_id="subject-a")
    first = await take(store)
    assert first["source_ids"] == [live]
    assert await finish(store, first) == 1
    resumed = await take(store)
    assert resumed["id"] == historical["id"] and resumed["attempts"] == 2
    assert await finish(store, resumed) == 1


async def test_status_lists_failed_stages_and_requeue_to_checkable_done(db, policy):
    record = SqlRecordStore(db)
    for n in range(4):
        await record.append(message=message(n), person_id="subject-a")
    await backfill(db, policy, before_ms=BEFORE)
    live_record = SqlRecordStore(db, extraction_unit=_note_unit_of(policy))
    await live_record.append(message=message(100), person_id="subject-a")
    store = SqlExtraction(db)
    assert await finish(store, await take(store)) == 1
    done = await take(store, messages_per_call=1)
    assert await finish(store, done) == 1
    failed = await take(store, messages_per_call=1)
    # Use the real attempt allowance and failure writer, without a model.
    for attempt in range(1, 6):
        await store.fail(failed, stage="schema" if attempt < 5 else "claims")
        async with db.begin() as conn:
            await conn.execute(sa.update(BATCH).where(BATCH.c.id == failed["id"])
                               .values(due_at=sa.func.now()))
        if attempt < 5:
            failed = await take(store, messages_per_call=1)
    pending = await take(store, messages_per_call=1)
    report = await status(db)
    assert report["rows"] == {"unbatched": 1, "pending": 1, "done": 1, "failed": 1, "retired": 0}
    assert report["claims_written"] == 1
    assert report["failed_batches"] == [
        {"id": failed["id"], "failure_stages": {"schema": 4, "claims": 1}},
    ]
    # Pre-migration attempts or exhausted leases cannot be assigned a guessed stage.
    async with db.begin() as conn:
        await conn.execute(sa.update(ATTEMPT).where(ATTEMPT.c.id == failed["attempt_id"])
                           .values(failure_stage=None))
    assert (await status(db))["failed_batches"][0]["failure_stages"] == {"schema": 4, "unknown": 1}
    assert await finish(store, pending) == 1
    assert await store.requeue(failed["id"])
    while job := await take(store):
        assert await finish(store, job) > 0
    report = await status(db)
    assert report["rows"] == {"unbatched": 0, "pending": 0, "done": 4, "failed": 0, "retired": 0}
    assert report["claims_written"] == 4 and report["failed_batches"] == []
    # Status prints structural data only, never message or claim text.
    assert "Workshop" not in json.dumps(report)


async def test_historical_nora_reply_is_idempotent_uncitable_context(db, policy):
    record = SqlRecordStore(db)
    first = await record.append(message=message(1), person_id="subject-a")
    nora = await record.append(
        message=message(2), is_nora=True, delivery_status=DeliveryStatus.SENT,
    )
    last = await record.append(message=message(3), person_id="subject-a")
    preview = await backfill(db, policy, before_ms=BEFORE, dry_run=True)
    assert preview["would_enqueue"] == 2 and preview["context_would_enqueue"] == 1
    assert preview["context_enqueued"] == 0
    applied = await backfill(db, policy, before_ms=BEFORE)
    assert applied["enqueued"] == 2 and applied["context_enqueued"] == 1
    again = await backfill(db, policy, before_ms=BEFORE)
    assert again["already_queued"] == 2 and again["context_already_queued"] == 1
    assert again["enqueued"] == again["context_enqueued"] == 0
    queue = await rows(db, QUEUE)
    assert len(queue) == 3 and all(r["historical"] for r in queue)
    assert {r["message_id"] for r in queue if r["is_source"]} == {first, last}
    store = SqlExtraction(db)
    assert await finish(store, await take(store, messages_per_call=1)) == 1
    job = await take(store)
    assert job["source_ids"] == [last]
    context = next(r for r in job["snapshot"]["context"] if r["id"] == nora)
    assert context["is_nora"] and context["body"] == "tomorrow visit Workshop"
    with pytest.raises(ValueError, match="citation outside batch"):
        validate_claims(parsed({"claims": [claim(nora)]}), job["snapshot"])
    assert await finish(store, job) == 1
    assert await take(store) is None
    report = await status(db)
    assert report["rows"] == {"unbatched": 0, "pending": 0, "done": 2, "failed": 0, "retired": 0}
    assert report["context_rows"] == {
        "unbatched": 1, "pending": 0, "done": 0, "failed": 0, "retired": 0,
    }
    assert report["claims_written"] == 2


async def test_enqueue_commits_each_chunk_without_candidate_share_locks(db, policy, monkeypatch):
    from scripts import extraction_backfill as command

    record = SqlRecordStore(db)
    for n in range(5):
        await record.append(message=message(n))
    monkeypatch.setattr(command, "CHUNK_SIZE", 2)
    enqueue = command.enqueue
    observed = []
    statements = []

    async def observe(conn, **kwargs):
        # An independent reader must see earlier chunks before this INSERT.
        observed.append(len(await rows(db, QUEUE)))
        return await enqueue(conn, **kwargs)

    def sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(command, "enqueue", observe)
    sa.event.listen(db.sync_engine, "before_cursor_execute", sql)
    try:
        result = await backfill(db, policy, before_ms=BEFORE)
    finally:
        sa.event.remove(db.sync_engine, "before_cursor_execute", sql)
    assert result["enqueued"] == 5
    assert observed == [0, 0, 2, 2, 4]
    assert not any("FOR SHARE" in statement for statement in statements)
