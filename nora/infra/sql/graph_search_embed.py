"""The text-only queue of unembedded Cognitive Graph search rows."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.cognitive_graph_schema import GRAPH_SEARCH, REGISTRY
from nora.infra.sql.embed_schedule import EMBED_MAX_ATTEMPTS, embed_due, failed_attempts
from nora.infra.sql.schema import SOCIAL_ITEM


@dataclass(frozen=True, slots=True)
class GraphEmbeddable:
    id: str
    unit: str
    text: str

    kind = "graph_search"
    sha256 = None


async def settled_story_frames(
    conn: AsyncConnection, frames: Iterable[tuple[str, str]], *, model: str,
) -> set[tuple[str, str]]:
    """Of `frames` (`(network, item_id)`), those whose Cognitive Graph embedding
    is settled for `model` (design/18-media-custody.md;
    design/instagram-adoption/social.md): at least one registered graph row of
    the frame holds a vector of `model`, or its input is terminal — given up at
    `EMBED_MAX_ATTEMPTS`, or failed `empty`, which no later tick of the same
    text will change. An absent row, one not yet attempted, and one still
    queued after a failure are not settled, so the frame's bytes wait for the
    hard limit: keeping bytes longer is the safe side of the bound."""
    wanted = {(network, item_id) for network, item_id in frames}
    if not wanted:
        return set()
    item, indexed = SOCIAL_ITEM.c, GRAPH_SEARCH.c
    settled = sa.or_(
        sa.and_(indexed.embedding.isnot(None), indexed.embedding_model == model),
        sa.and_(
            indexed.embedding.is_(None),
            sa.or_(
                indexed.embed_attempts >= EMBED_MAX_ATTEMPTS,
                indexed.embed_error == "empty",
            ),
        ),
    )
    rows = await conn.execute(
        sa.select(item.network, item.item_id)
        .select_from(SOCIAL_ITEM.join(GRAPH_SEARCH, sa.and_(
            item.graph_id == indexed.graph_id, item.unit == indexed.unit,
        )))
        .where(sa.tuple_(item.network, item.item_id).in_(sorted(wanted)), settled)
        .distinct()
    )
    return {(r.network, r.item_id) for r in rows}


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
