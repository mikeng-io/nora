"""`SqlMemoryIndex`, the memory index's read (design/unified-memory/
25-memory-graph.md §4, 60-build-step-3.md S5). `nora.infra.sql.memory_index`
is the one writer; this is the one reader, and it writes nothing.

**Scope is applied inside every statement, on every node it returns** (25 §3,
§4 step 3): the seed scan, the `about` seeds and each hop's join to
`memory_node`. A node out of scope is never read into the process, so a path
is cut at its first out-of-scope node by construction: the walk
(`nora.agent.memory_search`) can only step onto what a read returned. The
filter, for a node `n` (`_in_scope`, and `nora.domain.memory_index.in_scope`
in Python, which the contract suite holds to the same answers):

    n.state = 'live'
    AND n.kind NOT IN (hubs)
    AND (n.unit = ANY($units) OR n.origin_room = ANY($rooms)
         OR (n.unit IS NULL AND n.origin_room IS NULL AND n.kind IN ('stay', 'trip')))
    AND n.persons <@ $listed_here
    AND (cardinality(n.exclude_persons) = 0
         OR (NOT $exclude_strict AND NOT n.exclude_persons && $people_here))
    AND (n.kind NOT IN ('outing', 'stay', 'trip') OR n.persons <@ $presence_persons)

**A hub carries no scope** (a person, room, unit, Nora or a venue's place):
`neighbours` returns one only as a direct neighbour of an in-scope frontier
node, and no read seeds from one. Keeping a hub off the next frontier (the
hub rule) is the walk's, so a person node is never a bridge from one message
to everything said by or about that person.

**A dangling edge drops itself**: every hop joins `memory_node` on both the
edge's kind and id, so an edge into a hidden message or a switched-out
episode reaches nothing (0105, S4).

**Lexical seeding is `record_query`'s** (`WORD_SIMILARITY_MIN`, the `<%`
operator, containment ranked first): per term, a node matches when its text
contains the term or pg_trgm's word similarity finds it at the threshold;
its score is the mean over the terms of 1 for containment, else the word
similarity, so a node holding every term ranks above one holding one.
`idx_memory_node_text_trgm` serves both operators.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.memory_index import (
    EPISODE_KINDS,
    HUB_KINDS,
    PERSON_SCOPED_KINDS,
    IndexedNode,
    IndexState,
    MemoryScope,
    Neighbour,
    Seed,
)
from nora.infra.sql.memory_index import index_status
from nora.infra.sql.record_query import WORD_SIMILARITY_MIN
from nora.infra.sql.schema import MEMORY_EDGE, MEMORY_NODE

__all__ = ["SqlMemoryIndex"]

_TEXTS = pg.ARRAY(sa.Text)


def _array(values: Sequence[str] | frozenset[str]) -> Any:
    """One array parameter (`_among`'s reason: never one bind per value)."""
    return sa.cast(sa.bindparam(None, sorted(values), type_=_TEXTS), _TEXTS)


def _in_scope(n: Any, scope: MemoryScope) -> sa.ColumnElement[bool]:
    """The module docstring's filter, for the `memory_node` alias `n`."""
    excluded_ok = sa.or_(
        sa.func.cardinality(n.c.exclude_persons) == 0,
        sa.and_(
            sa.literal(not scope.exclude_strict),
            ~n.c.exclude_persons.overlap(_array(scope.people_here)),
        ),
    )
    return sa.and_(
        n.c.state == "live",
        n.c.kind.not_in(HUB_KINDS),
        sa.or_(
            n.c.unit == sa.any_(_array(scope.units)),
            n.c.origin_room == sa.any_(_array(scope.rooms)),
            sa.and_(
                n.c.unit.is_(None), n.c.origin_room.is_(None),
                n.c.kind.in_(PERSON_SCOPED_KINDS),
            ),
        ),
        n.c.persons.contained_by(_array(scope.listed_here)),
        excluded_ok,
        sa.or_(
            n.c.kind.not_in(EPISODE_KINDS),
            n.c.persons.contained_by(_array(scope.presence_persons)),
        ),
    )


def _node(r: Any) -> IndexedNode:
    return IndexedNode(
        kind=r.kind, source_id=r.source_id, text=r.text or "", at_ms=r.at_ms,
        unit=r.unit, origin_room=r.origin_room, persons=tuple(r.persons or ()),
        exclude_persons=tuple(r.exclude_persons or ()), lineage=r.lineage,
        sha256=r.sha256, state=r.state,
    )


def _columns(n: Any) -> list[Any]:
    return [
        n.c.kind, n.c.source_id, n.c.text, n.c.at_ms, n.c.unit, n.c.origin_room,
        n.c.persons, n.c.exclude_persons, n.c.lineage, n.c.sha256, n.c.state,
    ]


class SqlMemoryIndex:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def seed(
        self, terms: Sequence[str], scope: MemoryScope, *, limit: int,
    ) -> list[Seed]:
        wanted = [t for t in dict.fromkeys(t.strip() for t in terms) if t]
        if not wanted or limit <= 0:
            return []
        n = MEMORY_NODE.alias("n")
        t = sa.func.unnest(_array(wanted)).table_valued("term").render_derived(name="t")
        contains = sa.func.strpos(sa.func.lower(n.c.text), sa.func.lower(t.c.term)) > 0
        per_term = sa.func.greatest(
            sa.case((contains, 1.0), else_=0.0),
            sa.func.word_similarity(t.c.term, n.c.text),
        )
        score = (sa.func.sum(per_term) / len(wanted)).label("score")
        stmt = (
            sa.select(*_columns(n), score)
            .select_from(n.join(t, sa.true()))
            .where(sa.or_(contains, t.c.term.op("<%")(n.c.text)), _in_scope(n, scope))
            .group_by(n.c.id)
            .order_by(score.desc(), n.c.at_ms.desc().nulls_last(), n.c.id)
            .limit(limit)
        )
        async with self._engine.begin() as conn:
            await conn.execute(sa.text(
                f"SET LOCAL pg_trgm.word_similarity_threshold = {WORD_SIMILARITY_MIN}"
            ))
            rows = (await conn.execute(stmt)).all()
        return [Seed(_node(r), float(r.score)) for r in rows]

    async def about(
        self, person_id: str, scope: MemoryScope, *, limit: int,
    ) -> list[IndexedNode]:
        if limit <= 0:
            return []
        n = MEMORY_NODE.alias("n")
        e = MEMORY_EDGE
        stmt = (
            sa.select(*_columns(n))
            .select_from(n.join(e, sa.and_(
                e.c.src_kind == n.c.kind, e.c.src_id == n.c.source_id,
            )))
            .where(
                e.c.rel == "about", e.c.dst_kind == "person", e.c.dst_id == person_id,
                _in_scope(n, scope),
            )
            .order_by(n.c.at_ms.desc().nulls_last(), n.c.id)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_node(r) for r in rows]

    async def neighbours(
        self, frontier: Sequence[tuple[str, str]], scope: MemoryScope,
    ) -> list[Neighbour]:
        if not frontier:
            return []
        f = sa.func.unnest(
            sa.cast(sa.bindparam(None, [k for k, _ in frontier], type_=_TEXTS), _TEXTS),
            sa.cast(sa.bindparam(None, [i for _, i in frontier], type_=_TEXTS), _TEXTS),
        ).table_valued("kind", "sid").render_derived(name="f")
        e = MEMORY_EDGE
        out: list[Neighbour] = []
        async with self._engine.connect() as conn:
            for outgoing in (True, False):
                n = MEMORY_NODE.alias("n")
                near_kind, near_id = (e.c.src_kind, e.c.src_id) if outgoing else (
                    e.c.dst_kind, e.c.dst_id)
                far_kind, far_id = (e.c.dst_kind, e.c.dst_id) if outgoing else (
                    e.c.src_kind, e.c.src_id)
                stmt = (
                    sa.select(f.c.kind.label("via_kind"), f.c.sid.label("via_id"),
                              e.c.rel, *_columns(n))
                    .select_from(
                        f.join(e, sa.and_(near_kind == f.c.kind, near_id == f.c.sid))
                        .join(n, sa.and_(n.c.kind == far_kind, n.c.source_id == far_id))
                    )
                    .where(sa.or_(n.c.kind.in_(HUB_KINDS), _in_scope(n, scope)))
                    .order_by(f.c.kind, f.c.sid, e.c.rel, n.c.kind, n.c.source_id)
                )
                out += [
                    Neighbour(
                        via=(r.via_kind, r.via_id), rel=r.rel, outgoing=outgoing,
                        node=_node(r),
                    )
                    for r in await conn.execute(stmt)
                ]
        return out

    async def index_state(self) -> IndexState:
        async with self._engine.connect() as conn:
            s = await index_status(conn)
        return IndexState(status=s.status, queue=s.queue)
