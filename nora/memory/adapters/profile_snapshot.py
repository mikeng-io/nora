"""Graph profile metadata for replay evidence and judge input."""

from collections.abc import Iterable, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.cognitive_graph_schema import CLAIM
from nora.infra.sql.cognitive_graph_store import visible_to_reads

# design/cognition/graph.md:95-99,200-202 defines the evidence planes and profile parts.
PROFILE_PARTS = ("fact", "trait", "guess")
PROFILE_METADATA = ("id", "part", "state", "reasoning", "confidence", "asserted_at", "replaces")


async def profile_snapshot(
    conn: AsyncConnection, *, unit: str, rooms: Iterable[str],
    people: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Current profile metadata in a restored unit, bounded to the named rooms."""
    rows = (await conn.execute(sa.select(
        *(CLAIM.c[name] for name in PROFILE_METADATA),
    ).where(
        CLAIM.c.unit == unit, CLAIM.c.origin_room.in_(tuple(rooms)),
        CLAIM.c.part.in_(PROFILE_PARTS), CLAIM.c.state.in_(("live", "awaiting_weighing")),
        CLAIM.c.exposure == "external", sa.func.cardinality(CLAIM.c.excluded_identities) == 0,
        visible_to_reads(CLAIM),
        sa.true() if people is None else CLAIM.c.identities.contained_by(list(people)),
    ).order_by(CLAIM.c.id))).mappings()
    return [dict(row) for row in rows]
