"""Shared contract. Phase 1 must replace the explicit SQL skip with its loader."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.memory_graph import (
    ENTITY_KINDS,
    GRAPH_RELATIONS,
    LOCATION_BEARING,
    RELATION_RULES,
    Correction,
    Entity,
    Evidence,
    MemoryGraph,
    Refusal,
    Relation,
    Source,
    Walk,
    WorkItem,
    pointer_entity,
)
from nora.testing.memory_graph import FakeMemoryGraph

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PERSON = Entity("p1", "person", "u1", "Person One")
EVENT = Entity("e1", "event", "u1", "Generated Event")
SOURCE = Source(("message", "m1"), "record", "u1", ("p1",), "I attended the event.")
REL = Relation("r1", PERSON.key, "attended", EVENT.key, "notebook", "u1", ("p1",), asserted_at=NOW)
EV = Evidence(SOURCE.key, relation_id="r1", span="attended")


@pytest.fixture(scope="module")
def _graph_db(pg_admin):
    from uuid import uuid4

    import psycopg

    from nora.infra.sql.migrations_runner import run_migrations

    dbname = "t_graph_" + uuid4().hex
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
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["fake", "sql"])
async def graph(request):
    if request.param == "fake":
        yield FakeMemoryGraph()
        return
    import sqlalchemy as sa

    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.memory_graph import SqlMemoryGraph

    engine = create_sql_engine(request.getfixturevalue("_graph_db"))
    async with engine.begin() as conn:
        await conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
        await conn.execute(
            sa.text("TRUNCATE relation_evidence, relation, entity, memory_work_item CASCADE")
        )
    try:
        yield SqlMemoryGraph(engine)
    finally:
        await engine.dispose()


async def load(graph: MemoryGraph, relation: Relation = REL) -> None:
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT),
        relations=(relation,),
        evidence=(replace(EV, relation_id=relation.id),),
        sources={SOURCE.key: SOURCE},
    )
    assert result.relations == (relation,)
    assert not result.refused


def test_vocabulary_is_complete() -> None:
    assert set(GRAPH_RELATIONS) == set(RELATION_RULES)
    assert len(ENTITY_KINDS) == 9


@pytest.mark.parametrize(
    ("change", "source", "evidence", "reason"),
    [
        ({"rel": "invented"}, SOURCE, EV, "vocabulary"),
        ({"dst": ("work", "e1")}, SOURCE, EV, "wrong_ends"),
        ({"layer": "record"}, SOURCE, EV, "wrong_layer"),
        ({}, replace(SOURCE, layer="notebook"), EV, "notebook_evidence"),
        ({}, SOURCE, replace(EV, source=("message", "unseen")), "unseen_source"),
        ({}, SOURCE, replace(EV, span="not present"), "span_not_in_source"),
        ({"unit": "u2"}, SOURCE, EV, "scope_wider_than_evidence"),
        ({"persons": ()}, SOURCE, EV, "scope_wider_than_evidence"),
    ],
)
async def test_refusals(
    graph: MemoryGraph, change: dict, source: Source, evidence: Evidence, reason: str
) -> None:
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT),
        relations=(replace(REL, **change),),
        evidence=(evidence,),
        sources={source.key: source},
    )
    assert not result.relations
    assert reason in [r.reason for r in result.refused]
    assert not (await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))).nodes


async def test_partial_commit(graph: MemoryGraph) -> None:
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT),
        relations=(REL, replace(REL, id="bad", rel="invented")),
        evidence=(EV, replace(EV, relation_id="bad")),
        sources={SOURCE.key: SOURCE},
    )
    assert Refusal("evidence", "bad", "orphan_evidence") in result.refused
    assert result.relations == (REL,)
    assert result.evidence == (EV,)
    assert result.refused[0].reason == "vocabulary"
    preview = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))
    assert preview.status == "ok"
    assert len(preview.nodes) == 2
    assert preview.relations[0].evidence_count == 1
    assert preview.relations[0].relation.layer == "notebook"


async def test_missing_evidence_and_orphans(graph: MemoryGraph) -> None:
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT), relations=(REL,), evidence=(), sources={}
    )
    assert {r.reason for r in result.refused} == {"missing_evidence", "orphan_entity"}


@pytest.mark.parametrize("caps", [{"nodes": 1}, {"depth": 0}, {"fan_out": 1}])
async def test_caps_are_partial(graph: MemoryGraph, caps: dict) -> None:
    await load(graph)
    other = replace(EVENT, id="e2", name="Another Generated Event")
    r2 = replace(REL, id="r2", dst=other.key)
    result = await graph.stage_and_admit(
        entities=(other,),
        relations=(r2,),
        evidence=(replace(EV, relation_id="r2"),),
        sources={SOURCE.key: SOURCE},
    )
    assert result.relations
    result_walk = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"}), **caps))
    assert result_walk.status == "partial"
    assert len(result_walk.nodes) <= caps.get("nodes", 100)


async def test_hub_rule_and_scope_and_filters(graph: MemoryGraph) -> None:
    await load(graph)
    pointer = Relation(
        "pointer", SOURCE.key, "authored_by", PERSON.key, "record", "u1", ("p1",), asserted_at=NOW
    )
    assert (
        await graph.stage_and_admit(entities=(), relations=(pointer,), evidence=(), sources={})
    ).relations
    walk = await graph.walk(
        (PERSON.key,), Walk("u1", frozenset({"p1"}), layers=("record", "notebook"))
    )
    assert {n.key for n in walk.nodes} == {PERSON.key, EVENT.key}
    assert [e.relation.id for e in walk.relations] == ["r1"]
    for query in (
        Walk("u2", frozenset({"p1"})),
        Walk("u1", frozenset()),
        Walk("u1", frozenset({"p1"}), layers=("rollup",)),
    ):
        assert not (await graph.walk((PERSON.key,), query)).nodes


async def test_validity(graph: MemoryGraph) -> None:
    await load(graph, replace(REL, valid_from=NOW, valid_to=NOW + timedelta(days=1)))
    for at, count in ((NOW - timedelta(seconds=1), 0), (NOW, 1), (NOW + timedelta(days=1), 0)):
        result = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"}), valid_at=at))
        assert len(result.relations) == count


async def test_work_lifecycle(graph: MemoryGraph) -> None:
    item = WorkItem("w1", SOURCE.key, "u1", "generated-hash", NOW)
    assert await graph.enqueue(item) == await graph.enqueue(replace(item, id="duplicate"))
    assert await graph.claim(historical=True, now=NOW, lease_seconds=10) is None
    first = await graph.claim(historical=False, now=NOW, lease_seconds=10)
    assert first and first.attempts == 1
    assert await graph.claim(historical=False, now=NOW, lease_seconds=10) is None
    second = await graph.claim(historical=False, now=NOW + timedelta(seconds=11), lease_seconds=10)
    assert second and second.attempts == 2
    assert not await graph.settle(first, now=NOW)
    assert not await graph.fail(first, error="stale", now=NOW)
    assert await graph.settle(second, now=NOW)
    assert (
        await graph.claim(historical=False, now=NOW + timedelta(days=1), lease_seconds=10) is None
    )
    changed = await graph.enqueue(replace(item, id="w2", input_hash="changed-hash"))
    assert changed.state == "pending"
    for attempt in range(1, 6):
        now = NOW + timedelta(days=attempt)
        claimed = await graph.claim(historical=False, now=now, lease_seconds=10)
        assert claimed and claimed.attempts == attempt
        assert await graph.fail(claimed, error="generated failure", now=now)
    parked = await graph.enqueue(replace(changed, id="duplicate-failed"))
    assert parked.state == "failed" and parked.attempts == 5
    assert parked.last_error == "generated failure"
    assert (
        await graph.claim(historical=False, now=NOW + timedelta(days=10), lease_seconds=10) is None
    )


async def test_attribute_evidence_is_admitted_independently(graph: MemoryGraph) -> None:
    event = replace(EVENT, attributes={"category": "generated", "venue": "unsupported"})
    public = replace(SOURCE, persons=())
    attribute = Evidence(SOURCE.key, entity_id=EVENT.id, attribute="category", span="event")
    result = await graph.stage_and_admit(
        entities=(PERSON, event),
        relations=(REL,),
        evidence=(EV, attribute),
        sources={public.key: public},
    )
    assert result.relations == (REL,)
    assert result.entities[1].attributes == {"category": "generated"}
    assert attribute in result.evidence
    assert any(r.reason == "missing_evidence" for r in result.refused)


async def test_expired_attempts_park_without_deleting(graph: MemoryGraph) -> None:
    item = WorkItem("expired", SOURCE.key, "u1", "expired-hash", NOW, historical=True)
    await graph.enqueue(item)
    for attempt in range(1, 6):
        claimed = await graph.claim(
            historical=True, now=NOW + timedelta(days=attempt), lease_seconds=10
        )
        assert claimed and claimed.attempts == attempt
    assert await graph.claim(historical=True, now=NOW + timedelta(days=6), lease_seconds=10) is None
    assert (await graph.enqueue(item)).state == "failed"


async def test_unavailable_is_not_empty_success(graph: MemoryGraph) -> None:
    await load(graph)
    query = Walk("u1", frozenset({"p1"}))
    assert (await graph.walk((PERSON.key,), query)).nodes
    if isinstance(graph, FakeMemoryGraph):
        graph.available = False
        result = await graph.walk((PERSON.key,), query)
        graph.available = True
    else:
        import sqlalchemy as sa

        from nora.infra.sql.memory_graph import SqlMemoryGraph

        assert isinstance(graph, SqlMemoryGraph)
        # A real conflicting table lock forces the SQL read past its deadline.
        slow = SqlMemoryGraph(graph.engine, statement_timeout_ms=1)
        async with graph.engine.begin() as lock:
            await lock.execute(sa.text("LOCK TABLE relation IN ACCESS EXCLUSIVE MODE"))
            result = await slow.walk((PERSON.key,), query)
    assert result.status == "unavailable"
    assert not result.nodes and not result.relations
    assert (await graph.walk((PERSON.key,), query)).nodes


async def test_attributes_accumulate_across_batches(graph: MemoryGraph) -> None:
    public = replace(SOURCE, persons=())
    first = replace(EVENT, attributes={"category": "generated"})
    first_ev = Evidence(SOURCE.key, entity_id=EVENT.id, attribute="category", span="event")
    await graph.stage_and_admit(
        entities=(PERSON, first),
        relations=(REL,),
        evidence=(EV, first_ev),
        sources={public.key: public},
    )
    second = replace(
        EVENT, attributes={"venue": "generated venue", "category": "unsupported overwrite"}
    )
    second_ev = Evidence(SOURCE.key, entity_id=EVENT.id, attribute="venue", span="event")
    r2 = replace(REL, id="r2")
    result = await graph.stage_and_admit(
        entities=(PERSON, second),
        relations=(r2,),
        evidence=(replace(EV, relation_id="r2"), second_ev),
        sources={public.key: public},
    )
    event = next(e for e in result.entities if e.id == EVENT.id)
    assert event.attributes == {"category": "generated", "venue": "generated venue"}
    assert Refusal("entity", EVENT.id, "missing_evidence") in result.refused


def test_a_relation_without_asserted_at_is_refused() -> None:
    with pytest.raises(ValueError, match="asserted_at"):
        replace(REL, asserted_at=None)


def test_keyword_only_contract() -> None:
    import inspect

    for name in ("stage_and_admit", "aliases", "candidates", "claim", "requeue_failed"):
        params = inspect.signature(getattr(MemoryGraph, name)).parameters
        assert all(
            p.kind == inspect.Parameter.KEYWORD_ONLY for n, p in params.items() if n != "self"
        )
    for name in ("settle", "fail"):
        params = inspect.signature(getattr(MemoryGraph, name)).parameters
        assert all(
            params[n].kind == inspect.Parameter.KEYWORD_ONLY
            for n in params
            if n not in ("self", "item")
        )


def test_asserted_at_is_required_keyword_only() -> None:
    import inspect

    p = inspect.signature(Relation).parameters["asserted_at"]
    assert p.default is inspect.Parameter.empty
    assert p.kind == inspect.Parameter.KEYWORD_ONLY
    assert Relation.__annotations__["asserted_at"] == "datetime"


async def test_duplicate_relation_never_overwrites(graph: MemoryGraph) -> None:
    await load(graph)
    result = await graph.stage_and_admit(
        entities=(),
        relations=(replace(REL, valid_to=NOW),),
        evidence=(EV,),
        sources={SOURCE.key: SOURCE},
    )
    assert not result.relations
    assert Refusal("relation", REL.id, "duplicate_relation") in result.refused
    walk = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))
    assert walk.relations[0].relation == REL


async def test_duplicate_entity(graph: MemoryGraph) -> None:
    await load(graph)
    duplicate = replace(EVENT, id="duplicate")
    result = await graph.stage_and_admit(
        entities=(duplicate,),
        relations=(replace(REL, id="r2", dst=duplicate.key),),
        evidence=(replace(EV, relation_id="r2"),),
        sources={SOURCE.key: SOURCE},
    )
    assert Refusal("entity", duplicate.id, "duplicate_entity") in result.refused
    assert not result.entities and not result.relations


@pytest.mark.parametrize("kind", ["close", "supersede", "dispute", "retire"])
async def test_corrections_keep_history(graph: MemoryGraph, kind: str) -> None:
    await load(graph)
    replacement = replace(REL, id="r2")
    correction = Correction(REL.id, kind, NOW, by="r2", evidence=(SOURCE.key,))
    result = await graph.stage_and_admit(
        entities=(),
        relations=(replacement,),
        evidence=(replace(EV, relation_id="r2"),),
        sources={SOURCE.key: SOURCE},
        corrections=(correction,),
    )
    assert result.corrections == (correction,) and not result.refused
    assert Evidence(SOURCE.key, relation_id=REL.id) in result.evidence
    # Closing retains a current historical relation; status changes refuse another correction.
    again = await graph.stage_and_admit(
        entities=(),
        relations=(),
        evidence=(),
        sources={SOURCE.key: SOURCE},
        corrections=(correction,),
    )
    if kind == "close":
        assert again.corrections == (correction,)
    else:
        assert Refusal("correction", REL.id, "not_current") in again.refused
    retained = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))
    if kind == "close":
        old = next(e.relation for e in retained.relations if e.relation.id == REL.id)
        assert old.valid_to == NOW and old.status == "current"
    else:
        assert {e.relation.id for e in retained.relations} == {"r2"}
    if isinstance(graph, FakeMemoryGraph):
        old = graph.relations[REL.id]
        assert (
            old.status
            == {
                "close": "current",
                "supersede": "superseded",
                "dispute": "disputed",
                "retire": "retired",
            }[kind]
        )
        assert old.superseded_by == ("r2" if kind == "supersede" else None)
        assert EV in graph.evidence


@pytest.mark.parametrize(
    ("change", "source", "reason"),
    [
        ({"relation_id": "unknown"}, SOURCE, "unknown_relation"),
        ({"evidence": ()}, SOURCE, "missing_evidence"),
        ({"evidence": (("message", "unseen"),)}, SOURCE, "unseen_source"),
        ({}, replace(SOURCE, unit="u2"), "scope_wider_than_evidence"),
        ({}, replace(SOURCE, persons=("p2",)), "scope_wider_than_evidence"),
        ({}, replace(SOURCE, layer="notebook"), "notebook_evidence"),
        ({"kind": "supersede", "by": "absent"}, SOURCE, "missing_replacement"),
        ({"kind": "supersede", "by": REL.id}, SOURCE, "missing_replacement"),
    ],
)
async def test_correction_refusals(
    graph: MemoryGraph, change: dict, source: Source, reason: str
) -> None:
    await load(graph)
    correction = replace(Correction(REL.id, "retire", NOW, evidence=(SOURCE.key,)), **change)
    result = await graph.stage_and_admit(
        entities=(),
        relations=(),
        evidence=(),
        sources={source.key: source},
        corrections=(correction,),
    )
    assert not result.corrections
    assert Refusal("correction", correction.relation_id, reason) in result.refused
    assert (await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))).relations[
        0
    ].relation == REL


async def test_supersede_requires_admitted_replacement(graph: MemoryGraph) -> None:
    await load(graph)
    result = await graph.stage_and_admit(
        entities=(),
        relations=(replace(REL, id="bad", rel="invented"),),
        evidence=(),
        sources={SOURCE.key: SOURCE},
        corrections=(Correction(REL.id, "supersede", NOW, by="bad", evidence=(SOURCE.key,)),),
    )
    assert not result.corrections
    assert Refusal("correction", REL.id, "missing_replacement") in result.refused


@pytest.mark.parametrize("destination", ["rhythm", "ties", "daybook"])
async def test_during_excludes_key_only_stores(graph: MemoryGraph, destination: str) -> None:
    result = await graph.stage_and_admit(
        entities=(),
        relations=(
            replace(REL, src=SOURCE.key, rel="during", dst=(destination, "x"), layer="rollup"),
        ),
        evidence=(EV,),
        sources={SOURCE.key: SOURCE},
    )
    assert Refusal("relation", REL.id, "wrong_ends") in result.refused
    good = replace(REL, src=SOURCE.key, rel="during", dst=("outing", "x"), layer="rollup")
    assert (
        await graph.stage_and_admit(
            entities=(), relations=(good,), evidence=(EV,), sources={SOURCE.key: SOURCE}
        )
    ).relations == (good,)


def test_location_flag() -> None:
    assert frozenset({"works_at", "studies_at", "lives_in", "member_of"}) == LOCATION_BEARING
    for rel in GRAPH_RELATIONS:
        assert replace(REL, rel=rel).location == (
            rel in {"works_at", "studies_at", "lives_in", "member_of"}
        )


async def test_entity_lookup(graph: MemoryGraph) -> None:
    entities = (
        Entity("z", "place", "u1", "Exact", ("HALL",), {"location": (0, 0)}),
        Entity("a", "place", "u1", "Hall annex", attributes={"location": (2, 0)}),
        Entity("b", "place", "u1", "Far hall", attributes={"location": (9, 0)}),
        Entity("hidden", "place", "u2", "Hall"),
        Entity("merged", "place", "u1", "Hall", merged_into="z"),
        Entity("retired", "place", "u1", "Hall", state="retired"),
        Entity("event", "event", "u1", "Event hall"),
    )
    for entity in entities:
        await graph.upsert_entity(entity)
    aliases = await graph.aliases(unit="u1")
    assert aliases["hall"] == ("place", "z")
    assert aliases["exact"] == ("place", "z")
    assert "exact" not in await graph.aliases(unit="u2")
    candidates = await graph.candidates(unit="u1", kind="place", name="hAll", near=("place", "z"))
    assert candidates == entities[:3]
    assert await graph.candidates(unit="u1", kind="place", name="hAll", limit=1) == entities[:1]
    assert not await graph.candidates(unit="u1", kind="place", name="absent")
    assert not await graph.candidates(unit="u1", kind="place", name="hall", limit=0)


async def test_candidates_proximity_window_and_cap(graph: MemoryGraph) -> None:
    anchor = Entity("anchor", "place", "u1", "Anchor", attributes={"location": (0, 0)})
    await graph.upsert_entity(anchor)
    for i in range(7):
        await graph.upsert_entity(
            Entity(str(i), "place", "u1", "Hall", attributes={"location": (7 - i, 0)})
        )
    places = await graph.candidates(unit="u1", kind="place", name="hall", near=anchor.key, limit=99)
    assert [e.id for e in places] == ["6", "5", "4", "3", "2"]
    for id, start in (("a-old", NOW - timedelta(days=5)), ("z-current", NOW)):
        await graph.upsert_entity(
            Entity(
                id,
                "event",
                "u1",
                "Show",
                attributes={"start": start, "end": start + timedelta(hours=1)},
            )
        )
    events = await graph.candidates(
        unit="u1", kind="event", name="show", window=(NOW, NOW + timedelta(hours=2))
    )
    assert [e.id for e in events] == ["z-current", "a-old"]


async def test_operator_requeue_failed(graph: MemoryGraph) -> None:
    items = [
        WorkItem(str(i), ("message", str(i)), unit, str(i), NOW)
        for i, unit in enumerate(("u1", "u2"))
    ]
    for item in items:
        await graph.enqueue(item)
    for i in range(1, 6):
        for _ in items:
            claimed = await graph.claim(
                historical=False, now=NOW + timedelta(days=i), lease_seconds=10
            )
            assert claimed
            await graph.fail(claimed, error="generated", now=NOW + timedelta(days=i))
    later = NOW + timedelta(days=8)
    assert await graph.requeue_failed(unit="u1", now=later) == 1
    retained = await graph.enqueue(items[0])
    assert retained.state == "pending" and retained.attempts == 0 and retained.last_error is None
    assert retained.due_at == later and retained.updated_at == later
    assert (await graph.enqueue(items[1])).state == "failed"
    assert await graph.requeue_failed(now=later) == 1
    assert await graph.requeue_failed(now=later) == 0
    claimed = await graph.claim(historical=False, now=later, lease_seconds=10)
    assert claimed and claimed.attempts == 1


async def test_correction_cannot_cross_write_unit(graph: MemoryGraph) -> None:
    await load(graph)
    foreign = Entity("foreign", "work", "u2", "Generated foreign work")
    result = await graph.stage_and_admit(
        entities=(foreign,),
        relations=(),
        evidence=(),
        sources={SOURCE.key: SOURCE},
        corrections=(Correction(REL.id, "retire", NOW, evidence=(SOURCE.key,)),),
    )
    assert Refusal("correction", REL.id, "scope_wider_than_evidence") in result.refused


def test_entity_name_has_trigram_index() -> None:
    import importlib

    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    for module in ("nora.infra.sql.schema", "nora.infra.sql.migrations.versions.0137_memory_graph"):
        entity = importlib.import_module(module).ENTITY
        ddl = [
            str(CreateIndex(index).compile(dialect=postgresql.dialect()))
            for index in entity.indexes
        ]
        assert any("USING gin (name gin_trgm_ops)" in sql for sql in ddl)


def test_projection_ownership_is_documented() -> None:
    from nora.domain import memory_graph

    assert memory_graph.__doc__
    for rel in ("cites", "supersedes", "draws_on", "disputes", "feedback_on"):
        assert f"`{rel}`" in memory_graph.__doc__
    assert "projector" in memory_graph.__doc__


async def test_record_correction_requires_evidence(graph: MemoryGraph) -> None:
    await graph.upsert_entity(PERSON)
    pointer = Relation(
        "pointer", SOURCE.key, "authored_by", PERSON.key, "record", "u1", ("p1",), asserted_at=NOW
    )
    assert (
        await graph.stage_and_admit(entities=(), relations=(pointer,), evidence=(), sources={})
    ).relations
    correction = Correction(pointer.id, "retire", NOW)
    result = await graph.stage_and_admit(
        entities=(), relations=(), evidence=(), sources={}, corrections=(correction,)
    )
    assert Refusal("correction", pointer.id, "missing_evidence") in result.refused
    corrected = replace(correction, evidence=(SOURCE.key,))
    result = await graph.stage_and_admit(
        entities=(),
        relations=(),
        evidence=(),
        sources={SOURCE.key: SOURCE},
        corrections=(corrected,),
    )
    assert result.corrections == (corrected,) and not result.refused


async def test_one_person_in_two_units_is_two_entities(graph: MemoryGraph) -> None:
    for unit in ("unit-a", "unit-b"):
        person = pointer_entity("person", unit, "p-generated", "Generated Person")
        place = Entity(f"place-{unit}", "place", unit, "Generated Place")
        await graph.upsert_entity(person)
        await graph.upsert_entity(place)
        stay = Relation(
            f"stay-{unit}",
            person.key,
            "stayed_at",
            place.key,
            "rollup",
            unit,
            ("p-generated",),
            asserted_at=NOW,
        )
        result = await graph.stage_and_admit(
            entities=(), relations=(stay,), evidence=(), sources={}
        )
        assert result.relations == (stay,)
    for unit in ("unit-a", "unit-b"):
        seed = pointer_entity("person", unit, "p-generated", "Generated Person").key
        walk = await graph.walk((seed,), Walk(unit, frozenset({"p-generated"})))
        assert [e.relation.id for e in walk.relations] == [f"stay-{unit}"]


async def test_an_entity_never_changes_unit(graph: MemoryGraph) -> None:
    place = Entity("place-fixed", "place", "unit-a", "Generated Place")
    await graph.upsert_entity(place)
    with pytest.raises(ValueError):
        await graph.upsert_entity(replace(place, unit="unit-b"))


async def test_same_as_never_joins_two_units(graph: MemoryGraph) -> None:
    a = pointer_entity("person", "unit-a", "p-generated", "Generated Person")
    b = pointer_entity("person", "unit-b", "p-generated", "Generated Person")
    await graph.upsert_entity(a)
    await graph.upsert_entity(b)
    merge = Relation(
        "merge",
        a.key,
        "same_as",
        b.key,
        "notebook",
        "unit-a",
        ("p-generated",),
        asserted_at=NOW,
    )
    source = replace(SOURCE, unit="unit-a", persons=("p-generated",))
    result = await graph.stage_and_admit(
        entities=(),
        relations=(merge,),
        evidence=(replace(EV, relation_id="merge"),),
        sources={source.key: source},
    )
    assert not result.relations
    assert [r.reason for r in result.refused if r.kind == "relation"] == ["wrong_ends"]


def test_pointer_ids_are_unique_across_kinds() -> None:
    assert pointer_entity("person", "u", "x", "X").id != pointer_entity("room", "u", "x", "X").id


async def test_topic_activity_direct_evidence_counts_times_and_failure(graph: MemoryGraph) -> None:
    topic = Entity("generated-topic", "topic", "u1", "Generated racing")
    early = NOW - timedelta(days=2)
    sources = (SOURCE, replace(SOURCE, key=("message", "m2")))
    rels = tuple(
        replace(REL, id=f"interest-{i}", rel="interested_in", dst=topic.key, asserted_at=t)
        for i, t in enumerate((early, NOW))
    )
    for r, source in zip(rels, sources, strict=True):
        admitted = await graph.stage_and_admit(
            entities=(PERSON, topic) if r == rels[0] else (),
            relations=(r,),
            evidence=(Evidence(source.key, relation_id=r.id),),
            sources={source.key: source},
        )
        assert admitted.relations
    result = await graph.topic_activity(unit="u1", topic=topic.key, at=NOW)
    assert result.status == "ok"
    assert len(result.persons) == 1
    activity = result.persons[0]
    assert activity.person == PERSON.key and activity.evidence_count == 2
    assert (activity.first_asserted_at, activity.last_asserted_at) == (early, NOW)
    assert activity.sources == (SOURCE.key, ("message", "m2"))
    assert (
        await graph.topic_activity(unit="other", topic=topic.key, at=NOW)
    ).status == "nothing_found"
    if isinstance(graph, FakeMemoryGraph):
        graph.available = False
        failed = await graph.topic_activity(unit="u1", topic=topic.key, at=NOW)
        graph.available = True
    else:
        import sqlalchemy as sa

        from nora.infra.sql.memory_graph import SqlMemoryGraph

        slow = SqlMemoryGraph(graph.engine, statement_timeout_ms=1)
        async with graph.engine.begin() as lock:
            await lock.execute(sa.text("LOCK TABLE relation IN ACCESS EXCLUSIVE MODE"))
            failed = await slow.topic_activity(unit="u1", topic=topic.key, at=NOW)
    assert failed.status == "unavailable" and not failed.persons
    await graph.stage_and_admit(
        entities=(),
        relations=(),
        evidence=(),
        sources={SOURCE.key: SOURCE},
        corrections=(
            Correction(rels[0].id, "retire", NOW, evidence=(SOURCE.key,)),
            Correction(rels[1].id, "close", NOW, evidence=(SOURCE.key,)),
        ),
    )
    assert (
        await graph.topic_activity(unit="u1", topic=topic.key, at=NOW)
    ).status == "nothing_found"
