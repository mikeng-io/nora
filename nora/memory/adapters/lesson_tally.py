"""Rebuild a lesson head's tally from the verdicts on its dependencies."""

from collections.abc import Sequence
from datetime import datetime, timedelta

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


def resting_verdict_paths(unit: str, claim_id: str | None = None) -> sa.Subquery:
    rest, serve = EDGE.alias("lesson_rest"), EDGE.alias("maintain_child")
    lesson, parent = CLAIM.c, INTENTION.c
    by_claim = (lesson.id == claim_id,) if claim_id is not None else ()
    direct = sa.select(
        rest.c.dst_id.label("claim_id"), rest.c.src_id.label("intention_id"),
        rest.c.id.label("rest_id"), sa.literal(None, type_=sa.Text).label("serve_id"),
    ).select_from(rest.join(CLAIM, sa.and_(
        lesson.unit == rest.c.unit, lesson.id == rest.c.dst_id,
    ))).where(
        rest.c.unit == unit, rest.c.type == "rests_on", rest.c.state == "live",
        lesson.part == "lesson", visible_to_reads(rest), *by_claim,
    )
    rested = direct.cte("resting_on_lesson")
    indirect = sa.select(
        rested.c.claim_id, serve.c.src_id, rested.c.rest_id, serve.c.id,
    ).select_from(rested.join(
        INTENTION, sa.and_(parent.id == rested.c.intention_id, parent.unit == unit),
    ).join(serve, sa.and_(serve.c.unit == unit, serve.c.dst_id == parent.id))).where(
        parent.form == "maintain", serve.c.type == "serves", serve.c.state == "live",
        visible_to_reads(serve), visible_to_reads(INTENTION),
    )
    return direct.union_all(indirect).subquery()


async def rebuild_lessons_for_verdict(
    conn: AsyncConnection, graph: SqlCognitiveGraph,
    *, unit: str, intention_id: str, at: datetime,
) -> None:
    resting = resting_verdict_paths(unit)
    claims = tuple(await conn.scalars(sa.select(resting.c.claim_id).where(
        resting.c.intention_id == intention_id,
    ).distinct().order_by(resting.c.claim_id)))
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
    resting = resting_verdict_paths(unit, claim_id)
    paths = (await conn.execute(sa.select(INTENTION_VERDICT, resting.c.rest_id,
                                          resting.c.serve_id).join(
        resting, resting.c.intention_id == v.intention_id,
    ).where(
        v.unit == unit,
        v.ending.in_(("achieved", "leaked")), visible_to_reads(INTENTION_VERDICT),
    ).order_by(v.ended_at, v.intention_id))).mappings().all()
    verdicts = tuple({path["intention_id"]: path for path in paths}.values())
    edge_ids = {path[key] for path in paths for key in ("rest_id", "serve_id") if path[key]}
    edges = (await conn.execute(sa.select(EDGE).where(
        EDGE.c.unit == unit, EDGE.c.id.in_(edge_ids), visible_to_reads(EDGE),
    ).order_by(EDGE.c.id))).mappings().all() if edge_ids else ()
    await _write(conn, graph, lesson, verdicts, edges, at)


async def _write(
    conn: AsyncConnection, graph: SqlCognitiveGraph, lesson: RowData,
    verdicts: Sequence[sa.RowMapping], edges: Sequence[sa.RowMapping], at: datetime,
) -> None:
    unit, claim_id = lesson["unit"], lesson["id"]
    support = sum(row["ending"] == "achieved" for row in verdicts)
    contradiction = len(verdicts) - support
    row = LESSON_TALLY.c
    prior = (await conn.execute(sa.select(row.support, row.contradiction, row.asserted_at).where(
        row.unit == unit, row.claim_id == claim_id, visible_to_reads(LESSON_TALLY),
    ))).one_or_none()
    if prior is not None and (prior.support, prior.contradiction) == (support, contradiction):
        return
    asserted_at = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()
    if prior is not None:
        asserted_at = max(asserted_at, prior.asserted_at + timedelta(microseconds=1))
    evidence_at = verdicts[-1]["ended_at"] if verdicts else lesson["asserted_at"]
    planes = await graph.aggregate_planes(
        conn, LESSON_TALLY, unit=unit, key=(claim_id,), occurred_at=evidence_at,
        asserted_at=asserted_at, identities=lesson["identities"], room=lesson["origin_room"],
        inputs=(lesson, *verdicts, *edges), minimum="internal",
    )
    values = {**planes, "support": support, "contradiction": contradiction}
    stmt = insert(LESSON_TALLY).values(**values, claim_id=claim_id)
    await conn.execute(stmt.on_conflict_do_update(
        constraint="pk_lesson_tally", set_=values,
    ))
    if (
        lesson["state"] == "live" and contradiction > support
        and planes["exposure"] != "operator"
    ):
        await SqlGraphStimuli.append_in(conn, GraphStimulus(
            unit, "dependency", claim_id, at, "lesson_tally", room=lesson["origin_room"],
            occurrence=f"{support}:{contradiction}",
        ))
