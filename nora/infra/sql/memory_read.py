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

**Three seeds** (65 V2). `dense` is the search; `exact` pins literal
references ahead of it; `seed`, the lexical seed, is the fallback the walk
uses only when there is no query vector.

- **`dense`, two stages in one statement.** The candidates are the
  `DENSE_CANDIDATES` nearest by the leading 256 dimensions, through
  `idx_memory_node_embedding_256` (the ORDER BY is that index's expression,
  with its constants written as literals so the planner can match it), with
  the scope filter and the model fingerprint INSIDE the same scan; those are
  then re-scored by cosine on the full 2048 and the best `limit` kept (65 §0,
  measured in #24). Two settings, local to the statement's transaction:
  `hnsw.iterative_scan = relaxed_order` (pgvector 0.8), because HNSW returns
  at most `hnsw.ef_search` rows BEFORE the filter, so a room whose unit holds
  few of the nearest vectors would otherwise get fewer than its 50, or none;
  the scan goes on until 50 rows pass (or pgvector's `hnsw.max_scan_tuples`,
  20,000 by default). And `hnsw.ef_search = HNSW_EF_SEARCH` (the constant
  says why). The re-score orders the candidates afresh, so the relaxed
  order of the first stage costs nothing.
- **`exact`**: a node containing a term literally (`nora.domain.memory_index.
  exact_holds`): an ASCII term on its own, as a regular expression with
  ASCII look-arounds; any other as a case-insensitive substring. Ranked by
  how many terms it holds, then by cosine to the query when a vector of the
  current model is given, then newest.
- **`seed`, lexical, is `record_query`'s** (`WORD_SIMILARITY_MIN`, the `<%`
  operator, containment ranked first): per term, a node matches when its
  text contains the term or pg_trgm's word similarity finds it at the
  threshold; its score is the mean over the terms of 1 for containment, else
  the word similarity, so a node holding every term ranks above one holding
  one. `idx_memory_node_text_trgm` serves both operators.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
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
    exact_pattern,
)
from nora.infra.sql.memory_index import embed_counts, index_status
from nora.infra.sql.record_query import WORD_SIMILARITY_MIN
from nora.infra.sql.schema import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_INDEXED_DIMENSIONS,
    MEMORY_EDGE,
    MEMORY_NODE,
)

__all__ = ["DENSE_CANDIDATES", "HNSW_EF_SEARCH", "SqlMemoryIndex"]

#: The first stage's candidates, re-scored on the full 2048 (65 §0: the
#: bake-off's k, at which no query lost an item against an exact 2048 search).
DENSE_CANDIDATES = 50
#: HNSW's candidate list per scan. It must be at least `DENSE_CANDIDATES`: a
#: scan returns at most this many rows before the iterative scan resumes it.
#: The bake-off measured 50 over 460 items; recall at a fixed list falls as
#: the graph grows, and the live index is about a hundred times larger, so
#: this is twice the candidates, the margin pgvector's own docs show. The
#: cost is one HNSW scan of a 100-entry list, about linear in this number;
#: 65 §3 measures both at the real size after the backfill.
HNSW_EF_SEARCH = 100

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


def _leading(column: Any) -> Any:
    """`(subvector(embedding, 1, 256))::halfvec(256)`, written with literal
    constants: `idx_memory_node_embedding_256`'s own expression, which the
    planner matches only when no part of it is a bind parameter."""
    return sa.cast(
        sa.func.subvector(
            column, sa.literal_column("1"),
            sa.literal_column(str(EMBEDDING_INDEXED_DIMENSIONS)),
        ),
        HALFVEC(EMBEDDING_INDEXED_DIMENSIONS),
    )


def _vector(values: Sequence[float], width: int) -> Any:
    return sa.cast(sa.bindparam(None, list(values), type_=HALFVEC(width)), HALFVEC(width))


def _dense_statement(
    vector: Sequence[float], scope: MemoryScope, *, model: str, limit: int,
) -> sa.Select[Any]:
    """`SqlMemoryIndex.dense`'s query (the module docstring's two stages),
    apart so a test can ask the planner about it."""
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"a query vector of {len(vector)}, not {EMBEDDING_DIMENSIONS}")
    n = MEMORY_NODE.alias("n")
    near = _leading(n.c.embedding).cosine_distance(
        _vector(vector[:EMBEDDING_INDEXED_DIMENSIONS], EMBEDDING_INDEXED_DIMENSIONS))
    candidates = (
        sa.select(*_columns(n), n.c.embedding)
        .where(n.c.embedding_model == model, _in_scope(n, scope))
        .order_by(near)
        .limit(DENSE_CANDIDATES)
        .subquery("c")
    )
    score = (1 - candidates.c.embedding.cosine_distance(
        _vector(vector, EMBEDDING_DIMENSIONS))).label("score")
    return (
        sa.select(*[candidates.c[c.name] for c in _columns(n)], score)
        .order_by(score.desc(), candidates.c.at_ms.desc().nulls_last(),
                  candidates.c.kind, candidates.c.source_id)
        .limit(limit)
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

    async def dense(
        self, vector: Sequence[float], scope: MemoryScope, *, model: str, limit: int,
    ) -> list[Seed]:
        if limit <= 0:
            return []
        stmt = _dense_statement(vector, scope, model=model, limit=limit)
        async with self._engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            await conn.execute(sa.text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))
            rows = (await conn.execute(stmt)).all()
        return [Seed(_node(r), float(r.score)) for r in rows]

    async def exact(
        self, terms: Sequence[str], scope: MemoryScope, *, limit: int,
        vector: Sequence[float] | None = None, model: str | None = None,
    ) -> list[Seed]:
        wanted = [t for t in dict.fromkeys(t.strip() for t in terms) if t]
        if not wanted or limit <= 0:
            return []
        n = MEMORY_NODE.alias("n")
        matches = n.c.text.op("~*", is_comparison=True)
        held = [
            matches(sa.bindparam(None, exact_pattern(t), type_=sa.Text)) if t.isascii()
            else sa.func.strpos(
                sa.func.lower(n.c.text), sa.func.lower(sa.bindparam(None, t, type_=sa.Text)),
            ) > 0
            for t in wanted
        ]
        count = sum((sa.case((h, 1), else_=0) for h in held), sa.literal(0)).label("held")
        order: list[Any] = [count.desc()]
        if vector is not None and model is not None:
            order.append(sa.case(
                (n.c.embedding_model == model,
                 n.c.embedding.cosine_distance(_vector(vector, EMBEDDING_DIMENSIONS))),
                else_=None,
            ).asc().nulls_last())
        stmt = (
            sa.select(*_columns(n), count)
            .where(sa.or_(*held), _in_scope(n, scope))
            .order_by(*order, n.c.at_ms.desc().nulls_last(), n.c.kind, n.c.source_id)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [Seed(_node(r), 1.0) for r in rows]

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

    async def index_state(self, *, model: str | None = None) -> IndexState:
        async with self._engine.connect() as conn:
            s = await index_status(conn)
            if model is None:
                return IndexState(status=s.status, queue=s.queue)
            # ponytail: one count over the node table per search; a counter
            # row kept by the worker if it ever shows in a turn's latency.
            c = await embed_counts(conn, model)
        return IndexState(
            status=s.status, queue=s.queue, embedded=c.embedded,
            unembedded=c.pending - c.given_up,
        )
