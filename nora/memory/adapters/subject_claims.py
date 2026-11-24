"""Read the claims a person can inspect and correct on the portal."""

from collections import defaultdict
from collections.abc import Collection, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.lineage import split_room
from nora.infra.sql.cognitive_graph_schema import CLAIM, DISPUTE, EDGE
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.infra.sql.schema import MESSAGES


class SqlSubjectClaims:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read(
        self, *, unit: str, viewer: str, viewer_rooms: Collection[tuple[str, str]],
    ) -> tuple[dict[str, Any], ...]:
        # design/cognition/claims.md:60 gives the subject this inspection read.
        async with self._engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="REPEATABLE READ")
            claims = (await conn.execute(sa.select(
                CLAIM.c.id, CLAIM.c.part, CLAIM.c.content, CLAIM.c.reasoning,
                CLAIM.c.origin_room, CLAIM.c.state, CLAIM.c.asserted_at,
                CLAIM.c.prediction, CLAIM.c.window_end, CLAIM.c.grade,
            ).where(
                CLAIM.c.unit == unit, sa.any_(CLAIM.c.persons) == viewer,
                CLAIM.c.state != "superseded", visible_to_reads(CLAIM),
            ).order_by(CLAIM.c.asserted_at, CLAIM.c.id))).mappings().all()
            if not claims:
                return ()
            ids = tuple(claim["id"] for claim in claims)
            citations = (await conn.execute(_citations(unit, ids, viewer, viewer_rooms))).mappings()
            by_claim: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            for citation in citations:
                entry = {"ref": citation["target"], "kind": "unknown"}
                if citation["message"] is not None:
                    entry.update(kind="message", text=citation["body"], said_at=citation["ts"])
                by_claim[citation["claim"]].append(entry)
            open_disputes = await conn.scalars(sa.select(DISPUTE.c.claim_id).where(
                DISPUTE.c.unit == unit, DISPUTE.c.claim_id.in_(ids), visible_to_reads(DISPUTE),
            ).distinct())
            disputed = set(open_disputes)
            return tuple({
                **claim, "evidence": by_claim.get(claim["id"], []),
                "disputed": claim["id"] in disputed and claim["state"] == "awaiting_weighing",
                "origin_room": _origin(claim["origin_room"], viewer_rooms),
            } for claim in claims)


def _origin(ref: str | None, viewer_rooms: Collection[tuple[str, str]]) -> str | None:
    if ref is None:
        return None
    platform, conversation = split_room(ref)
    if platform is not None and (platform, conversation) in viewer_rooms:
        return ref
    return "private room"


def _citations(
    unit: str, ids: Sequence[str], viewer: str, viewer_rooms: Collection[tuple[str, str]],
) -> sa.Select[Any]:
    """Resolve raw messages only in rooms the portal viewer belongs to now."""
    edge, msg = EDGE.c, MESSAGES.c
    return sa.select(
        edge.src_id.label("claim"), edge.dst_id.label("target"), msg.graph_id.label("message"),
        msg.body, msg.ts,
    ).select_from(EDGE.outerjoin(MESSAGES, sa.and_(
        msg.graph_id == edge.dst_id, msg.unit == edge.unit,
        # design/cognition/boundaries.md:38-43 reserves raw evidence for its room.
        sa.tuple_(msg.platform, msg.conversation_id).in_(sorted(viewer_rooms)),
        msg.hidden_at.is_(None), msg.exposure == "external",
        viewer != sa.all_(msg.excluded_persons), visible_to_reads(MESSAGES),
    ))).where(
        edge.unit == unit, edge.src_id.in_(ids), edge.type == "cites", edge.state == "live",
        visible_to_reads(EDGE),
    ).order_by(edge.dst_id)
