"""Select the current review docket and its citable graph evidence."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql import FromClause

from nora.infra.sql.cognitive_graph_schema import CLAIM, LESSON_TALLY
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.memory.adapters.review_grades import current_grade, lesson_verdict_ids
from nora.memory.domain.pass_declarations import declaration

_STATES = ("live", "awaiting_weighing")
_TIME = sa.DateTime(timezone=True)


class SqlReviewLessons:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def due_lessons(self, unit: str) -> tuple[str, ...]:
        t, c = LESSON_TALLY.c, CLAIM.c
        due = sa.select(t.claim_id).join(CLAIM, sa.and_(c.unit == t.unit, c.id == t.claim_id))
        due = due.where(
            t.unit == unit, t.contradiction > 0, c.part == "lesson", c.state.in_(_STATES),
            visible_to_reads(LESSON_TALLY), visible_to_reads(CLAIM),
            ~current_grade(unit, t.claim_id, newer_than=t.asserted_at),
        ).order_by(t.claim_id)
        async with self._engine.connect() as conn:
            return tuple(await conn.scalars(due))


async def review_anchors(
    conn: AsyncConnection, graph: FromClause, unit: str, lessons: tuple[str, ...], now: datetime,
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    row = graph.c
    window = row.image["window_end"].astext.cast(_TIME)
    due = (await conn.execute(sa.select(row.graph_id, row.asserted_at, window).where(
        row.kind == "claim", row.state.in_(_STATES),
        sa.or_(
            sa.and_(row.image["part"].astext == "guess", window <= now,
                    ~current_grade(unit, row.graph_id)),
            sa.and_(row.image["part"].astext == "lesson", row.graph_id.in_(lessons)),
        ),
    ).order_by(row.graph_id))).all()
    if not due:
        return (), ()
    cap = declaration("review").bounds(now).rows
    evidence: list[tuple[str, str]] = []
    for id_, start, end in due:
        if end is None:
            refs = await conn.scalars(sa.select(row.graph_id).where(
                sa.or_(sa.and_(row.source == "lesson_tally",
                              row.image["claim_id"].astext == id_),
                       row.graph_id.in_(lesson_verdict_ids(unit, id_))),
            ).order_by((row.source == "lesson_tally").desc(), row.event_time.desc(),
                       row.graph_id).limit(cap))
        else:
            guesses = await _guess_evidence(conn, graph, start, end, now, cap)
            refs = tuple(dict.fromkeys(guesses))
        evidence.extend((ref, id_) for ref in refs)
    return tuple(item.graph_id for item in due), tuple(evidence)


async def _guess_evidence(
    conn: AsyncConnection, graph: FromClause, start: datetime, end: datetime, now: datetime,
    cap: int,
) -> tuple[str, ...]:
    row = graph.c
    source_health = row.source == "source_health"
    computed = row.kind == "aggregate"
    ordinary = sa.and_(row.kind.in_(("item", "aggregate")), ~source_health)
    result: list[str] = []
    for condition in (
        ordinary & row.event_time.between(start, end),
        (source_health | computed) & row.event_time.between(end, now),
    ):
        result.extend(await conn.scalars(sa.select(row.graph_id).where(
            condition, row.state == "live",
        ).order_by(source_health.desc(), row.event_time.desc(), row.graph_id).limit(cap)))
    return tuple(result)
