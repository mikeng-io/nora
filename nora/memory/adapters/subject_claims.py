"""Read the claims a person can inspect and correct on the portal."""

from collections import defaultdict
from collections.abc import Collection, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.lineage import split_room
from nora.infra.sql.audit import transcript_select
from nora.infra.sql.cognitive_graph_schema import CLAIM, DISPUTE, EDGE
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGES
from nora.memory.adapters.graph_row_render import render_graph_content


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
                _is_dm_origin().label("dm_origin"),
            ).where(
                CLAIM.c.unit == unit, sa.any_(CLAIM.c.identities) == viewer,
                viewer != sa.all_(CLAIM.c.excluded_identities),
                CLAIM.c.exposure == "external",  # design/cognition/boundaries.md:36-37
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
                    entry.update(kind="message", text=citation["body"], said_at=citation["ts"],
                                 person_id=citation["person_id"])
                by_claim[citation["claim"]].append(entry)
            dispute_times = dict((await conn.execute(sa.select(
                DISPUTE.c.claim_id, sa.func.max(DISPUTE.c.asserted_at),
            ).where(
                DISPUTE.c.unit == unit, DISPUTE.c.claim_id.in_(ids), visible_to_reads(DISPUTE),
            ).group_by(DISPUTE.c.claim_id))).tuples().all())
            grades = await _guess_grades(conn, unit, claims)
            return tuple({
                **claim, "evidence": by_claim.get(claim["id"], []),
                "content": _content_text(claim["content"]),
                "grade": grades.get(claim["id"], claim["grade"]),
                "disputed_at": (
                    dispute_times.get(claim["id"])
                    if claim["state"] == "awaiting_weighing" else None
                ),
                "origin_room": _origin(claim["origin_room"], claim["dm_origin"], viewer_rooms),
            } for claim in claims)


def _content_text(value: object) -> str:
    text = value.get("text") if isinstance(value, dict) else value
    return text if isinstance(text, str) else render_graph_content(value)


def _is_dm_origin() -> sa.ColumnElement[bool]:
    msg = MESSAGES.c
    return sa.exists(sa.select(msg.graph_id).where(
        msg.unit == CLAIM.c.unit, msg.conversation_type == "dm",
        (msg.platform + ":" + msg.conversation_id) == CLAIM.c.origin_room,
    ))


def _origin(ref: str | None, is_dm: bool, rooms: Collection[tuple[str, str]]) -> str | None:
    if ref is None:
        return None
    platform, conversation = split_room(ref)
    if platform is not None and (platform, conversation) in rooms:
        return ref
    return "direct message" if is_dm else "private room"


async def _guess_grades(
    conn: AsyncConnection, unit: str, claims: Sequence[sa.RowMapping],
) -> dict[str, str]:
    # design/cognition/claims.md:18-23 puts the assessment on a separate claim.
    grades = {row["id"]: row["grade"] for row in claims
              if row["part"] == "grade" and row["state"] == "live"}
    guesses = tuple(row["id"] for row in claims if row["part"] == "guess")
    if not grades or not guesses:
        return {}
    edge = EDGE.c
    links = await conn.execute(sa.select(edge.dst_id, edge.src_id).where(
        edge.unit == unit, edge.type == "grades", edge.state == "live",
        edge.dst_id.in_(guesses), edge.src_id.in_(tuple(grades)), visible_to_reads(EDGE),
    ).order_by(edge.asserted_at, edge.id))
    return {guess: grades[grade] for guess, grade in links}


def _citations(
    unit: str, ids: Sequence[str], viewer: str, viewer_rooms: Collection[tuple[str, str]],
) -> sa.Select[Any]:
    """Resolve raw messages only in rooms the portal viewer belongs to now."""
    edge, msg = EDGE.c, MESSAGES.c
    cites = sa.select(
        edge.src_id.label("claim"), edge.dst_id.label("target"),
    ).where(
        edge.unit == unit, edge.src_id.in_(ids), edge.type == "cites", edge.state == "live",
        visible_to_reads(EDGE),
    ).cte("claim_citations")
    messages = transcript_select().add_columns(msg.graph_id).where(
        msg.unit == unit, msg.graph_id.in_(sa.select(cites.c.target)),
        # design/cognition/boundaries.md:38-43 reserves raw evidence for its room.
        sa.tuple_(msg.platform, msg.conversation_id).in_(sorted(viewer_rooms)),
        msg.exposure == "external",
        viewer != sa.all_(msg.excluded_identities),
        visible_to_reads(MESSAGES),
        ~sa.exists(sa.select(MESSAGE_DELETE.c.id).where(MESSAGE_DELETE.c.message_id == msg.id)),
    ).subquery()
    visible = messages.c
    body = sa.case((visible.edit_ts.is_not(None), visible.edit_body), else_=visible.body)
    return sa.select(
        cites.c.claim, cites.c.target, visible.graph_id.label("message"),
        body.label("body"), visible.ts, visible.person_id,
    ).select_from(cites.outerjoin(
        messages, visible.graph_id == cites.c.target,
    )).order_by(cites.c.target)
