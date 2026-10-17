"""`SqlMemoryIndex`, the memory index's read. `nora.infra.sql.memory_index`
is the one writer; this is the one reader, and it writes nothing.

**Scope is applied inside every statement, on every node it returns**: the
seed scan, the `about` seeds and each hop's join to
`memory_node`. A node out of scope is never read into the process, so a path
is cut at its first out-of-scope node by construction: the walk
(`nora.agent.memory_search`) can only step onto what a read returned. The
filter, for a node `n` (`_in_scope`, and `nora.domain.memory_index.in_scope`
in Python, which the contract suite holds to the same answers):

    n.state = 'live'
    AND n.kind NOT IN (hubs)
    AND (n.unit = ANY($units) OR n.origin_room = ANY($rooms)
         OR (n.unit IS NULL AND n.origin_room IS NULL
             AND n.kind IN ('stay', 'trip', 'social', 'social_media',
                            'social_location')))
    AND n.persons <@ $listed_here
    AND (cardinality(n.exclude_persons) = 0
         OR (NOT $exclude_strict AND NOT n.exclude_persons && $people_here))
    AND (n.kind NOT IN ('outing', 'stay', 'trip', 'social_location')
         OR n.persons <@ $presence_persons)
    AND (n.kind NOT IN ('social', 'social_media', 'social_location')
         OR NOT n.persons && $social_off)

The presence line is `PRESENCE_KINDS`: an episode, and the place a social
item is tagged with, which is a node of its own so that this line can gate
it while the item stays readable by its caption. The last line is a
person's `social = false` (`MemoryScope.social_off`), read per read: their
social items are out of every read, including those recorded before the
switch, and the projector never needs to know.

**The question's narrowing rides on the same filter** (`MemoryScope.only`,
`memory_search`'s `kinds`, `since`, `until` and `person`; `_narrowed`, and
`nora.domain.memory_index.narrowed` in Python): ANDed onto it, so it is
inside whichever read is handed it, never a cut of a top k. A node outside
it is not read, so it takes no slot and bridges nothing. The caller decides
which reads carry `kinds`: `nora.agent.memory_search` hands it to the seeds
only, and runs the hops on the same scope without it. A person narrows to nodes with an
`about`, `authored_by` or `shared_by` edge to them (an `EXISTS` on the
edge's primary key) or listing them in `persons`, said in a room that lists
them or in no room.

**A hub carries no scope** (a person, room, unit, Nora or a place):
`neighbours` returns one only as a direct neighbour of an in-scope frontier
node, and no read seeds from one. Keeping a hub off the next frontier (the
hub rule) is the walk's, so a person node is never a bridge from one message
to everything said by or about that person. `places` returns the place hubs
a question names, which the walk anchors on and never returns (step 3a,
`nora.agent.memory_search._anchors`); every node reached from one is read
through the scope like any other.

**A dangling edge drops itself**: every hop joins `memory_node` on both the
edge's kind and id, so an edge into a hidden message or a switched-out
episode reaches nothing (0105).

**Three seeds.** `dense` is the search; `exact` pins literal
references ahead of it; `seed`, the lexical seed, is the fallback the walk
uses when there is no query vector, and beside `dense` it reads only the
nodes no vector reaches yet.

- **`dense`, two stages in one statement.** The candidates are the
  `DENSE_CANDIDATES` nearest by the leading 256 dimensions, through
  `idx_memory_node_embedding_256` (the ORDER BY is that index's expression,
  with its constants written as literals so the planner can match it), with
  the scope filter and the model fingerprint INSIDE the same scan; those are
  then re-scored by cosine on the full 2048 and the best `limit` kept. Two
  settings, local to the statement's transaction:
  `hnsw.iterative_scan = relaxed_order` (pgvector 0.8), because HNSW returns
  at most `hnsw.ef_search` rows BEFORE the filter, so a room whose unit holds
  few of the nearest vectors would otherwise get fewer than its 50, or none;
  the scan goes on until 50 rows pass or it has visited
  `hnsw.max_scan_tuples = HNSW_MAX_SCAN_TUPLES` tuples. And `hnsw.ef_search
  = HNSW_EF_SEARCH`. The constants say why. The re-score orders the
  candidates afresh, so the relaxed order of the first stage costs nothing.
- **`exact`**: a node containing a term literally (`nora.domain.memory_index.
  exact_holds`): a Latin term on its own, as a regular expression with
  look-arounds (`~`, or `~*` for a term matched in any case); any other as a
  substring, `LIKE` (or `ILIKE`). Both forms are served by
  `idx_memory_node_text_trgm`, so the OR of the terms is a bitmap OR of
  index scans, never a scan of the table. Ranked by how many terms it holds,
  then by cosine to the query when a vector of the current model is given
  (returned as the seed's `near`), then newest.
- **`seed`, lexical, is `record_query`'s** (`WORD_SIMILARITY_MIN`, the `<%`
  operator, containment ranked first): per term, a node matches when its
  text contains the term or pg_trgm's word similarity finds it at the
  threshold; its score is the mean over the terms of 1 for containment, else
  the word similarity, so a node holding every term ranks above one holding
  one. `idx_memory_node_text_trgm` serves both operators. With
  `unembedded_of` it reads only the embeddable nodes holding no vector of
  that model, waiting for one or given up on, which the dense seed cannot
  reach: a line said a moment ago is found by its words before the worker's
  next tick embeds it.
- **`index_state`** counts the vector coverage within the search's scope
  (`_in_scope`), so a node waiting in another unit does not hold this
  room's answers `partial`; with `fresh_seconds`, how many of the waiting
  nodes with a text have owed their vector for less (`IndexState.fresh`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.memory_index import (
    EMBEDDED_KINDS,
    HUB_KINDS,
    PERSON_RELATIONS,
    PERSON_SCOPED_KINDS,
    PRESENCE_KINDS,
    SOCIAL_KINDS,
    ExactTerm,
    IndexedNode,
    IndexState,
    MemoryScope,
    Neighbour,
    Seed,
    exact_pattern,
    is_latin,
    place_named,
)
from nora.infra.sql.memory_index import embed_counts, index_status
from nora.infra.sql.record_query import WORD_SIMILARITY_MIN
from nora.infra.sql.schema import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_INDEXED_DIMENSIONS,
    MEMORY_EDGE,
    MEMORY_NODE,
)

__all__ = ["DENSE_CANDIDATES", "HNSW_EF_SEARCH", "HNSW_MAX_SCAN_TUPLES", "SqlMemoryIndex"]

#: The first stage's candidates, re-scored on the full 2048: the k at which no
#: bake-off query lost an item against an exact 2048 search.
DENSE_CANDIDATES = 50
#: HNSW's candidate list per scan. It must be at least `DENSE_CANDIDATES`: a
#: scan returns at most this many rows before the iterative scan resumes it.
#: Recall at a fixed list falls as the graph grows, so this is twice the
#: candidates, the margin pgvector's own docs show. The cost is one HNSW scan
#: of a 100-entry list, about linear in this number.
HNSW_EF_SEARCH = 100
#: How many tuples an iterative scan may visit before it stops, short of its
#: `DENSE_CANDIDATES` or not. pgvector's default is 20,000, which could cut
#: off a unit holding few of the nearest vectors with candidates still
#: unvisited. This is above the whole index: a scoped scan gives up only after
#: it has seen every vector. The cost falls on a scope holding fewer than
#: `DENSE_CANDIDATES` vectors, which walks the whole graph every search;
#: `nora_memory_dense_short_total` counts every search that came back short.
HNSW_MAX_SCAN_TUPLES = 50_000

_TEXTS = pg.ARRAY(sa.Text)


def _array(values: Sequence[str] | frozenset[str]) -> Any:
    """One array parameter (`_among`'s reason: never one bind per value)."""
    return sa.cast(sa.bindparam(None, sorted(values), type_=_TEXTS), _TEXTS)


def _narrowed(n: Any, scope: MemoryScope) -> list[sa.ColumnElement[bool]]:
    """`scope.only` for the `memory_node` alias `n` (the module docstring);
    nothing when it narrows nothing."""
    f = scope.only
    if f is None:
        return []
    out: list[sa.ColumnElement[bool]] = []
    if f.kinds:
        out.append(n.c.kind == sa.any_(_array(f.kinds)))
    if f.since_ms is not None:
        out.append(n.c.at_ms >= f.since_ms)
    if f.until_ms is not None:
        out.append(n.c.at_ms <= f.until_ms)
    if f.person is not None:
        if f.person not in scope.listed_here:
            return [sa.false()]
        e = MEMORY_EDGE.alias("pe")
        linked = sa.exists().where(
            e.c.src_kind == n.c.kind, e.c.src_id == n.c.source_id,
            e.c.rel.in_(PERSON_RELATIONS), e.c.dst_kind == "person",
            e.c.dst_id == f.person,
        )
        out.append(sa.or_(linked, n.c.persons.contains(_array([f.person]))))
        out.append(sa.or_(
            n.c.origin_room.is_(None),
            n.c.origin_room == sa.any_(_array(scope.rooms_listing(f.person))),
        ))
    return out


def _in_scope(n: Any, scope: MemoryScope) -> sa.ColumnElement[bool]:
    """The module docstring's filter, for the `memory_node` alias `n`, with
    the question's narrowing (`_narrowed`) ANDed on."""
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
            n.c.kind.not_in(PRESENCE_KINDS),
            n.c.persons.contained_by(_array(scope.presence_persons)),
        ),
        sa.or_(
            n.c.kind.not_in(SOCIAL_KINDS),
            ~n.c.persons.overlap(_array(scope.social_off)),
        ),
        *_narrowed(n, scope),
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


def _like(term: str) -> str:
    """`%term%` with LIKE's own wildcards and escape character escaped."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _holds(text: Any, term: ExactTerm) -> sa.ColumnElement[bool]:
    """`exact_holds` in SQL, in a form `idx_memory_node_text_trgm` serves: a
    Latin term as a regular expression (`~`, or `~*` uncased), any other as
    `LIKE` (or `ILIKE`) on a substring. Never `strpos`, which no index
    serves: one such term in the OR would turn the whole read into a scan of
    the table."""
    if is_latin(term.text):
        op = "~" if term.cased else "~*"
        pattern = sa.bindparam(None, exact_pattern(term.text), type_=sa.Text)
        regex: sa.ColumnElement[bool] = text.op(op, is_comparison=True)(pattern)
        return regex
    like = sa.bindparam(None, _like(term.text), type_=sa.Text)
    held: sa.ColumnElement[bool] = text.like(like) if term.cased else text.ilike(like)
    return held


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
        unembedded_of: str | None = None,
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
        where = [sa.or_(contains, t.c.term.op("<%")(n.c.text)), _in_scope(n, scope)]
        if unembedded_of is not None:
            where += [
                n.c.kind.in_(EMBEDDED_KINDS),
                sa.or_(n.c.embedding.is_(None),
                       n.c.embedding_model.is_distinct_from(unembedded_of)),
            ]
        stmt = (
            sa.select(*_columns(n), score)
            .select_from(n.join(t, sa.true()))
            .where(*where)
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
            await conn.execute(sa.text(
                f"SET LOCAL hnsw.max_scan_tuples = {HNSW_MAX_SCAN_TUPLES}"
            ))
            rows = (await conn.execute(stmt)).all()
        return [Seed(_node(r), float(r.score)) for r in rows]

    async def exact(
        self, terms: Sequence[ExactTerm], scope: MemoryScope, *, limit: int,
        vector: Sequence[float] | None = None, model: str | None = None,
    ) -> list[Seed]:
        wanted = [t for t in dict.fromkeys(
            ExactTerm(t.text.strip(), t.cased) for t in terms) if t.text]
        if not wanted or limit <= 0:
            return []
        n = MEMORY_NODE.alias("n")
        held = [_holds(n.c.text, t) for t in wanted]
        count = sum((sa.case((h, 1), else_=0) for h in held), sa.literal(0)).label("held")
        order: list[Any] = [count.desc()]
        near: Any = sa.null()
        if vector is not None and model is not None:
            near = sa.case(
                (n.c.embedding_model == model,
                 1 - n.c.embedding.cosine_distance(_vector(vector, EMBEDDING_DIMENSIONS))),
                else_=None,
            )
            order.append(near.desc().nulls_last())
        stmt = (
            sa.select(*_columns(n), count, near.label("near"))
            .where(sa.or_(*held), _in_scope(n, scope))
            .order_by(*order, n.c.at_ms.desc().nulls_last(), n.c.kind, n.c.source_id)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            Seed(_node(r), 1.0, None if r.near is None else float(r.near)) for r in rows
        ]

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

    async def places(self, query: str, *, limit: int) -> list[IndexedNode]:
        if not query.strip() or limit <= 0:
            return []
        n = MEMORY_NODE.alias("n")
        stmt = (
            sa.select(*_columns(n))
            .where(n.c.kind == "place", n.c.text != "")
            .order_by(n.c.source_id)
        )
        # ponytail: every named place hub is read and matched here, not in
        # SQL (a name must be held by the question, the reverse of a seed);
        # they are the group's venues and the districts and cities around
        # them, hundreds. A name index if they ever grow to many thousands.
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_node(r) for r in rows if place_named(r.text, query)][:limit]

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

    async def index_state(
        self, *, model: str | None = None, scope: MemoryScope | None = None,
        fresh_seconds: float = 0.0,
    ) -> IndexState:
        async with self._engine.connect() as conn:
            s = await index_status(conn)
            if model is None:
                return IndexState(status=s.status, queue=s.queue)
            # ponytail: one count over the scope's nodes per search; a counter
            # row kept by the worker if it ever shows in a turn's latency.
            where = () if scope is None else (_in_scope(MEMORY_NODE, scope),)
            c = await embed_counts(conn, model, *where, fresh_seconds=fresh_seconds)
        return IndexState(
            status=s.status, queue=s.queue, embedded=c.embedded,
            unembedded=c.pending - c.given_up - c.empty, given_up=c.given_up,
            fresh=c.fresh,
        )
