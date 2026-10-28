"""Real fault, plan and source-write proofs for the graph store."""

import json
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from nora.domain.memory_graph import Walk, pointer_entity
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.graph_projection import ProjectionContext, form_batch
from nora.infra.sql.memory_graph import WALK_SQL, SqlMemoryGraph, walk_parameters
from nora.infra.sql.schema import MEMORY_GRAPH_REFUSAL, MEMORY_WORK_ITEM, RELATION
from nora.obs.metrics import Metrics
from tests.contract.test_memory_graph_contract import PERSON, load
from tests.contract.test_memory_graph_contract import _graph_db as _graph_db

BASE_MS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)

@pytest.fixture
async def sql(_graph_db):
    engine = create_sql_engine(_graph_db)
    async with engine.begin() as conn:
        await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
        await conn.execute(
            sa.text(
                "TRUNCATE relation_evidence, relation, entity, memory_work_item, "
                "graph_refusal, graph_arrival, messages, memory_node, memory_edge, "
                "memory_projection_queue, rollup_build, rollup_colocation, rollup_outing, "
                "rollup_response, presence_stays, standing_decision, standing_decision_revision, "
                "reminders RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield SqlMemoryGraph(engine, metrics=Metrics())
    finally:
        await engine.dispose()


async def test_real_database_fault_is_unavailable_and_recovers(sql):
    await load(sql)
    query = Walk("u1", frozenset({"p1"}))
    assert (await sql.walk((PERSON.key,), query)).nodes
    async with sql.engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE relation RENAME TO faulted_relation"))
    try:
        failed = await sql.walk((PERSON.key,), query)
        assert failed.status == "unavailable"
        assert not failed.nodes and not failed.relations
    finally:
        async with sql.engine.begin() as conn:
            await conn.execute(sa.text("ALTER TABLE faulted_relation RENAME TO relation"))
    assert (await sql.walk((PERSON.key,), query)).nodes


async def test_walk_plan_uses_both_relation_endpoint_indexes(sql):
    async with sql.engine.begin() as conn:
        await conn.execute(
            sa.text("""
          INSERT INTO relation(id,src_kind,src_id,rel,dst_kind,dst_id,layer,
                               asserted_at,written_by,unit)
          SELECT 'generated-'||n,'event',n::text,'at','place',(n+1)::text,
                 'notebook',now(),'generated','u1' FROM generate_series(1,10000) n
        """)
        )
        await conn.execute(sa.text("ANALYZE relation"))
        plan = (
            await conn.execute(
                sa.text("EXPLAIN (FORMAT JSON) " + WALK_SQL),
                walk_parameters((("event", "5000"),), Walk("u1", frozenset())),
            )
        ).scalar_one()
    rendered = json.dumps(plan)
    assert "idx_relation_src_walk" in rendered
    assert "idx_relation_dst_walk" in rendered
    assert "Index Scan" in rendered or "Bitmap Index Scan" in rendered
    result = await sql.walk((("event", "5000"),), Walk("u1", frozenset()))
    assert result.nodes and result.relations


async def test_projection_toggle_batching_pointer_identity_and_backfill(sql):
    from nora.app.graph_project import graph_project

    engine = sql.engine
    store = SqlRecordStore(engine)

    def message(i):
        return NormalizedMessage(
            platform="signal",
            conversation_id="generated-room",
            conversation_type="group",
            sender_id="generated-sender",
            sender_name="Generated Person",
            timestamp=BASE_MS + i,
            body="Generated text",
            raw={},
        )

    first = await store.append(message=message(0))
    async with engine.begin() as conn:
        assert not (await conn.execute(sa.select(RELATION))).all()
    scope = ProjectionContext(
        {"signal:generated-room": "generated-unit"},
        {"generated-unit": frozenset({"generated-person"})},
    )
    engine.update_execution_options(memory_graph=scope)
    for i in range(1, 24):
        row = await store.append(message=message(i))
        await store.set_person_id(row_id=row, person_id="generated-person")
    await store.append_edit(message_id=row, message=message(24))
    async with engine.begin() as conn:
        count = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(RELATION)
                .where(RELATION.c.rel == "authored_by", RELATION.c.status == "current")
            )
        ).scalar_one()
        assert count == 23
        pointer = pointer_entity("person", "generated-unit", "generated-person", "person")
        assert (
            await conn.execute(
                sa.select(RELATION.c.dst_id)
                .where(RELATION.c.rel == "authored_by", RELATION.c.status == "current")
                .limit(1)
            )
        ).scalar_one() == pointer.id
        now = datetime.now(UTC) + timedelta(seconds=3)
        assert await form_batch(conn, now=now) == 20
        assert await form_batch(conn, now=now) == 3
        assert await form_batch(conn, now=now) == 0
        batches = (await conn.execute(sa.select(MEMORY_WORK_ITEM))).mappings().all()
        assert len(batches) == 2
        assert all(r["state"] == "pending" for r in batches)
    before = await graph_project(engine, scope=scope)
    assert before["applied"] is False
    async with engine.begin() as conn:
        assert not (
            await conn.execute(
                sa.select(RELATION.c.id).where(
                    RELATION.c.src_id == str(first),
                    RELATION.c.rel == "in_room",
                    RELATION.c.status == "current",
                )
            )
        ).first()
    applied = await graph_project(engine, scope=scope, apply=True, rows_per_second=100000)
    again = await graph_project(engine, scope=scope, apply=True, rows_per_second=100000)
    assert applied["relations"] == again["relations"]
    assert applied["sources"]["message"] == 24
    async with engine.begin() as conn:
        assert (
            await conn.execute(sa.select(sa.func.count()).select_from(MEMORY_WORK_ITEM))
        ).scalar_one() == 2
        assert (
            await conn.execute(
                sa.select(RELATION.c.id).where(
                    RELATION.c.src_id == str(first),
                    RELATION.c.rel == "in_room",
                    RELATION.c.status == "current",
                )
            )
        ).first()


async def test_refused_parts_are_durable(sql):
    from dataclasses import replace

    from tests.contract.test_memory_graph_contract import EV, EVENT, REL, SOURCE

    admitted = await sql.stage_and_admit(
        entities=(PERSON, EVENT),
        relations=(REL, replace(REL, id="bad", rel="invented")),
        evidence=(EV,),
        sources={SOURCE.key: SOURCE},
    )
    assert admitted.relations == (REL,)
    async with sql.engine.begin() as conn:
        rows = (await conn.execute(sa.select(MEMORY_GRAPH_REFUSAL))).mappings().all()
    assert any(r["part_id"] == "bad" and r["reason"] == "vocabulary" for r in rows)


async def test_projection_failure_keeps_source_and_requeues(sql):
    from nora.infra.sql import memory_index
    from nora.infra.sql.schema import MEMORY_PROJECTION_QUEUE, MESSAGES

    sql.engine.update_execution_options(
        memory_graph=ProjectionContext(
            {"signal:generated-room": "generated-unit"}, {"generated-unit": frozenset()}
        )
    )
    store = SqlRecordStore(sql.engine, metrics=sql.metrics)
    async with sql.engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE relation RENAME TO faulted_relation"))
    try:
        mid = await store.append(
            message=NormalizedMessage(
                "signal",
                "generated-room",
                "group",
                "generated-sender",
                "Generated",
                BASE_MS,
                "Generated source",
                {},
            )
        )
        async with sql.engine.begin() as conn:
            assert (
                await conn.execute(sa.select(MESSAGES.c.id).where(MESSAGES.c.id == mid))
            ).scalar_one() == mid
            assert (
                await conn.execute(sa.select(MEMORY_PROJECTION_QUEUE.c.source_id))
            ).scalar_one() == str(mid)
        assert (
            sql.metrics.get("nora_memory_projection_total", kind="message", outcome="queued") == 1
        )
    finally:
        async with sql.engine.begin() as conn:
            await conn.execute(sa.text("ALTER TABLE faulted_relation RENAME TO relation"))
    async with sql.engine.begin() as conn:
        assert await memory_index.drain_queue(conn) == 1
        assert not (await conn.execute(sa.select(MEMORY_PROJECTION_QUEUE))).all()
        assert (
            await conn.execute(sa.select(RELATION.c.id).where(RELATION.c.rel == "in_room"))
        ).first()


async def test_social_and_late_media_produce_pending_work(sql):
    from nora.domain.media import MediaBlob
    from nora.domain.social_stream import MediaItem, SocialItem
    from nora.infra.sql.media_objects import SqlMediaObjectStore
    from nora.infra.sql.social_stream import SqlSocialStreamStore

    sql.engine.update_execution_options(
        memory_graph=ProjectionContext(
            {"signal:generated-room": "generated-unit"},
            {"generated-unit": frozenset({"generated-person"})},
        )
    )
    sha = "de" * 32
    await SqlRecordStore(sql.engine).append(
        message=NormalizedMessage(
            "signal",
            "generated-room",
            "group",
            "generated-sender",
            "Generated",
            BASE_MS,
            "Generated photo",
            {},
            media_blobs=(MediaBlob(sha256=sha, media_type="image/jpeg", size=10),),
        )
    )
    media = SqlMediaObjectStore(sql.engine)
    assert await media.comprehend(
        sha256=sha,
        title="Generated title",
        description="Generated reading",
        content="",
        model="generated-model",
    )
    social = SqlSocialStreamStore(sql.engine)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    item = SocialItem(
        network="instagram",
        item_id="generated-item",
        account="generated-account",
        kind="post",
        taken_at=now,
        expires_at=None,
        caption="Generated caption",
        alt_text=None,
        media=(MediaItem("image", "https://example.invalid/generated"),),
    )
    assert await social.insert_item(
        item, person="generated-person", origin="poll", seen_at=now, media_ref=sha
    )
    async with sql.engine.begin() as conn:
        work = (await conn.execute(sa.select(MEMORY_WORK_ITEM))).mappings().all()
        assert {r["source_kind"] for r in work} == {"media", "social"}
        assert all(r["state"] == "pending" for r in work)
        edges = (
            (await conn.execute(sa.select(RELATION.c.rel).where(RELATION.c.status == "current")))
            .scalars()
            .all()
        )
        assert {"authored_by", "in_room", "attached_to", "shared_by"} <= set(edges)


async def test_rollup_projection_and_closed_episode_work_are_atomic(sql):
    from nora.domain.rollup import episode_key
    from nora.infra.sql import memory_index
    from nora.infra.sql.schema import (
        PRESENCE_STAYS,
        ROLLUP_BUILD,
        ROLLUP_COLOCATION,
        ROLLUP_OUTING,
        ROLLUP_RESPONSE,
    )

    unit = "generated-unit"
    persons = ("generated-person-a", "generated-person-b")
    sql.engine.update_execution_options(
        memory_graph=ProjectionContext({"signal:generated-room": unit}, {unit: frozenset(persons)})
    )
    start = BASE_MS
    await SqlRecordStore(sql.engine).append(
        message=NormalizedMessage(
            "signal",
            "generated-room",
            "group",
            "generated-sender",
            "Generated",
            start + 10,
            "Generated source",
            {},
        ),
        person_id=persons[0],
    )
    async with sql.engine.begin() as conn:
        bid = (
            await conn.execute(
                sa.insert(ROLLUP_BUILD)
                .values(code_version="generated", params_sha256="generated", status="current")
                .returning(ROLLUP_BUILD.c.id)
            )
        ).scalar_one()
        common = {
            "build_id": bid,
            "unit": unit,
            "persons": persons,
            "lineage": [],
            "valid_from_ms": start,
            "valid_to_ms": start + 100,
            "as_of_ms": start + 100,
            "lat": float(0),
            "lng": float(0),
        }
        await conn.execute(sa.insert(ROLLUP_COLOCATION).values(**common, radius_m=10, stay_count=2))
        await conn.execute(
            sa.insert(ROLLUP_OUTING).values(
                **common, district="Generated District", city="Generated City"
            )
        )
        await conn.execute(
            sa.insert(PRESENCE_STAYS).values(
                build_id=bid,
                subject_id=persons[0],
                person_id=persons[0],
                circle_id="generated-circle",
                started_ts=start,
                ended_ts=start + 100,
                lat=float(0),
                lng=float(0),
                radius_m=10,
                fix_count=2,
                address="Generated Place",
                lineage=[],
            )
        )
        await conn.execute(
            sa.insert(ROLLUP_RESPONSE).values(
                build_id=bid,
                unit=unit,
                lineage=[],
                valid_from_ms=start,
                valid_to_ms=start + 100,
                as_of_ms=start + 100,
                message_id=123,
                platform="signal",
                conversation_id="generated-room",
                sent_ts=start,
                replies=1,
                reactions=0,
                edits=0,
                named_persons=persons,
                outcome="responded",
            )
        )
        key = episode_key(f"{unit}|{','.join(persons)}", start, start + 100)
        await memory_index.project_episodes(conn, bid, "outing", [key])
        edges = (
            (await conn.execute(sa.select(RELATION.c.rel).where(RELATION.c.status == "current")))
            .scalars()
            .all()
        )
        assert {"located_in", "during", "stayed_at", "co_located_with", "outcome_of"} <= set(edges)
        work = (await conn.execute(sa.select(MEMORY_WORK_ITEM.c.source_kind))).scalars().all()
        assert {"outing", "co_location", "episode"} <= set(work)


async def test_global_ids_cannot_conflate_entity_kinds(sql):
    from nora.domain.memory_graph import Entity

    first = Entity("generated-collision", "place", "generated-unit", "Generated Place")
    await sql.upsert_entity(first)
    with pytest.raises(ValueError, match="kind"):
        await sql.upsert_entity(Entity(first.id, "work", first.unit, "Generated Work"))
    assert await sql.candidates(unit=first.unit, kind="place", name=first.name) == (first,)


async def test_register_writers_enqueue_their_own_committed_revisions(sql):
    from uuid import uuid4

    from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
    from nora.domain.note_body import Shown, ShownMessage
    from nora.domain.reminders import Reminder, ReminderState
    from nora.domain.standing_ledger import LedgerOp
    from nora.infra.sql.reminders import SqlReminderStore
    from nora.infra.sql.standing_ledger import SqlStandingLedger

    unit = "generated-unit"
    room = "generated-room"
    persons = frozenset({"generated-person"})
    sql.engine.update_execution_options(
        memory_graph=ProjectionContext(
            {f"signal:{room}": unit}, {unit: persons}, metrics=sql.metrics
        )
    )
    mid = await SqlRecordStore(sql.engine).append(
        message=NormalizedMessage(
            "signal",
            room,
            "group",
            "generated-sender",
            "Generated",
            BASE_MS,
            "Generated source",
            {},
        ),
        person_id="generated-person",
    )
    facts = RoomFacts(
        "signal", frozenset({room}), unit, persons, primary_id=room, label="Generated"
    )
    target = RenderRoom(
        facts, rooms=(facts,), live=LiveRoom(persons, unnamed=0), mode="off", content_checks=False
    )
    op = LedgerOp(
        "open",
        "decision",
        subject="Generated choice",
        scope="unit",
        sections={"Options": f"Generated option [{mid}]", "Leaning": "none yet"},
    )
    result = await SqlStandingLedger(sql.engine, metrics=sql.metrics).apply(
        op,
        target=target,
        shown=Shown(rendered=(ShownMessage(id=mid, sender="Generated"),)),
        now_ms=(BASE_MS + 10),
    )
    assert result.status == "ok", result.refusal
    now = datetime(2026, 1, 1, tzinfo=UTC)
    reminder = Reminder(
        id=uuid4(),
        requester_person_id="generated-person",
        platform="signal",
        conversation_id=room,
        conversation_type="group",
        text="Generated reminder",
        created_at=now,
        due_at=now + timedelta(hours=1),
        state=ReminderState.PENDING,
        fired_at=None,
        outcome=None,
    )
    reminders = SqlReminderStore(sql.engine)
    await reminders.create(reminder)
    assert await reminders.cancel(reminder.id, conversation_id=room)
    async with sql.engine.begin() as conn:
        work = (await conn.execute(sa.select(MEMORY_WORK_ITEM))).mappings().all()
    assert [r["source_kind"] for r in work].count("decision") == 1
    assert [r["source_kind"] for r in work].count("reminder") == 2
    assert all(r["state"] == "pending" for r in work)
