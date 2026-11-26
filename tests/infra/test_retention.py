"""Expiry preserves graph endpoints and records gaps in source coverage."""

# ruff: noqa: F811 - pytest resolves the imported engine fixture.

import asyncio
import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from nora.analytics.units import UnitConfig
from nora.config import ConfigError, load
from nora.domain.fetched_page import FetchedPage
from nora.domain.observations import Observation, PollPayload, SourceScope
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import (
    CLAIM,
    EDGE,
    EXPIRED_COVERAGE,
    NORA_PASS_OUTPUT,
    REGISTRY,
)
from nora.infra.sql.cognitive_graph_store import _SOURCE_CONTENT, SqlCognitiveGraph
from nora.infra.sql.fetched_pages import SqlFetchedPageStore
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.item_registration import epoch_ms
from nora.infra.sql.item_retention import tombstone_items
from nora.infra.sql.observations import SqlObservationStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.schema import (
    BRIDGE_ACTION,
    FETCHED_PAGES,
    MEMORY_NODE,
    MESSAGE_DELETE,
    MESSAGE_EDIT,
    MESSAGE_REACTION,
    MESSAGES,
    PLATFORM_OBSERVATION,
    PRESENCE_FIXES,
    ROW_HISTORY,
    SOURCE_HEALTH,
)
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.condition_matcher import SqlIntentionBatch
from nora.memory.domain.acts import ActAuthority
from nora.memory.domain.conditions import decode_condition
from nora.memory.domain.registration import ITEM_SOURCES, PROTECTED_ITEM_SOURCES
from nora.memory.wire import condition_matcher
from nora.obs.metrics import Metrics
from tests.fixtures.legacy_projection import ProjectingRecordStore
from tests.infra.rollup_fixtures import RollupFixture
from tests.infra.test_absence import _END, _SIX_HOURS, _START, _silent_span
from tests.infra.test_act_envelope import PASS
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_condition_matcher import _edges, _endings, _head, _health, _intention
from tests.infra.test_item_registration import (
    _AT,
    _MS,
    _PERSON,
    _ROOM,
    _UNIT,
    _registration,
    _Writer,
    engine,  # noqa: F401
)


def test_every_item_source_has_content_columns():
    assert set(ITEM_SOURCES) <= set(_SOURCE_CONTENT)


@pytest.mark.parametrize("source", [
    "nora_act", "nora_act_outcome", "nora_pass_output", "computer_events",
    "presence_fixes", "generated_unknown",
])
def test_retention_rejects_a_source_without_a_configurable_horizon(example_config_path, source):
    with example_config_path.open("a") as config:
        config.write(f"\n[memory.retention.horizon_days]\n{source} = 1\n")
    with pytest.raises(ConfigError, match=source):
        load(example_config_path, require_secrets=False)



async def test_protected_item_sources_match_postgres_delete_guards(engine):
    # The named message trigger updates extraction state and returns OLD on delete.
    async with engine.connect() as conn:
        trigger_tables = set(await conn.scalars(sa.text("""
            SELECT c.relname
              FROM pg_trigger AS t
              JOIN pg_class AS c ON c.oid = t.tgrelid
              JOIN pg_namespace AS ns ON ns.oid = c.relnamespace
             WHERE ns.nspname = current_schema()
               AND NOT t.tgisinternal AND t.tgenabled <> 'D'
               AND (t.tgtype & 2) <> 0 AND (t.tgtype & 8) <> 0
               AND NOT (c.relname = 'messages' AND t.tgname = 'extraction_changed'
                        AND t.tgfoid = 'extraction_source_changed()'::regprocedure)
        """)))
        item_references = (await conn.execute(sa.text("""
            SELECT parent.relname, child.relname
              FROM pg_constraint AS fk
              JOIN pg_class AS parent ON parent.oid = fk.confrelid
              JOIN pg_class AS child ON child.oid = fk.conrelid
              JOIN pg_namespace AS ns ON ns.oid = parent.relnamespace
             WHERE ns.nspname = current_schema()
               AND fk.contype = 'f' AND fk.confdeltype IN ('a', 'r')
        """))).all()
    protected = trigger_tables & ITEM_SOURCES.keys()
    protected.update(
        parent for parent, child in item_references
        if parent in ITEM_SOURCES and child in ITEM_SOURCES
    )
    assert protected == PROTECTED_ITEM_SOURCES


def _page(*, expires=_AT):
    return FetchedPage(_ROOM, "https://example.invalid/page", "generated-key", _AT, expires, {})


async def _registry(engine, graph_id, unit=_UNIT):
    async with engine.connect() as conn:
        return (await conn.execute(sa.select(REGISTRY).where(
            REGISTRY.c.id == graph_id, REGISTRY.c.unit == unit,
        ))).mappings().one()


async def test_configured_horizon_tombstones_only_old_rows_and_keeps_citation(engine):
    await _health(engine, "signal", "up")
    store = ProjectingRecordStore(
        engine, registration=_registration(), max_per_conversation=None, horizons={"messages": 1},
    )
    writer = _Writer(engine, _registration())
    msg = replace(writer._message(), body="generated old text", raw={})
    message_id = await store.append(message=msg, person_id=_PERSON)
    fresh = await store.append(
        message=replace(msg, timestamp=_MS + 172_800_000), person_id=_PERSON,
    )
    row = (await _rows(engine, MESSAGES))[message_id]
    graph_id = row["graph_id"]
    graph = SqlCognitiveGraph(engine)
    await graph.create(CLAIM, {
        "id": "generated-claim", "unit": _UNIT, "kind": "claim", "basis": "inferred",
        "reasoning": "induced", "confidence": "low", "part": "fact", "content": {"text": "fixture"},
        "state": "live", "writer": "generated", "exposure": "internal",
    })
    await graph.create(EDGE, {
        "id": "generated-cites", "unit": _UNIT, "kind": "edge", "basis": "computed",
        "state": "live", "writer": "generated", "exposure": "internal", "type": "cites",
        "src_id": "generated-claim", "dst_id": graph_id,
    })
    assert await store.prune_all(now=_AT + timedelta(days=2)) == 1
    assert set(await _rows(engine, MESSAGES)) == {fresh}
    assert len(await _rows(engine, SOURCE_HEALTH)) == 1
    assert (await _edges(engine, "cites"))["generated-claim"]["dst_id"] == graph_id
    tombstone = await _registry(engine, graph_id)
    assert (tombstone["id"], tombstone["unit"], tombstone["kind"]) == (graph_id, _UNIT, "item")
    assert tombstone["state"] == "tombstoned"
    assert tombstone["occurred_at"] == row["occurred_at"]
    assert tombstone["tombstoned_at"] is not None
    content = (
        b'{"body":"generated old text","external_files_json":null,'
        b'"media_json":null,"raw_json":"{}"}'
    )
    assert tombstone["content_hash"] == hashlib.sha256(content).hexdigest()
    async with engine.connect() as conn:
        nodes = (await conn.scalars(sa.select(MEMORY_NODE.c.source_id).where(
            MEMORY_NODE.c.kind == "message",
        ))).all()
        assert nodes == [str(fresh)]


async def test_prune_skips_a_referenced_message_and_finishes_both_sweeps(engine, caplog):
    registration = _registration()
    metrics = Metrics()
    store = SqlRecordStore(
        engine, registration=registration, max_per_conversation=2,
        horizons={"messages": 1}, metrics=metrics,
    )
    message = _Writer(engine, registration)._message()
    day = 86_400_000
    blocked, expired_a, expired_b, count_only, fresh_a, fresh_b = [
        await store.append(message=replace(message, timestamp=_MS + offset), person_id=_PERSON)
        for offset in (0, 1, 2, day + 1, day + 2, day + 3)
    ]
    await store.append_edit(message_id=blocked, message=message)
    rows = await _rows(engine, MESSAGES)
    (edit,) = (await _rows(engine, MESSAGE_EDIT)).values()
    async with engine.begin() as conn:
        await conn.execute(sa.insert(BRIDGE_ACTION).values(
            action_id="generated-blocker", platform="signal",
            account_key="generated-account", owner_kind="live_invocation", owner_id="fixture",
            op="send", request={}, request_hash="fixture", message_id=blocked,
        ))

    assert await store.prune_all(now=_AT + timedelta(days=2)) == 3
    remaining = await _rows(engine, MESSAGES)
    assert set(remaining) == {blocked, fresh_a, fresh_b}
    assert remaining[blocked] == rows[blocked]
    assert (await _rows(engine, MESSAGE_EDIT))[edit["id"]] == edit
    registry = await _rows(engine, REGISTRY)
    for message_id in (expired_a, expired_b, count_only):
        assert registry[rows[message_id]["graph_id"]]["state"] == "tombstoned"
    for graph_id in (rows[blocked]["graph_id"], edit["graph_id"]):
        endpoint = registry[graph_id]
        assert endpoint["state"] == "live"
        assert endpoint["content_hash"] is endpoint["tombstoned_at"] is None
    coverage = (await _rows(engine, EXPIRED_COVERAGE)).values()
    assert {(r["source"], r["identity"], r["from"], r["to"]) for r in coverage} == {
        ("messages", _PERSON, epoch_ms(_MS + 1), epoch_ms(_MS + 2)),
        ("messages", _PERSON, epoch_ms(_MS + day + 1), epoch_ms(_MS + day + 1)),
    }
    assert metrics.get("nora_errors_total", stage="prune") == 2
    assert caplog.messages.count(
        f"prune skipped messages item key={(blocked,)!r} because of a foreign key"
    ) == 2


async def test_message_count_prune_tombstones_all_three_cascade_children(engine):
    writer = _Writer(engine, _registration())
    store = SqlRecordStore(engine, registration=writer.registration, max_per_conversation=1)
    message = writer._message()
    old = await store.append(message=message, person_id=_PERSON)
    await store.append_edit(message_id=old, message=message)
    await store.append_reaction(message_id=old, message=message, person_id=_PERSON)
    await store.append_delete(message_id=old, message=message, person_id=_PERSON)
    tables = (MESSAGES, MESSAGE_EDIT, MESSAGE_REACTION, MESSAGE_DELETE)
    ids = {}
    for table in tables:
        (row,) = (await _rows(engine, table)).values()
        ids[table] = row["graph_id"]
    fresh = await store.append(message=replace(message, timestamp=message.timestamp + 1),
                               person_id=_PERSON)
    assert await store.prune_all() == 1
    assert set(await _rows(engine, MESSAGES)) == {fresh}
    for table in tables:
        if table is not MESSAGES:
            assert not await _rows(engine, table)
        assert (await _registry(engine, ids[table]))["state"] == "tombstoned"


async def test_page_prune_removes_unscoped_rows_without_graph_or_coverage(engine):
    store = SqlFetchedPageStore(engine, registration=_registration())
    await store.put(page=replace(_page(), conversation_id="generated-unscoped"))
    async with engine.connect() as conn:
        assert await conn.scalar(sa.select(sa.func.count()).select_from(FETCHED_PAGES)) == 1
    assert await store.purge_expired(now=_AT) == ["generated-key"]
    async with engine.connect() as conn:
        for table in (FETCHED_PAGES, REGISTRY, EXPIRED_COVERAGE):
            assert await conn.scalar(sa.select(sa.func.count()).select_from(table)) == 0


async def test_refetch_revives_the_same_registered_page(engine):
    store = SqlFetchedPageStore(engine, registration=_registration())
    page = _page()
    await store.put(page=page)
    async with engine.connect() as conn:
        graph_id = await conn.scalar(sa.select(FETCHED_PAGES.c.graph_id))
    assert await store.purge_expired(now=_AT) == [page.blob_key]
    assert (await _registry(engine, graph_id))["state"] == "tombstoned"
    assert await store.get(conversation_id=_ROOM, url=page.url, now=_AT) is None
    await store.put(page=replace(page, expires_at=_AT + timedelta(hours=1)))
    assert await store.get(conversation_id=_ROOM, url=page.url, now=_AT) is not None
    live = await _registry(engine, graph_id)
    assert live["state"] == "live"
    assert all(live[k] is None for k in ("occurred_at", "content_hash", "tombstoned_at"))


async def test_closed_by_witness_survives_message_retention(engine):
    writer = _Writer(engine, _registration())
    store = SqlRecordStore(engine, registration=writer.registration, max_per_conversation=0)
    text = "Generated acceptance."
    await _intention(engine, "target", "identity_event", {
        "identity_id": _PERSON, "platform": "signal", "room": _ROOM, "text": text,
    })
    await _health(engine, "signal", "up")
    await store.append(message=replace(writer._message(), body=text), person_id=_PERSON)
    await condition_matcher(engine).run_once()
    witness_id = (await _edges(engine))["target"]["dst_id"]
    assert await store.prune_all() == 1
    assert await _endings(engine) == {"target": "achieved"}
    assert (await _edges(engine))["target"]["dst_id"] == witness_id
    assert (await _registry(engine, witness_id))["state"] == "tombstoned"


async def test_presence_sweep_records_expiry_coverage_without_a_graph_tombstone(engine):
    person = "generated-person"
    record = RollupFixture(engine, people=(person,))
    for at in (_MS, _MS + 90_000, _MS + 180_000):
        await record.fix(person, at)
    assert await PresenceStore(engine, retention_days=1).prune(now_ms=_MS + 86_520_000) == 2
    (fix,) = (await _rows(engine, PRESENCE_FIXES)).values()
    assert fix["ts"] == _MS + 180_000
    async with engine.connect() as conn:
        history = (await conn.execute(sa.select(ROW_HISTORY.c.image).where(
            ROW_HISTORY.c.table_name == "presence_fixes", ROW_HISTORY.c.op == "delete",
        ))).scalars().all()
        registered = await conn.scalar(sa.select(sa.func.count()).select_from(REGISTRY))
    coverage = (await _rows(engine, EXPIRED_COVERAGE)).values()
    assert sorted(image["ts"] for image in history) == [_MS, _MS + 90_000]
    assert registered == 0
    assert [(r["unit"], r["source"], r["identity"], r["from"], r["to"]) for r in coverage] == [
        (record.unit, "presence_fixes", person, _AT, _AT + timedelta(seconds=90)),
    ]


async def test_predicate_is_unknown_over_expired_witness_input(engine):
    head = await _head(engine, "create", "open")
    await _health(engine, "signal", "up")
    value = {"graph_id": head["graph_id"], "path": ["source_state", "state"], "equals": "open"}
    await _intention(engine, "waiting", "predicate", value)
    async with engine.begin() as conn:
        batch = SqlIntentionBatch(conn, SqlCognitiveGraph(engine))
        (row,) = await batch.open()
        assert (await batch.evaluate(row, decode_condition("predicate", value))).truth == "true"
        assert await tombstone_items(conn, PLATFORM_OBSERVATION)
        assert (await batch.evaluate(row, decode_condition("predicate", value))).truth == "unknown"
    await condition_matcher(engine).run_once()
    assert await _endings(engine) == {"waiting": None}


async def test_person_event_is_unknown_after_its_matching_message_expires(engine):
    value = {
        "identity_id": _PERSON, "platform": "signal", "room": _ROOM, "text": "Generated answer.",
    }
    await _intention(engine, "waiting", "identity_event", value)
    await _health(engine, "signal", "up")
    writer = _Writer(engine, _registration())
    store = SqlRecordStore(engine, registration=writer.registration, max_per_conversation=0)
    await store.append(message=replace(writer._message(), body=value["text"]), person_id=_PERSON)
    async with engine.begin() as conn:
        batch = SqlIntentionBatch(conn, SqlCognitiveGraph(engine))
        (row,) = await batch.open()
        condition = decode_condition("identity_event", value)
        assert (await batch.evaluate(row, condition)).truth == "true"
    assert await store.prune_all() == 1
    async with engine.begin() as conn:
        batch = SqlIntentionBatch(conn, SqlCognitiveGraph(engine))
        (row,) = await batch.open()
        assert (await batch.evaluate(row, condition)).truth == "unknown"


async def test_rebuilt_healthy_silence_is_absence_only_for_the_person_without_expiry(engine):
    person, other = "generated-one", "generated-two"
    record = RollupFixture(engine, people=(person, other), config=UnitConfig(
        deviation_silence_days=1, tz_name="UTC",
        health_max_poll_gap_seconds=_SIX_HOURS / 1000,
    ))
    await _silent_span(record)
    await record.message("signal", _START - _SIX_HOURS, person=other)
    await record.fix(person, _START + _SIX_HOURS)
    assert await PresenceStore(engine, retention_days=1).prune(now_ms=_END + _SIX_HOURS + 1) == 1
    rebuilt = await record.runner.rebuild(through_ms=_END)
    report = await record.runner.run(build_id=rebuilt, through_ms=_END)
    assert report.failed == ()
    stimuli = await SqlGraphStimuli(engine).read(record.unit)
    assert [s.identity for s in stimuli if s.kind == "absence"] == [other]



async def test_expiry_covers_both_people_on_an_observation(engine):
    other = "generated-target"
    record = RollupFixture(engine, people=(_PERSON, other))
    obs = Observation(
        "poll", SourceScope("signal", "generated-account", dict(record.rooms)["signal"]),
        "generated-poll", "create", PollPayload(options=(("a", "Fixture"),), state="open"),
        occurred_ms=_MS, received_ms=_MS, time_quality="upstream",
        actor_id=_PERSON, target_author_id=other,
    )
    store = SqlObservationStore(engine, registration=record.registration)
    await store.append(observation=obs, event_id="event", source_digest="generated")
    (row,) = (await _rows(engine, PLATFORM_OBSERVATION)).values()
    async with engine.begin() as conn:
        assert await tombstone_items(
            conn, PLATFORM_OBSERVATION, PLATFORM_OBSERVATION.c.id == row["id"],
        )
    coverage = (await _rows(engine, EXPIRED_COVERAGE)).values()
    assert {r["identity"] for r in coverage} == {_PERSON, other}


async def test_retention_rolls_back_if_the_memory_index_cannot_drop_a_message(engine):
    writer = _Writer(engine, _registration())
    store = ProjectingRecordStore(engine, registration=writer.registration, max_per_conversation=0)
    message_id = await store.append(message=writer._message(), person_id=_PERSON)
    row = (await _rows(engine, MESSAGES))[message_id]
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE FUNCTION reject_generated_drop() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'generated index refusal';
            END $$;
        """))
        await conn.execute(sa.text("""
            CREATE TRIGGER reject_generated_drop BEFORE DELETE ON memory_node
            FOR EACH ROW EXECUTE FUNCTION reject_generated_drop()
        """))
    try:
        with pytest.raises(sa.exc.DBAPIError, match="generated index refusal"):
            await store.prune_all()
        assert set(await _rows(engine, MESSAGES)) == {message_id}
        assert (await _registry(engine, row["graph_id"]))["state"] == "live"
        assert not await _rows(engine, EXPIRED_COVERAGE)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("DROP TRIGGER reject_generated_drop ON memory_node"))
            await conn.execute(sa.text("DROP FUNCTION reject_generated_drop()"))



async def test_concurrent_first_observations_register_each_pass_output_once(engine):
    store = SqlActStore(engine, _registration())
    for round_number in range(18):
        context = replace(PASS, pass_id=f"generated-race-{round_number}")
        authority = ActAuthority(context.unit, room=context.room, pass_context=context)
        outcomes = await asyncio.gather(
            *(store.append_pass(authority, "silence", None) for _ in range(8)),
            return_exceptions=True,
        )
        assert outcomes == [None] * 8
    rows = await _rows(engine, NORA_PASS_OUTPUT)
    assert len(rows) == 18
    assert len({row["graph_id"] for row in rows.values()}) == 18
