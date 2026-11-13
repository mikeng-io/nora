"""Entries are reachable from their subjects: profile lines, Characters and
events project code-owned `about` relations from the same index edges the
search pointers use, admitted like any notebook relation — evidence is the
entry's resolved citations (Record/Rollup beneath them, never the entry), so
an entry whose citations resolve to nothing projects nothing, and a citation
in someone else's name narrows the persons who may read it. A revision
replaces them; a retirement or hide retires them with the entry; the explicit
backfill reports them.

Module-scoped database per `test_event_citations.py`'s pattern."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.app.graph_project import graph_project
from nora.domain.culture import character_key, unit_culture_key
from nora.domain.gatherings import (
    EventCitation,
    EventEvidence,
    Gathering,
    GatheringStatus,
)
from nora.domain.memory_graph import Walk, pointer_entity
from nora.domain.models import NormalizedMessage
from nora.domain.profile import (
    Account,
    AddOp,
    ProfileCitable,
    RetireOp,
    ReviseOp,
    Said,
)
from nora.domain.rollup import episode_key
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.graph_projection import ProjectionContext
from nora.infra.sql.memory_graph import SqlMemoryGraph
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.schema import (
    EVENT_REVISION,
    MEMORY_EDGE,
    MEMORY_PROJECTION_QUEUE,
    PRESENCE_STAYS,
    RELATION,
    ROLLUP_BUILD,
)

pytestmark = pytest.mark.asyncio

_UNIT = "u"
NOW = 1_790_000_000_000
T0 = datetime(2026, 1, 1, tzinfo=UTC)
ACCOUNT = Account(
    key=character_key(_UNIT, "member-b"),
    entity_kind="human",
    unit=_UNIT,
    subject="member-b",
)
SCOPE = ProjectionContext(
    {"signal:g1": _UNIT, "signal:ga": _UNIT},
    {_UNIT: frozenset({"member-a", "member-b"})},
)


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_graph_about_" + uuid4().hex
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
    eng.update_execution_options(memory_graph=SCOPE)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in (
                "relation_evidence", "relation", "entity", "memory_work_item",
                "graph_refusal", "graph_arrival", "memory_node", "memory_edge",
                "memory_projection_queue", "event_revision", "event_rsvps",
                "events", "messages", "profile_line", "profile", "culture",
                "presence_stays", "rollup_build", "row_history",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


async def _said(engine: Any, *, person: str = "member-b", room: str = "g1") -> str:
    store = SqlRecordStore(engine)
    mid = await store.append(
        message=NormalizedMessage(
            "signal", room, "group", "+1", "P", 1000, "hi", {},
        ),
    )
    await store.set_person_id(row_id=mid, person_id=person)
    return str(mid)


async def _unit_proven(engine: Any) -> None:
    await SqlCultureStore(engine).append(
        key=unit_culture_key(_UNIT), body="the unit", written_at=10,
        established_by="dreamer",
    )


async def _line(
    engine: Any, text: str = "cycles to work", cite: str | None = None,
    person: str = "member-b",
) -> Any:
    await _unit_proven(engine)
    citations = (cite,) if cite else ()
    result = await SqlProfileStore(
        engine, screen=lambda _t: None, index=True
    ).profile_write(
        account=ACCOUNT,
        ops=[AddOp(
            part="facts", kind="attribute", text=text, mode="told",
            citations=citations, told_by=person, fact_key="occupation",
            fact_value=text,
        )],
        citable=ProfileCitable(
            messages={int(cite): Said(1000, person, "g1")} if cite else {},
            listed_rooms=frozenset({"g1"}),
        ),
        now_ms=NOW, written_by_session="tag", apply_decay=False,
    )
    assert result.status == "ok"
    return result.written[0]


def _event(**over: Any) -> Gathering:
    base: dict[str, Any] = {
        "id": uuid4(), "conversation_id": "ga", "title": "dinner",
        "created_by": "member-a", "when": None, "venue_text": None,
        "venue_place_id": None, "venue_name": None, "venue_address": None,
        "venue_rating": None, "venue_price_level": "", "venue_maps_url": None,
        "venue_resolved_at": None, "status": GatheringStatus.PROPOSED,
        "reminder_id": None, "created_at": T0, "unit_id": _UNIT,
        "platform": "signal",
    }
    base.update(over)
    return Gathering(**base)


async def _abouts(engine: Any) -> list[Any]:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                sa.select(RELATION).where(RELATION.c.rel == "about")
            )
        ).mappings().all()


async def test_a_profile_line_is_reachable_from_its_subject(engine):
    mid = await _said(engine)
    sid = await _line(engine, cite=mid)
    entity = pointer_entity("person", _UNIT, "member-b", "member-b")
    rows = await _abouts(engine)
    current = [
        r for r in rows
        if r["src_kind"] == "profile_line" and r["src_id"] == str(sid)
        and r["dst_id"] == entity.id and r["status"] == "current"
    ]
    assert current and current[0]["unit"] == _UNIT
    got = await SqlMemoryGraph(engine).walk(
        (("person", entity.id),), Walk(_UNIT, frozenset({"member-b"}))
    )
    assert any(n.key == ("profile_line", str(sid)) for n in got.nodes)


async def test_a_character_is_reachable_from_its_subject(engine):
    mid = await _said(engine)
    await _unit_proven(engine)
    await SqlCultureStore(engine).append(
        key=character_key(_UNIT, "member-b"), body="the dreamer",
        written_at=10, established_by="dreamer", citations=(mid,),
    )
    entity = pointer_entity("person", _UNIT, "member-b", "member-b")
    rows = await _abouts(engine)
    assert any(
        r["src_kind"] == "character" and r["dst_id"] == entity.id
        and r["status"] == "current"
        for r in rows
    )


async def test_an_event_is_reachable_from_its_people(engine):
    mid = await _said(engine, person="member-a", room="ga")
    event = await SqlGatheringStore(engine).create(
        _event(people=("member-a",)),
        evidence=EventEvidence(
            parts=frozenset({"what"}),
            citations=(EventCitation(part="what", store="message", ref=mid),),
        ),
    )
    entity = pointer_entity("person", _UNIT, "member-a", "member-a")
    rows = await _abouts(engine)
    assert any(
        r["src_kind"] == "event_entry" and r["src_id"] == str(event.id)
        and r["dst_id"] == entity.id and r["status"] == "current"
        for r in rows
    )
    got = await SqlMemoryGraph(engine).walk(
        (("person", entity.id),), Walk(_UNIT, frozenset({"member-a"}))
    )
    assert any(n.key == ("event_entry", str(event.id)) for n in got.nodes)


async def test_citations_that_resolve_to_nothing_project_nothing(engine):
    mid = await _said(engine)
    await _line(engine, cite="424242424242")
    await SqlGatheringStore(engine).create(_event(people=("member-a",)))
    assert mid  # the row was written; it is just not cited
    assert await _abouts(engine) == []


async def test_a_citation_in_another_persons_name_narrows_the_readers(engine):
    mid = await _said(engine, person="member-a")
    sid = await _line(engine, cite=mid)
    entity = pointer_entity("person", _UNIT, "member-b", "member-b")
    rows = [
        r for r in await _abouts(engine)
        if r["src_id"] == str(sid) and r["status"] == "current"
    ]
    assert rows and set(rows[0]["persons"]) == {"member-a", "member-b"}
    graph = SqlMemoryGraph(engine)
    hidden = await graph.walk(
        (("person", entity.id),), Walk(_UNIT, frozenset({"member-b"}))
    )
    assert not any(n.key == ("profile_line", str(sid)) for n in hidden.nodes)
    shown = await graph.walk(
        (("person", entity.id),), Walk(_UNIT, frozenset({"member-a", "member-b"}))
    )
    assert any(n.key == ("profile_line", str(sid)) for n in shown.nodes)


async def test_a_revision_replaces_and_a_retire_retires(engine):
    mid = await _said(engine)
    sid = await _line(engine, cite=mid)
    store = SqlProfileStore(engine, screen=lambda _t: None, index=True)
    revised = await store.profile_write(
        account=ACCOUNT,
        ops=[ReviseOp(line="P1", citations=(mid,), text="runs daily")],
        citable=ProfileCitable(
            messages={int(mid): Said(1000, "member-b", "g1")},
            listed_rooms=frozenset({"g1"}),
        ),
        now_ms=NOW + 1, written_by_session="tag", apply_decay=False,
    )
    assert revised.status == "ok", revised.refusals
    rows = await _abouts(engine)
    stale = [
        r for r in rows if r["src_id"] == str(sid) and r["status"] == "current"
    ]
    assert not stale
    successor = revised.written[0]
    assert any(
        r["src_id"] == str(successor) and r["status"] == "current" for r in rows
    )
    retired = await store.profile_write(
        account=ACCOUNT,
        ops=[RetireOp(line="P1", why="forget it")],
        citable=ProfileCitable(messages={}, listed_rooms=frozenset()),
        now_ms=NOW + 2, written_by_session="tag", apply_decay=False,
    )
    assert retired.status == "ok", retired.refusals
    assert not [
        r for r in await _abouts(engine) if r["status"] == "current"
    ]


async def test_an_event_revision_replaces_and_cancellation_retires(engine):
    first = await _said(engine, person="member-a", room="ga")
    store = SqlGatheringStore(engine)
    event = await store.create(
        _event(people=("member-a",)),
        evidence=EventEvidence(
            parts=frozenset({"what"}),
            citations=(EventCitation(part="what", store="message", ref=first),),
        ),
    )
    second = await _said(engine, person="member-b", room="ga")
    from dataclasses import replace as _replace

    event = await store.update(
        _replace(event, people=("member-b",)),
        evidence=EventEvidence(
            parts=frozenset({"people"}),
            citations=(EventCitation(part="people", store="message", ref=second),),
        ),
    )
    entity_a = pointer_entity("person", _UNIT, "member-a", "member-a")
    entity_b = pointer_entity("person", _UNIT, "member-b", "member-b")
    rows = await _abouts(engine)
    current = {r["dst_id"] for r in rows if r["status"] == "current"}
    assert entity_b.id in current
    assert entity_a.id not in current or any(
        r["dst_id"] == entity_a.id and r["status"] != "current" for r in rows
    )
    async with engine.connect() as conn:
        kept_cites = {
            (r.provenance, r.dst_id)
            for r in (
                await conn.execute(
                    sa.select(MEMORY_EDGE.c.provenance, MEMORY_EDGE.c.dst_id).where(
                        MEMORY_EDGE.c.src_kind == "event",
                        MEMORY_EDGE.c.src_id == str(event.id),
                        MEMORY_EDGE.c.rel == "cites",
                    )
                )
            )
        }
    # The restated part dropped its old citation; the unstated part kept its.
    assert ("event_citations.what", first) in kept_cites
    assert ("event_citations.people", second) in kept_cites
    assert ("event_citations.people", first) not in kept_cites
    await store.update(_replace(event, status=GatheringStatus.CANCELLED))
    assert not [
        r for r in await _abouts(engine) if r["status"] == "current"
    ]


async def test_the_backfill_projects_and_counts_them(engine):
    mid = await _said(engine)
    await _line(engine, cite=mid)
    await SqlCultureStore(engine).append(
        key=character_key(_UNIT, "member-b"), body="the dreamer",
        written_at=10, established_by="dreamer", citations=(mid,),
    )
    report = await graph_project(engine, scope=SCOPE, apply=True)
    about = report["relations"]["about"]
    assert about["graph"] >= 2
    assert about["memory_edge"] >= about["graph"]


async def _current_about(engine: Any, src_id: str, dst_id: str) -> bool:
    return any(
        r["src_id"] == src_id and r["dst_id"] == dst_id and r["status"] == "current"
        for r in await _abouts(engine)
    )


async def test_rebuild_and_retry_recreate_subject_relations(engine):
    """Retirement and recreation ride the ONE projection path every caller
    takes (`project_source` inside `project`): a rebuild and a queued retry
    leave the relations current, never retired-forgotten."""
    mid = await _said(engine)
    sid = await _line(engine, cite=mid)
    entity = pointer_entity("person", _UNIT, "member-b", "member-b")
    assert await _current_about(engine, str(sid), entity.id)
    async with engine.begin() as conn:
        await conn.execute(
            sa.update(RELATION)
            .where(RELATION.c.rel == "about")
            .values(status="retired")
        )
    await memory_index.rebuild(engine)
    assert await _current_about(engine, str(sid), entity.id)
    async with engine.begin() as conn:
        await conn.execute(
            sa.update(RELATION)
            .where(RELATION.c.rel == "about")
            .values(status="retired")
        )
        await conn.execute(
            sa.insert(MEMORY_PROJECTION_QUEUE).values(
                kind="profile_line", source_id=str(sid), last_error="t"
            )
        )
        await memory_index.drain_queue(conn)
    assert await _current_about(engine, str(sid), entity.id)


_STAY_START_MS = 1_780_000_000_000


async def _stay(engine: Any, *, person: str) -> str:
    """A stay node for `person`, the way the current build projects it."""
    async with engine.begin() as conn:
        build = (
            await conn.execute(
                sa.select(ROLLUP_BUILD.c.id).where(ROLLUP_BUILD.c.status == "current")
            )
        ).scalar()
        if build is None:
            build = (
                await conn.execute(
                    sa.insert(ROLLUP_BUILD)
                    .values(code_version="t", params_sha256="x", status="current")
                    .returning(ROLLUP_BUILD.c.id)
                )
            ).scalar_one()
        await conn.execute(
            sa.insert(PRESENCE_STAYS).values(
                subject_id=person, person_id=person, circle_id="c",
                started_ts=_STAY_START_MS, ended_ts=_STAY_START_MS + 3_600_000,
                lat=0.0, lng=0.0, radius_m=50.0, fix_count=3,
                build_id=build, lineage=[],
            )
        )
        key = episode_key(person, _STAY_START_MS, _STAY_START_MS + 3_600_000)
        await memory_index.project_episodes(conn, build, "stay", [key])
    return key


async def test_person_scoped_evidence_restricts_or_refuses_never_widens(engine):
    """A stay's node carries no unit or room; its eligibility is resolved the
    way the graph resolves Rollup scope — the member units of its persons.
    A Character citing member-b's message AND member-a's stay projects a
    relation restricted to {member-a, member-b}, never one a scope lacking
    member-a can see; a citation that establishes no eligibility refuses the
    relation outright, never drops the evidence and keeps it."""
    mid = await _said(engine, person="member-b", room="g1")
    stay = await _stay(engine, person="member-a")
    await _unit_proven(engine)
    store = SqlCultureStore(engine)
    await store.append(
        key=character_key(_UNIT, "member-b"), body="the dreamer",
        written_at=10, established_by="dreamer",
        citations=(mid, f"rollup(stay|{stay})"),
    )
    entity = pointer_entity("person", _UNIT, "member-b", "member-b")
    rows = [
        r for r in await _abouts(engine)
        if r["src_kind"] == "character" and r["status"] == "current"
    ]
    assert rows and {"member-a", "member-b"} <= set(rows[0]["persons"])
    graph = SqlMemoryGraph(engine)
    hidden = await graph.walk(
        (("person", entity.id),), Walk(_UNIT, frozenset({"member-b"}))
    )
    assert not any(n.key[0] == "character" for n in hidden.nodes)
    shown = await graph.walk(
        (("person", entity.id),),
        Walk(_UNIT, frozenset({"member-a", "member-b"})),
    )
    assert any(n.key[0] == "character" for n in shown.nodes)
    away = await _stay(engine, person="member-z")
    await store.append(
        key=character_key(_UNIT, "member-c"), body="elsewhere",
        written_at=11, established_by="dreamer",
        citations=(f"rollup(stay|{away})",),
    )
    assert not [
        r for r in await _abouts(engine)
        if r["src_id"] == character_key(_UNIT, "member-c")
        and r["status"] == "current"
    ]


async def test_event_citations_live_on_the_revision_and_survive_a_rebuild(engine):
    """`memory_edge` is derived — a restore leaves it empty for `nora memory
    rebuild` to refill — so an event write's citations are custody of the
    append-only `event_revision` it stamped them on, and `cites` edges are
    derived from the head revision's fold: wiping the derived table and
    rebuilding re-derives them; a write with no citations stores none and
    projects none."""
    mid = await _said(engine, person="member-a", room="ga")
    store = SqlGatheringStore(engine)
    event = await store.create(
        _event(people=("member-a",)),
        evidence=EventEvidence(
            parts=frozenset({"what"}),
            citations=(EventCitation(part="what", store="message", ref=mid),),
        ),
    )
    bare = await store.create(_event(people=("member-a",)))
    async with engine.connect() as conn:
        stored = (
            await conn.execute(
                sa.select(EVENT_REVISION.c.citations).where(
                    EVENT_REVISION.c.event_id == event.id
                )
            )
        ).scalar_one()
        bare_stored = (
            await conn.execute(
                sa.select(EVENT_REVISION.c.citations).where(
                    EVENT_REVISION.c.event_id == bare.id
                )
            )
        ).scalar_one()
    assert stored == {"what": [mid]}
    assert not bare_stored
    async with engine.begin() as conn:
        await conn.execute(
            sa.delete(MEMORY_EDGE).where(MEMORY_EDGE.c.rel == "cites")
        )
    await memory_index.rebuild(engine)
    async with engine.connect() as conn:
        cites = {
            (r.provenance, r.src_id, r.dst_id)
            for r in (
                await conn.execute(
                    sa.select(
                        MEMORY_EDGE.c.provenance,
                        MEMORY_EDGE.c.src_id,
                        MEMORY_EDGE.c.dst_id,
                    ).where(MEMORY_EDGE.c.rel == "cites")
                )
            )
        }
    assert ("event_citations.what", str(event.id), mid) in cites
    assert not [c for c in cites if c[1] == str(bare.id)]
