"""The text-only queue of unembedded Cognitive Graph search rows."""

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.cognitive_graph_schema import GRAPH_SEARCH, REGISTRY
from nora.infra.sql.embed_schedule import embed_due, failed_attempts


@dataclass(frozen=True, slots=True)
class GraphEmbeddable:
    id: str
    unit: str
    text: str

    kind = "graph_search"
    sha256 = None


async def pick(conn: AsyncConnection, *, limit: int, now_ms: int) -> list[GraphEmbeddable]:
    """Pick the newest text among the rows with the fewest attempts."""
    row = GRAPH_SEARCH.c
    identity = REGISTRY.c
    result = await conn.execute(
        sa.select(row.graph_id, row.unit, row.text)
        .join(REGISTRY, (row.graph_id == identity.id) & (row.unit == identity.unit))
        .where(row.embedding.is_(None), embed_due(row, now_ms))
        .order_by(row.embed_attempts, identity.registered_at.desc(), row.graph_id, row.unit)
        .limit(limit),
    )
    return [GraphEmbeddable(r.graph_id, r.unit, r.text) for r in result]


def _same_input(item: GraphEmbeddable) -> Any:
    row = GRAPH_SEARCH.c
    return sa.and_(row.graph_id == item.id, row.unit == item.unit, row.text == item.text)


async def store(
    conn: AsyncConnection, item: GraphEmbeddable, *, vector: list[float], model: str,
    now_ms: int,
) -> bool:
    """Keep the vector only if its text still exists."""
    result = await conn.execute(sa.update(GRAPH_SEARCH).where(_same_input(item)).values(
        embedding=vector, embedding_model=model, embedded_at=now_ms,
        embed_attempts=0, embed_error=None, embed_input="text",
    ))
    return bool(result.rowcount)


async def failed(
    conn: AsyncConnection, item: GraphEmbeddable, *, error: str, now_ms: int, counts: bool,
) -> int:
    """Back off the current text after a failed attempt."""
    attempts = GRAPH_SEARCH.c.embed_attempts
    result = await conn.scalar(sa.update(GRAPH_SEARCH).where(_same_input(item)).values(
        embedding=None, embedding_model=None, embedded_at=now_ms,
        embed_attempts=failed_attempts(attempts, counts=counts), embed_error=error[:80],
        embed_input="text",
    ).returning(attempts))
    return int(result or 0)


async def clear_other_models(conn: AsyncConnection, *, keep_model: str) -> int:
    """Return differently fingerprinted vectors to the queue."""
    row = GRAPH_SEARCH.c
    result = await conn.execute(sa.update(GRAPH_SEARCH).where(
        row.embedding.is_not(None), row.embedding_model.is_distinct_from(keep_model),
    ).values(
        embedding=None, embedding_model=None, embedded_at=None,
        embed_attempts=0, embed_error=None, embed_input=None,
    ))
    return int(result.rowcount or 0)
