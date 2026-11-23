"""Read grade freshness and validate evidence in the admission transaction."""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, INTENTION_VERDICT, LESSON_TALLY
from nora.infra.sql.cognitive_graph_store import RowData, visible_to_reads
from nora.infra.sql.schema import SOURCE_HEALTH
from nora.memory.adapters.condition_sources import ConditionSources
from nora.memory.adapters.lesson_tally import resting_verdict_paths
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_ports import AdmissionWriteRefusedError
from nora.memory.domain.admission_proposals import CreateClaim
from nora.memory.domain.review_admission import grade_target


def current_grade(
    unit: Any, target: Any, *, newer_than: Any = None,
) -> sa.ColumnElement[bool]:
    edge = EDGE.alias("grade_links")
    e = edge.c
    query = sa.select(1).select_from(edge).where(
        e.unit == unit, e.dst_id == target, e.type == "grades", e.state == "live",
    )
    if newer_than is not None:
        grade = CLAIM.alias("grade_claims")
        g = grade.c
        query = query.join(grade, sa.and_(e.unit == g.unit, e.src_id == g.id)).where(
            g.part == "grade", g.state == "live", g.asserted_at > newer_than,
            visible_to_reads(edge), visible_to_reads(grade),
        )
    return query.exists()


async def check_grade(
    conn: AsyncConnection, *, unit: str, proposal: CreateClaim,
) -> RowData:
    c, t = CLAIM.c, LESSON_TALLY.c
    target = (await conn.execute(sa.select(CLAIM).where(
        c.unit == unit, c.id == grade_target(proposal),
        c.part.in_(("guess", "lesson")), c.state.in_(("live", "awaiting_weighing")),
        visible_to_reads(CLAIM),
    ).with_for_update())).mappings().one_or_none()
    if target is None:
        raise AdmissionWriteRefusedError(Refusal("grade_target", "links"))
    citations = proposal.citations
    newer = None
    if target["part"] == "lesson":
        newer = await conn.scalar(sa.select(t.asserted_at).where(
            t.unit == unit, t.claim_id == target["id"], visible_to_reads(LESSON_TALLY),
        ))
        if newer is None or not await _lesson_evidence(conn, target, citations):
            raise AdmissionWriteRefusedError(Refusal("no_later_evidence", "citations"))
    if await conn.scalar(sa.select(current_grade(unit, target["id"], newer_than=newer))):
        raise AdmissionWriteRefusedError(Refusal("already_graded", "links"))
    revision = newer or target["asserted_at"]
    if proposal.reviewed_at is not None and proposal.reviewed_at != revision:
        raise AdmissionWriteRefusedError(Refusal("stale_base", "target"))
    if proposal.grade == "expired" and not await _healthy_evidence(conn, target, citations):
        raise AdmissionWriteRefusedError(Refusal("no_later_evidence", "citations"))
    return target


def lesson_verdict_ids(unit: str, lesson: str) -> sa.Select[Any]:
    paths = resting_verdict_paths(unit, lesson)
    verdict = INTENTION_VERDICT.c
    query = sa.select(verdict.graph_id).join(paths, paths.c.intention_id == verdict.intention_id)
    return query.where(
        verdict.unit == unit, verdict.ending.in_(("achieved", "leaked")),
        visible_to_reads(INTENTION_VERDICT),
    ).distinct()


async def _lesson_evidence(
    conn: AsyncConnection, target: RowData, citations: tuple[str, ...],
) -> bool:
    t, v = LESSON_TALLY.c, INTENTION_VERDICT.c
    unit, id_, start = target["unit"], target["id"], target["asserted_at"]
    tally = sa.select(1).where(
        t.unit == unit, t.claim_id == id_, t.graph_id.in_(citations),
        t.asserted_at > start, visible_to_reads(LESSON_TALLY),
    )
    verdict = sa.select(1).where(
        v.unit == unit, v.graph_id.in_(citations), v.graph_id.in_(lesson_verdict_ids(unit, id_)),
        v.asserted_at > start,
    )
    return bool(await conn.scalar(sa.select(tally.exists() | verdict.exists())))


async def _healthy_evidence(
    conn: AsyncConnection, target: RowData, citations: Iterable[str],
) -> bool:
    health = SOURCE_HEALTH.c
    unit, end = target["unit"], target["window_end"]
    samples = (await conn.execute(sa.select(health.source, health.scope, health.occurred_at).where(
        health.unit == unit, health.graph_id.in_(citations), health.state == "live",
        health.occurred_at >= end, health.outcome.in_(("up", "ok")),
        visible_to_reads(SOURCE_HEALTH),
    ))).all()
    for source, scope, at in samples:
        if await _healthy_at(conn, unit, source, scope, at):
            return True
    resolver = ConditionSources(conn)
    for citation in citations:
        entry = await resolver.aggregate(unit, citation)
        if entry is None:
            continue
        table, row = entry
        at = row.get("occurred_at") or row["asserted_at"]
        if at < end:
            continue
        sources = await resolver.aggregate_sources(table, row)
        if sources and all([await _healthy_at(conn, unit, *source, at) for source in sources]):
            return True
    return False


async def _healthy_at(
    conn: AsyncConnection, unit: str, source: str, scope: str, at: datetime,
) -> bool:
    health = SOURCE_HEALTH.c
    latest = sa.select(health.outcome).where(
        health.unit == unit, health.source == source, health.scope == scope,
        health.state == "live", health.occurred_at <= at,
    ).order_by(health.at_ms.desc(), health.id.desc()).limit(1)
    return await conn.scalar(latest) in ("up", "ok")
