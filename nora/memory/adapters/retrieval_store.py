"""Bounded SQL seeds and walks over one scoped snapshot."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql import FromClause

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.infra.sql.cognitive_graph_schema import GRAPH_SEARCH
from nora.memory.adapters.retrieval_load import load_rows
from nora.memory.adapters.retrieval_rows import tombstoned_ids
from nora.memory.adapters.retrieval_scope import RetrievalAccess, scoped_rows
from nora.memory.domain.registration import SEARCHABLE_SOURCES
from nora.memory.domain.retrieval import (
    Candidates,
    FreshCandidates,
    ReadingRoom,
    RetrievalBounds,
    RetrievalScope,
    RetrievalUnavailableError,
    RetrievedRow,
    ReviewWeakening,
    ScopedGraph,
)

_INACTIVE = ("retired", "ended", "superseded", "merged", "tombstoned")


class SqlRetrieval:
    def __init__(
        self, engine: AsyncEngine, access: IdentityAccess, lineage: LineagePolicy,
        embedding_model: str | None,
    ) -> None:
        self._engine = engine
        self._authority = RetrievalAccess(access, lineage)
        self._model = embedding_model

    @asynccontextmanager
    async def open_retrieval(
        self, room: ReadingRoom, concerns: frozenset[str], as_of: datetime | None,
        bounds: RetrievalBounds, now: datetime,
    ) -> AsyncIterator[ScopedGraph]:
        if bounds.ceiling is not None:
            as_of, now = None, bounds.ceiling
        target, scope, dm_rooms = await self._authority.authority(room, concerns, as_of)
        try:
            async with self._engine.connect() as raw:
                conn = await raw.execution_options(isolation_level="REPEATABLE READ")
                opted_out = await self._authority.social_opt_outs(conn, scope)
                scope = replace(scope, social_off=opted_out)
                rows = await scoped_rows(conn, scope, target, dm_rooms, ceiling=bounds.ceiling)
                yield _ScopedQueries(
                    conn, rows, scope, bounds, self._model, self._authority.access, now, dm_rooms,
                )
        except sa.exc.SQLAlchemyError as exc:
            raise RetrievalUnavailableError("statement_failed") from exc


class _ScopedQueries:
    def __init__(
        self, conn: AsyncConnection, rows: FromClause, scope: RetrievalScope,
        bounds: RetrievalBounds, model: str | None, access: IdentityAccess,
        now: datetime, dm_rooms: frozenset[str],
    ) -> None:
        self.conn = conn
        self.rows = rows
        self.scope = scope
        self.bounds = bounds
        self.model = model
        self.access = access
        self.now = now
        self.dm_rooms = dm_rooms

    def _window(self, at: Any) -> sa.ColumnElement[bool]:
        end = self.bounds.until
        if self.bounds.ceiling is not None:
            end = min(end, self.bounds.ceiling)
        return at.between(self.bounds.since, end)

    async def resolve_exact_seeds(self, graph_ids: tuple[str, ...]) -> Candidates:
        row = self.rows.c
        priority = -sa.func.array_position(array(graph_ids), row.graph_id)
        matched = sa.select(
            row.graph_id.label("root"), row.graph_id, priority.label("score"),
        ).where(row.graph_id.in_(graph_ids))
        return await self._resolved(matched, exact=True)

    def _history_text(self) -> Any:
        row = self.rows.c
        return sa.case(*(
            (row.source == table, sa.func.nora_graph_search_text(row.image, array(fields)))
            for table, fields in SEARCHABLE_SOURCES.items()
        ))

    async def lexical(self, query: str) -> Candidates:
        row = self.rows.c
        source: FromClause
        if self.scope.as_of is None:
            indexed = GRAPH_SEARCH.c
            source = self.rows.join(GRAPH_SEARCH, (
                (indexed.graph_id == row.graph_id) & (indexed.unit == row.unit)
            ))
            text = indexed.text
        else:
            source = self.rows
            text = self._history_text()
        score = sa.func.similarity(text, query)
        matches = sa.select(
            row.graph_id.label("root"), row.graph_id, score.label("score"),
        ).select_from(source).where(text.op("%")(query))
        return await self._resolved(matches, exact=False)

    async def vector(self, vector: list[float]) -> Candidates:
        row = self.rows.c
        indexed = GRAPH_SEARCH.c
        if self.model is None:
            return Candidates()
        distance = indexed.embedding.cosine_distance(vector)
        matches = sa.select(
            row.graph_id.label("root"), row.graph_id, (-distance).label("score"),
        ).select_from(self.rows.join(GRAPH_SEARCH, (
            (indexed.graph_id == row.graph_id) & (indexed.unit == row.unit)
        ))).where(
            indexed.embedding.is_not(None), indexed.embedding_model == self.model,
            sa.true() if self.scope.as_of is None else indexed.text == self._history_text(),
        )
        return await self._resolved(matches, exact=False)

    async def _resolved(self, matches: sa.Select[Any], *, exact: bool) -> Candidates:
        row = self.rows.c
        chain = matches.cte("revisions", recursive=True)
        chain = chain.union(sa.select(
            chain.c.root, row.graph_id, chain.c.score,
        ).where(row.replaces == chain.c.graph_id, row.graph_id != chain.c.graph_id))
        child = self.rows.alias("successor").c
        terminal = sa.select(
            row.graph_id, row.event_time, sa.func.max(chain.c.score).label("score"),
        ).select_from(self.rows.join(chain, row.graph_id == chain.c.graph_id)).where(
            ~sa.exists(sa.select(1).where(child.replaces == row.graph_id)),
            row.state.not_in(_INACTIVE) | (
                (chain.c.root == row.graph_id) & row.state.in_(("ended", "retired"))
                if exact else sa.false()
            ),
        ).group_by(row.graph_id, row.event_time).cte("resolved")
        return await self._candidates(terminal, self.bounds.rows + 1, terminal.c.score.desc())

    async def _candidates(self, found: FromClause, limit: int, *order: Any) -> Candidates:
        row = found.c
        inside = self._window(row.event_time)
        matches = tuple(await self.conn.scalars(sa.select(row.graph_id).where(inside)
                                               .order_by(*order, row.graph_id).limit(limit)))
        outside = await self.conn.scalar(sa.select(
            sa.exists(sa.select(1).select_from(found).where(~inside)),
        ))
        return Candidates(matches, bool(outside))

    async def walk_neighbours(self, graph_id: str, seen: frozenset[str]) -> Candidates:
        current_row = self.rows.alias("current_row")
        neighbour = self.rows.alias("neighbour")
        connects = self.rows.alias("connects")
        current, other, edge = current_row.c, neighbour.c, connects.c
        src, dst, typ = _endpoints(edge)
        links = sa.or_(
            (src == current.graph_id) & (dst == other.graph_id),
            (dst == current.graph_id) & (src == other.graph_id),
        )
        pointer = current.image["pointer_kind"].astext.is_not(None)
        found = sa.select(other.graph_id, other.event_time).select_from(
            current_row.join(connects, (src == current.graph_id) | (dst == current.graph_id))
            .join(neighbour, links),
        ).where(
            current.graph_id == graph_id, edge.source == "edge", edge.state == "live",
            other.state.not_in(_INACTIVE), other.graph_id.not_in(seen),
            ~pointer | ((dst == graph_id) & (typ == "about")
                        & other.kind.in_(("claim", "intention"))),
        ).distinct().cte("neighbours")
        return await self._candidates(found, self.bounds.fan_out + 1, found.c.event_time.desc())

    async def fresh_items(self, distances: Mapping[str, int]) -> FreshCandidates:
        sources = _fresh_links(self.rows, distances)
        inside = self._window(sources.c.event_time)
        priority = sa.case(dict(distances), value=sources.c.claim_id, else_=self.bounds.depth + 1)
        nearest = sa.select(
            sources.c.graph_id, sources.c.claim_id, sources.c.asserted_at,
            sa.func.row_number().over(
                partition_by=sources.c.graph_id, order_by=(priority, sources.c.claim_id),
            ).label("rank"),
        ).where(inside).subquery()
        result = await self.conn.execute(sa.select(nearest.c.graph_id, nearest.c.claim_id).where(
            nearest.c.rank == 1,
        ).order_by(nearest.c.asserted_at.desc(), nearest.c.graph_id).limit(self.bounds.rows + 1))
        outside = await self.conn.scalar(sa.select(sa.exists(
            sa.select(1).select_from(sources).where(~inside),
        )))
        return FreshCandidates(tuple((r.graph_id, r.claim_id) for r in result), bool(outside))

    async def load(
        self, distances: Mapping[str, int], already_shown: frozenset[str],
    ) -> tuple[RetrievedRow, ...]:
        return await load_rows(
            self.conn, self.rows, distances, already_shown, self.scope, self.access,
            self.now, self.dm_rooms, ceiling=self.bounds.ceiling,
        )

    async def visible_review_weakenings(
        self, requested: tuple[ReviewWeakening, ...],
    ) -> frozenset[ReviewWeakening]:
        # Both rows come from the graph opened for the final retrieval audience.
        grade, edge = self.rows.c, self.rows.alias("weakening_links").c
        source, target, kind = _endpoints(edge)
        pairs = tuple((ref.grade_id, ref.lesson_id) for ref in requested)
        query = sa.select(source, target).where(
            edge.source == "edge", edge.state == "live", kind == "grades",
            sa.tuple_(source, target).in_(pairs), grade.graph_id == source,
            grade.source == "claim", grade.state == "live", grade.event_time <= self.now,
            grade.image["part"].astext == "grade", grade.image["grade"].astext == "weakened",
        )
        if self.scope.as_of is not None:
            removed = tombstoned_ids(self.scope.unit, since=self.scope.as_of)
            query = query.where(grade.graph_id.not_in(removed), edge.graph_id.not_in(removed))
        return frozenset(ReviewWeakening(*pair) for pair in await self.conn.execute(query))


def _endpoints(edge: Any) -> tuple[Any, Any, Any]:
    image = edge.image
    return image["src_id"].astext, image["dst_id"].astext, image["type"].astext


def _fresh_links(rows: FromClause, distances: Mapping[str, int]) -> FromClause:
    item = rows.alias("fresh_item").c
    claim = rows.alias("seed_claim").c
    about = rows.alias("claim_about").c
    anchor = rows.alias("shared_subject").c
    tagged = rows.alias("item_about").c
    claim_src, claim_dst, claim_type = _endpoints(about)
    item_src, item_dst, item_type = _endpoints(tagged)
    person = anchor.image["pointer_ref"].astext
    matches_tag = sa.exists(sa.select(1).where(
        tagged.source == "edge", tagged.state == "live", item_type == "about",
        item_src == item.graph_id, item_dst == anchor.graph_id,
    ))
    return sa.select(
        item.graph_id, claim.graph_id.label("claim_id"), item.event_time, item.asserted_at,
    ).where(
        item.kind == "item", item.graph_id.not_in(distances),
        claim.source == "claim", claim.graph_id.in_(distances),
        item.asserted_at > claim.asserted_at,
        about.source == "edge", about.state == "live", claim_type == "about",
        claim_src == claim.graph_id, claim_dst == anchor.graph_id, anchor.source == "entity",
        matches_tag | ((anchor.image["pointer_kind"].astext == "person")
                       & item.image["persons"].op("?")(person)),
    ).distinct().cte("fresh")
