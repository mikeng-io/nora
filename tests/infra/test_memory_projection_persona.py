"""The Persona store's projection into the memory index (45 §1, the switch):
with `index` on (`[persona] render`), every write keeps its lines' nodes and
edges in the write's own transaction — a line's scope its account key's, its
citations `cites`, its premises `draws_on`, its chain `supersedes`, and a
hidden line («forget that», 35 §2) out of the index entirely. With `index`
off (the shadow) a write lands no node; the shadow's lines join through the
flip's `nora memory rebuild`, which writes exactly what live projection did.

Module-scoped database per `test_memory_projection_notebook.py`'s pattern
(one migrated database, truncated between tests)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.culture import character_key, unit_culture_key
from nora.domain.models import NormalizedMessage
from nora.domain.persona import (
    Account,
    AddOp,
    PersonaCitable,
    RetireOp,
    Said,
)
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.persona import SqlPersonaStore
from nora.infra.sql.schema import MEMORY_EDGE, MEMORY_NODE, PERSONA_LINE

pytestmark = pytest.mark.asyncio

_ROOM = "signal:g1"
_UNIT = "unit-a"
NOW = 1_790_000_000_000
ACCOUNT = Account(
    key=character_key(_UNIT, "member-b"),
    entity_kind="human",
    unit=_UNIT,
    subject="member-b",
)


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_memory_persona_" + uuid4().hex
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
                "messages", "persona", "persona_line", "persona_dispute",
                "culture", "culture_revision", "row_history", "memory_node",
                "memory_edge", "memory_projection_queue",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


def _no_offence(_text: str) -> None:
    return None


def _store(engine: Any, *, index: bool = True) -> SqlPersonaStore:
    return SqlPersonaStore(engine, screen=_no_offence, index=index)


async def _said(engine: Any, body: str = "i cycle to work") -> str:
    mid = await SqlRecordStore(engine).append(
        message=NormalizedMessage(
            "signal", "g1", "group", "+10000000002", "Member B", 1000, body, {},
        ),
        person_id="member-b",
    )
    return str(mid)


async def _unit_proven(engine: Any) -> None:
    """`_project_persona_line` indexes a person account only when the Record
    and the stores PROVE its owner is a unit (`_is_a_unit`); the unit's own
    culture account is that proof."""
    await SqlCultureStore(engine).append(
        key=unit_culture_key(_UNIT), body="the unit", written_at=10,
        established_by="dreamer",
    )


def _citable(mid: str, **kw: Any) -> PersonaCitable:
    return PersonaCitable(
        messages={int(mid): Said(1000, "member-b", "g1")},
        listed_rooms=frozenset({"g1"}), **kw,
    )


def _told(text: str, cite: str) -> AddOp:
    return AddOp(
        part="identity",
        kind="attribute",
        text=text,
        mode="told",
        citations=(cite,),
        told_by="member-b",
        fact_key="occupation",
        fact_value=text,
    )


async def _nodes(engine: Any) -> dict[tuple[str, str], Any]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(MEMORY_NODE))).all()
    return {(r.kind, r.source_id): r for r in rows}


async def _edges(engine: Any, sid: str) -> set[tuple[str, str, str, str, str]]:
    """Every edge into or out of the persona line `sid`."""
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id, MEMORY_EDGE.c.rel,
            MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id,
        ).where(sa.or_(
            sa.and_(MEMORY_EDGE.c.src_kind == "persona_line",
                    MEMORY_EDGE.c.src_id == sid),
            sa.and_(MEMORY_EDGE.c.dst_kind == "persona_line",
                    MEMORY_EDGE.c.dst_id == sid),
        )))).all()
    return {tuple(r) for r in rows}


async def test_a_written_line_is_a_node_in_its_accounts_scope(engine):
    mid = await _said(engine)
    await _unit_proven(engine)
    store = _store(engine)
    result = await store.persona_write(
        account=ACCOUNT, ops=[_told("cycles to work", mid)], citable=_citable(mid),
        now_ms=NOW, written_by_session="tag", apply_decay=False,
    )
    assert result.status == "ok"
    sid = result.written[0]
    node = (await _nodes(engine))[("persona_line", sid)]
    assert (node.unit, node.origin_room, node.persons, node.state) == (
        _UNIT, None, ["member-b"], "live",
    )
    assert (node.text, node.at_ms) == (
        "occupation: cycles to work (asserted 1970-01-01T00:00:01+00:00)", 1000)
    assert await _edges(engine, sid) == {
        ("persona_line", sid, "about", "person", "member-b"),
        ("persona_line", sid, "cites", "message", mid),
    }


async def test_a_retired_line_supersedes_its_old_node(engine):
    mid = await _said(engine)
    await _unit_proven(engine)
    store = _store(engine)
    first = await store.persona_write(
        account=ACCOUNT, ops=[_told("cycles to work", mid)], citable=_citable(mid),
        now_ms=NOW, written_by_session="tag", apply_decay=False,
    )
    old = first.written[0]
    retired = await store.persona_write(
        account=ACCOUNT, ops=[RetireOp(line="P1", why="said otherwise")],
        citable=_citable(mid), now_ms=NOW + 1, written_by_session="persona",
        apply_decay=True, expected_revision=1,
    )
    assert retired.status == "ok"
    nodes = await _nodes(engine)
    assert nodes[("persona_line", old)].state == "superseded"
    assert nodes[("persona_line", retired.written[0])].state == "retired"
    assert ("persona_line", retired.written[0], "supersedes", "persona_line", old) in (
        await _edges(engine, old)
    )


async def test_a_forget_dispute_drops_the_line_and_its_successor(engine):
    """«Forget that» from the subject hides the line (35 §2): the hidden row
    has no node, and neither has the row it supersedes — a forget leaves the
    index, not only the account."""
    mid = await _said(engine)
    await _unit_proven(engine)
    store = _store(engine)
    written = await store.persona_write(
        account=ACCOUNT, ops=[_told("cycles to work", mid)], citable=_citable(mid),
        now_ms=NOW, written_by_session="tag", apply_decay=False,
    )
    old = written.written[0]
    assert ("persona_line", old) in await _nodes(engine)
    result = await store.record_dispute(
        account=ACCOUNT, disputed_by="member-b", claim="cycles to work",
        kind="forget", message_id=int(mid), now_ms=NOW + 1,
    )
    assert (result.status, result.effect) == ("ok", "hidden")
    nodes = await _nodes(engine)
    assert ("persona_line", old) not in nodes
    assert not [k for k in nodes if k[0] == "persona_line"]
    assert await _edges(engine, old) == set()


async def test_another_members_dispute_marks_the_line_but_keeps_it(engine):
    mid = await _said(engine)
    await _unit_proven(engine)
    store = _store(engine)
    written = await store.persona_write(
        account=ACCOUNT, ops=[_told("cycles to work", mid)], citable=_citable(mid),
        now_ms=NOW, written_by_session="tag", apply_decay=False,
    )
    old = written.written[0]
    result = await store.record_dispute(
        account=ACCOUNT, disputed_by="member-a", claim="cycles to work",
        kind="dispute", message_id=int(mid), now_ms=NOW + 1,
    )
    assert (result.status, result.effect) == ("ok", "disputed")
    nodes = await _nodes(engine)
    # The old row's node is superseded; the disputed head still renders, so
    # its node is live.
    assert nodes[("persona_line", old)].state == "superseded"
    async with engine.connect() as conn:
        head = (await conn.execute(sa.select(PERSONA_LINE.c.id).where(
            PERSONA_LINE.c.supersedes == old))).scalar_one()
    assert nodes[("persona_line", head)].state == "live"


async def test_a_deduced_line_draws_on_its_premises_and_cites_a_social_item(engine):
    mid = await _said(engine)
    await _unit_proven(engine)
    store = _store(engine)
    told = await store.persona_write(
        account=ACCOUNT, ops=[_told("cycles to work", mid)], citable=_citable(mid),
        now_ms=NOW, written_by_session="tag", apply_decay=False,
    )
    premise = told.written[0]
    citable = _citable(
        mid, social={"instagram:post1": Said(2000, "member-b", None)},
    )
    deduced = await store.persona_write(
        account=ACCOUNT,
        ops=[
            AddOp(
                part="identity",
                kind="role",
                text="commutes by bike",
                mode="deduced",
                citations=(mid, "social:instagram:post1"),
                draws_on=("P1",),
                fact_key="role",
                fact_value="commutes by bike",
            )
        ],
        citable=citable,
        now_ms=NOW + 1,
        written_by_session="persona",
        apply_decay=True,
        expected_revision=1,
    )
    assert deduced.status == "ok"
    sid = deduced.written[0]
    assert await _edges(engine, sid) == {
        ("persona_line", sid, "about", "person", "member-b"),
        ("persona_line", sid, "cites", "message", mid),
        ("persona_line", sid, "cites", "social", "instagram:post1"),
        ("persona_line", sid, "draws_on", "persona_line", premise),
    }


async def test_a_room_accounts_line_scopes_to_its_room(engine):
    mid = await _said(engine)
    store = _store(engine)
    room_account = Account(key="g1", entity_kind="room", unit=_UNIT)
    result = await store.persona_write(
        account=room_account,
        ops=[
            AddOp(
                part="identity",
                kind="norm",
                text="plans in the open",
                mode="told",
                citations=(mid,),
                told_by="member-b",
                fact_key="norm",
                fact_value="plans in the open",
            )
        ],
        citable=PersonaCitable(messages={int(mid): Said(1000, "member-b", "g1")}),
        now_ms=NOW,
        written_by_session="persona-room",
        apply_decay=True,
    )
    assert result.status == "ok"
    sid = result.written[0]
    node = (await _nodes(engine))[("persona_line", sid)]
    assert (node.unit, node.origin_room, node.persons) == (None, _ROOM, [])
    assert ("persona_line", sid, "about", "room", _ROOM) in await _edges(engine, sid)


async def test_the_shadow_writes_no_node(engine):
    """`index` off (the shadow): the write lands, no node does."""
    mid = await _said(engine)
    await _unit_proven(engine)
    store = _store(engine, index=False)
    result = await store.persona_write(
        account=ACCOUNT, ops=[_told("cycles to work", mid)], citable=_citable(mid),
        now_ms=NOW, written_by_session="tag", apply_decay=False,
    )
    assert result.status == "ok"
    assert not [k for k in await _nodes(engine) if k[0] == "persona_line"]


async def test_a_rebuild_equals_live_projection(engine):
    """The flip's backfill: a rebuild from empty writes exactly what the live
    writes projected — including the drop of a hidden line's chain."""
    mid = await _said(engine)
    await _unit_proven(engine)
    store = _store(engine)
    await store.persona_write(
        account=ACCOUNT, ops=[_told("cycles to work", mid)], citable=_citable(mid),
        now_ms=NOW, written_by_session="tag", apply_decay=False,
    )
    kept = await store.persona_write(
        account=ACCOUNT,
        ops=[replace(_told("runs on sundays", mid), kind="role", fact_key="role")],
        citable=_citable(mid), now_ms=NOW + 1, written_by_session="tag",
        apply_decay=False,
    )
    await store.record_dispute(
        account=ACCOUNT, disputed_by="member-b", claim="cycles to work",
        kind="forget", message_id=int(mid), now_ms=NOW + 2,
    )
    live_nodes = {k: (n.text, n.state, n.unit, n.origin_room, n.persons)
                  for k, n in (await _nodes(engine)).items()}
    live_edges = await _edges(engine, kept.written[0])
    async with engine.begin() as conn:
        await conn.execute(sa.delete(MEMORY_EDGE))
        await conn.execute(sa.delete(MEMORY_NODE))
    await memory_index.rebuild(engine)
    rebuilt = {k: (n.text, n.state, n.unit, n.origin_room, n.persons)
               for k, n in (await _nodes(engine)).items()}
    assert rebuilt == live_nodes
    assert await _edges(engine, kept.written[0]) == live_edges


async def test_relationship_projection_retrieval_preserves_scoped_targets(engine):
    from datetime import UTC, datetime

    from nora.agent.memory_search import Hit, Step, render_packet
    from nora.domain.memory_index import MemoryFilter, MemoryScope
    from nora.infra.sql.memory_read import SqlMemoryIndex

    mid = await _said(engine)
    await _unit_proven(engine)
    store = _store(engine)
    ids = []
    for target in ("target-alice", "target-beth", "private-target"):
        result = await store.persona_write(
            account=ACCOUNT,
            ops=[replace(_told("untrusted prose", mid), kind="relationship",
                         fact_key="relationship",
                         fact_value={"relation": "sister", "person_id": target})],
            citable=_citable(mid), now_ms=NOW, written_by_session="test",
            apply_decay=False,
        )
        assert result.status == "ok"
        ids.extend(result.written)
    scope = MemoryScope(
        units=(_UNIT,), rooms=(_ROOM,),
        listed_here=frozenset({"member-b", "target-alice", "target-beth"}),
        people_here=frozenset({"member-b"}), exclude_strict=False,
        presence_persons=frozenset(), social_off=frozenset(), persona_render=True,
    )
    index = SqlMemoryIndex(engine)
    names = {"target-alice": "Alice", "target-beth": "Beth", "private-target": "Secret"}

    def packet(nodes, name_of=lambda pid: names.get(pid, "someone")):
        return render_packet(
            [Hit(n, 1, (Step("persona_line", how="about"),)) for n in nodes],
            name_of=name_of, room_label=lambda _: "Friends",
            now=datetime.fromtimestamp(NOW / 1000, UTC),
        )[0]

    for target, sid, name in zip(
        ("target-alice", "target-beth"), ids, ("Alice", "Beth"), strict=False,
    ):
        found = await index.about(target, scope, limit=10)
        assert [n.source_id for n in found] == [sid]
        assert f"sister: {name}" in packet(found)[0]["text"]
        # The regular SQL person filter and lexical path agree with the edge read.
        seeds = await index.seed(
            ["sister"], replace(scope, only=MemoryFilter(person=target)), limit=10,
        )
        assert [s.node.source_id for s in seeds] == [sid]
        assert f"sister: {name}" in packet([s.node for s in seeds])[0]["text"]
    all_nodes = [s.node for s in await index.seed(["sister"], scope, limit=10)]
    shown = str(packet(all_nodes))
    assert "Alice" in shown and "Beth" in shown
    assert "Secret" not in shown and "unknown" in shown
    assert all(target not in shown for target in names)
    assert all("sister: someone (unknown)" in item["text"]
               for item in packet(all_nodes, name_of=lambda _: "someone"))
    unknown = packet(all_nodes, name_of=str)
    assert all("sister: someone (unknown)" in item["text"] for item in unknown)
    assert all(target not in str(unknown) for target in names)
    assert "untrusted prose" not in shown
    assert not await index.about("private-target", scope, limit=10)


async def test_fallback_projection_and_hydration_share_structured_value_and_assertion(engine):
    from datetime import UTC, datetime

    from nora.agent.memory_search import Hit, Step, render_packet
    from nora.domain.memory_index import MemoryScope
    from nora.infra.sql.memory_read import SqlMemoryIndex

    mid = await _said(engine)
    await _unit_proven(engine)
    value = {"name": "height", "value": 178, "unit": "cm"}
    result = await _store(engine).persona_write(
        account=ACCOUNT, ops=[replace(_told("conflicting prose", mid),
                                    fact_key="attribute", fact_value=value)],
        citable=_citable(mid), now_ms=NOW, written_by_session="test", apply_decay=False,
    )
    assert result.status == "ok"
    scope = MemoryScope(units=(_UNIT,), rooms=(_ROOM,),
        listed_here=frozenset({"member-b"}), people_here=frozenset({"member-b"}),
        exclude_strict=False, presence_persons=frozenset(), social_off=frozenset(),
        persona_render=True)
    index = SqlMemoryIndex(engine)
    [node] = await index.about("member-b", scope, limit=10)
    assert node.identity_fact == {"key": "attribute", "value": value,
        "as_of": _citable(mid).messages[int(mid)].ts, "valid_from": None, "valid_until": None}
    packet, _ = render_packet([Hit(node, 1, (Step("persona_line", how="about"),))],
        name_of=lambda _: "Kit", room_label=lambda _: "Friends",
        now=datetime.fromtimestamp(NOW / 1000, UTC))
    assert "height: 178 cm (asserted " in packet[0]["text"]
    assert "conflicting prose" not in packet[0]["text"]
    live = {k: n.text for k, n in (await _nodes(engine)).items()}
    await memory_index.rebuild(engine)
    assert {k: n.text for k, n in (await _nodes(engine)).items()} == live
