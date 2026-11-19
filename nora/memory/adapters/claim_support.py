"""Recompute the current support tally from a claim's visible cited items."""

from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql.cognitive_graph_schema import CLAIM, CLAIM_SUPPORT, EDGE
from nora.infra.sql.cognitive_graph_store import (
    _SOURCE_CONTENT,
    RowData,
    SqlCognitiveGraph,
    content_digest,
    visible_to_reads,
)
from nora.infra.sql.schema import METADATA
from nora.memory.domain.registration import ITEM_SOURCES

_SCOPE = ("excluded_persons", "exposure", "lineage")


class SqlClaimSupport:
    def __init__(self, engine: AsyncEngine, graph: SqlCognitiveGraph) -> None:
        self._engine = engine
        self._graph = graph

    async def rebuild(self, *, unit: str, claim_id: str, at: datetime) -> int | None:
        """Rebuild one registered head, or return None when the claim is unavailable."""
        claim = CLAIM.c
        async with self._engine.begin() as conn:
            parent = (await conn.execute(sa.select(
                claim.persons, claim.origin_room, *(claim[key] for key in _SCOPE),
            ).where(
                claim.unit == unit, claim.id == claim_id,
                claim.state.in_(("live", "awaiting_weighing")), visible_to_reads(CLAIM),
            ).with_for_update(key_share=True))).mappings().one_or_none()
            if parent is None:
                return None
            counted = _occasions(await _cited_items(conn, unit, claim_id))
            last = counted[-1]["occurred_at"] if counted else None
            planes = await self._graph.aggregate_planes(
                conn, CLAIM_SUPPORT, unit=unit, key=(claim_id,), occurred_at=last,
                asserted_at=at, persons=parent["persons"], room=parent["origin_room"],
                inputs=(parent, *counted), minimum="internal",
            )
            values = {**planes, "support_count": len(counted), "last_evidenced_at": last}
            stmt = insert(CLAIM_SUPPORT).values(claim_id=claim_id, **values)
            await conn.execute(stmt.on_conflict_do_update(
                index_elements=[CLAIM_SUPPORT.c.claim_id, CLAIM_SUPPORT.c.unit],
                set_={key: stmt.excluded[key] for key in values},
            ))
        return len(counted)


async def _cited_items(conn: AsyncConnection, unit: str, claim_id: str) -> list[RowData]:
    edge = EDGE.c
    cites = sa.select(edge.dst_id).where(
        edge.unit == unit, edge.src_id == claim_id, edge.type == "cites", edge.state == "live",
        visible_to_reads(EDGE),
    ).distinct().cte("live_cites")
    reads = []
    for source in ITEM_SOURCES:
        table = METADATA.tables[source]
        c = table.c
        payload = sa.func.jsonb_build_object(
            *(arg for col in _SOURCE_CONTENT[source] for arg in (sa.literal(col), c[col])),
            type_=JSONB,
        ).label("payload")
        reads.append(sa.select(
            c.graph_id, c.origin_room, c.occurred_at, *(c[key] for key in _SCOPE),
            sa.literal(source).label("source"), payload,
        ).join(cites, cites.c.dst_id == c.graph_id).where(
            c.unit == unit, c.state == "live", c.occurred_at.is_not(None), visible_to_reads(table),
        ))
    rows = await conn.execute(sa.union_all(*reads).order_by("occurred_at", "graph_id"))
    return list(rows.mappings())


def _occasions(items: list[RowData]) -> list[RowData]:
    digests: set[str] = set()
    occasions: dict[tuple[str | None, date], RowData] = {}
    for item in items:
        digest = content_digest(item["source"], item["payload"])
        if digest in digests:
            continue
        digests.add(digest)
        at = item["occurred_at"]
        key = item["origin_room"], at.astimezone(UTC).date()
        occasions.setdefault(key, item)
    return list(occasions.values())
