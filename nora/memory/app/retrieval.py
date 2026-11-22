"""Seed, walk and load one room-scoped Cognitive Graph read."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from nora.domain.memory_index import QueryEmbedder
from nora.memory.domain.retrieval import (
    Candidates,
    ReadingRoom,
    RetrievalAnchors,
    RetrievalBounds,
    RetrievalLimit,
    RetrievalReader,
    RetrievalResult,
    RetrievalUnavailableError,
    ScopedGraph,
)


@dataclass(slots=True)
class _Walk:
    bounds: RetrievalBounds
    distances: dict[str, int] = field(default_factory=dict)
    limits: dict[RetrievalLimit, None] = field(default_factory=dict)

    def collect(self, candidates: Candidates, distance: int) -> tuple[str, ...]:
        if candidates.outside_window:
            self.limits["time_window"] = None
        added = []
        for graph_id in candidates.ids:
            if graph_id in self.distances:
                continue
            if len(self.distances) == self.bounds.rows:
                self.limits["rows"] = None
                break
            self.distances[graph_id] = distance
            added.append(graph_id)
        return tuple(added)

    async def expand(self, graph: ScopedGraph, seeds: tuple[str, ...]) -> None:
        frontier = seeds
        for depth in range(self.bounds.depth + 1):
            following: list[str] = []
            for graph_id in frontier:
                neighbours = await graph.walk_neighbours(graph_id, frozenset(self.distances))
                if neighbours.outside_window:
                    self.limits["time_window"] = None
                if depth == self.bounds.depth:
                    if neighbours.ids:
                        self.limits["depth"] = None
                    continue
                if len(neighbours.ids) > self.bounds.fan_out:
                    self.limits["fan_out"] = None
                following.extend(self.collect(
                    Candidates(neighbours.ids[:self.bounds.fan_out]), depth + 1,
                ))
            if not following:
                break
            frontier = tuple(following)


class RetrievalService:
    def __init__(self, reader: RetrievalReader, embedder: QueryEmbedder | None) -> None:
        self._reader = reader
        self._embedder = embedder

    async def retrieve(
        self, room: ReadingRoom, anchors: RetrievalAnchors, bounds: RetrievalBounds,
        *, concerns: frozenset[str] = frozenset(), as_of: datetime | None = None,
        already_shown: frozenset[str] = frozenset(), now: datetime | None = None,
    ) -> RetrievalResult:
        """Return the bounded subgraph that the reading room may see."""
        walk = _Walk(bounds)
        try:
            async with self._reader.open_retrieval(
                room, concerns, as_of, bounds, now if now is not None else datetime.now(UTC),
            ) as graph:
                degraded = await self._seed(graph, anchors, walk)
                if not walk.distances:
                    return RetrievalResult(
                        "partial" if walk.limits else "nothing_found", bounds,
                        limits=tuple(walk.limits),
                    )
                await walk.expand(graph, tuple(walk.distances))
                fresh = await graph.fresh_items(walk.distances)
                for graph_id, claim_id in fresh.links:
                    distance = walk.distances[claim_id] + 1
                    if distance > bounds.depth:
                        walk.limits["depth"] = None
                    else:
                        walk.collect(Candidates((graph_id,)), distance)
                if fresh.outside_window:
                    walk.limits["time_window"] = None
                rows = await graph.load(walk.distances, already_shown)
        except RetrievalUnavailableError as exc:
            return RetrievalResult("unavailable", bounds, cause=str(exc))
        if any(row.unavailable for row in rows):
            return RetrievalResult(
                "unavailable", bounds, rows=rows, cause="tombstoned_since",
                new_count=sum(not row.already_shown for row in rows),
            )
        if any(row.items_since_cause == "items_since_unavailable" for row in rows):
            degraded = "items_since_unavailable"
        elif any("published_occurrences" in row.close_when
                 and row.close_when["published_occurrences"] is None for row in rows):
            degraded = "occurrences_unavailable"
        return RetrievalResult(
            "partial" if degraded or walk.limits else "ok",
            bounds, rows, tuple(walk.limits), degraded,
            sum(not row.already_shown for row in rows),
        )

    async def _seed(
        self, graph: ScopedGraph, anchors: RetrievalAnchors, walk: _Walk,
    ) -> str | None:
        ids = (*anchors.graph_ids, *(h.removeprefix("graph:") for h in anchors.handles
                                     if h.startswith("graph:")))
        if ids:
            walk.collect(await graph.resolve_exact_seeds(ids), 0)
        query = anchors.query.strip()
        if not query:
            return None
        lexical = await graph.lexical(query)
        walk.collect(lexical, 0)
        try:
            if self._embedder is None:
                raise RetrievalUnavailableError("query_embedder_unavailable")
            vector = await self._embedder.embed_query(query)
        except Exception as exc:
            if not walk.distances:
                raise RetrievalUnavailableError("query_embedding_failed") from exc
            return "query_embedding_failed"
        walk.collect(await graph.vector(vector), 0)
        return None
