"""Rebuild a lesson head's tally from the verdicts on its dependencies."""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.graph_stimuli import GraphStimulus
from nora.infra.sql.cognitive_graph_schema import (
    CLAIM,
    EDGE,
    INTENTION,
    INTENTION_VERDICT,
    LESSON_TALLY,
)
from nora.infra.sql.cognitive_graph_store import RowData, SqlCognitiveGraph, visible_to_reads
from nora.infra.sql.graph_stimuli import SqlGraphStimuli


def _resting(unit: str) -> sa.Subquery:
    rest, serve = EDGE.alias("lesson_rest"), EDGE.alias("maintain_child")
    lesson, parent = CLAIM.c, INTENTION.c
    resting = sa.select(
        rest.c.dst_id.label("claim_id"), rest.c.src_id.label("intention_id"),
    ).select_from(rest.join(CLAIM, sa.and_(
        lesson.unit == rest.c.unit, lesson.id == rest.c.dst_id,
    ))).where(
        rest.c.unit == unit, rest.c.type == "rests_on", rest.c.state == "live",
        lesson.part == "lesson", visible_to_reads(rest),
    ).cte("resting_on_lesson")
    indirect = sa.select(resting.c.claim_id, serve.c.src_id).select_from(resting.join(
        INTENTION, sa.and_(parent.id == resting.c.intention_id, parent.unit == unit),
    ).join(serve, sa.and_(serve.c.unit == unit, serve.c.dst_id == parent.id))).where(
        parent.form == "maintain", serve.c.type == "serves", serve.c.state == "live",
        visible_to_reads(serve), visible_to_reads(INTENTION),
    )
    return sa.select(resting).union(indirect).subquery()


async def rebuild_lessons_for_verdict(
    conn: AsyncConnection, graph: SqlCognitiveGraph,
    *, unit: str, intention_id: str, at: datetime,
) -> None:
    resting = _resting(unit)
    claims = tuple(await conn.scalars(sa.select(resting.c.claim_id).where(
        resting.c.intention_id == intention_id,
    ).order_by(resting.c.claim_id)))
    for claim_id in claims:
        await rebuild_lesson_tally(conn, graph, unit=unit, claim_id=claim_id, at=at)


async def rebuild_lesson_tally(
    conn: AsyncConnection, graph: SqlCognitiveGraph,
    *, unit: str, claim_id: str, at: datetime,
) -> None:
    c, v = CLAIM.c, INTENTION_VERDICT.c
    lesson = (await conn.execute(sa.select(CLAIM).where(
        c.unit == unit, c.id == claim_id, c.part == "lesson", visible_to_reads(CLAIM),
    ).with_for_update())).mappings().one_or_none()
    if lesson is None:
        return
    resting = _resting(unit)
    verdicts = (await conn.execute(sa.select(INTENTION_VERDICT).join(
        resting, resting.c.intention_id == v.intention_id,
    ).where(
        v.unit == unit, resting.c.claim_id == claim_id,
        v.ending.in_(("achieved", "leaked")), visible_to_reads(INTENTION_VERDICT),
    ).order_by(v.ended_at, v.intention_id))).mappings().all()
    await _write(conn, graph, lesson, verdicts, at)


async def _write(
    conn: AsyncConnection, graph: SqlCognitiveGraph, lesson: RowData,
    verdicts: Sequence[sa.RowMapping], at: datetime,
) -> None:
    unit, claim_id = lesson["unit"], lesson["id"]
    support = sum(row["ending"] == "achieved" for row in verdicts)
    contradiction = len(verdicts) - support
    evidence_at = verdicts[-1]["ended_at"] if verdicts else lesson["asserted_at"]
    planes = await graph.aggregate_planes(
        conn, LESSON_TALLY, unit=unit, key=(claim_id,), occurred_at=evidence_at,
        asserted_at=evidence_at, persons=lesson["persons"], room=lesson["origin_room"],
        inputs=(lesson, *verdicts), minimum="internal",
    )
    values = {**planes, "support": support, "contradiction": contradiction}
    row = LESSON_TALLY.c
    stmt = insert(LESSON_TALLY).values(**values, claim_id=claim_id)
    await conn.execute(stmt.on_conflict_do_update(
        constraint="pk_lesson_tally", set_=values,
        where=sa.or_(*(row[key].is_distinct_from(stmt.excluded[key]) for key in values)),
    ))
    if lesson["state"] == "live" and contradiction > support:
        await SqlGraphStimuli.append_in(conn, GraphStimulus(
            unit, "dependency", claim_id, at, "lesson_tally", room=lesson["origin_room"],
            occurrence=f"{support}:{contradiction}",
        ))
