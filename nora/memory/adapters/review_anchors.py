"""Select the current review docket and its citable graph evidence."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql import FromClause

from nora.infra.sql.cognitive_graph_schema import CLAIM, LESSON_TALLY
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.memory.adapters.review_grades import current_grade, lesson_verdict_ids
from nora.memory.domain.pass_declarations import declaration
from nora.memory.domain.pass_inputs import ReviewCursor, ReviewInput

_STATES = ("live", "awaiting_weighing")
_EVIDENCE_PER_TARGET = 3


def _due_guesses(unit: str, now: datetime) -> sa.Select:
    c = CLAIM.c
    return sa.select(c.id, c.origin_room).where(
        c.unit == unit, c.part == "guess", c.state.in_(_STATES), c.window_end <= now,
        visible_to_reads(CLAIM), ~current_grade(unit, c.id),
    )


def _due_lessons(unit: str) -> sa.Select:
    t, c = LESSON_TALLY.c, CLAIM.c
    return sa.select(t.claim_id, c.origin_room).join(
        CLAIM, sa.and_(c.unit == t.unit, c.id == t.claim_id),
    ).where(
        t.unit == unit, t.contradiction > 0, c.part == "lesson", c.state.in_(_STATES),
        visible_to_reads(LESSON_TALLY), visible_to_reads(CLAIM),
        ~current_grade(unit, t.claim_id, newer_than=t.asserted_at),
    )


class SqlReviewLessons:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def due_lessons(self, unit: str) -> tuple[str, ...]:
        due = _due_lessons(unit).order_by(LESSON_TALLY.c.claim_id)
        async with self._engine.connect() as conn:
            return tuple(await conn.scalars(due))

    async def due_review_origins(self, unit: str, now: datetime) -> tuple[str | None, ...]:
        origin = CLAIM.c.origin_room
        due = _due_guesses(unit, now).with_only_columns(origin).union(
            _due_lessons(unit).with_only_columns(origin),
        )
        async with self._engine.connect() as conn:
            return tuple(await conn.scalars(due))


async def review_anchors(
    conn: AsyncConnection, graph: FromClause, unit: str, data: ReviewInput, now: datetime,
    room_aliases: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], ReviewCursor | None]:
    row = graph.c
    window = row.image["window_end"].astext.cast(sa.DateTime(timezone=True))
    part = row.image["part"].astext
    priority = sa.case((part == "lesson", 0), else_=1)
    origin = row.origin_room.in_(room_aliases)
    if data.include_roomless:
        origin = origin | row.origin_room.is_(None)
    due_guesses = _due_guesses(unit, now).with_only_columns(CLAIM.c.id)
    query = sa.select(row.graph_id, row.asserted_at, window, priority).where(
        row.kind == "claim", row.state.in_(_STATES),
        origin,
        sa.or_(
            sa.and_(part == "guess", row.graph_id.in_(due_guesses)),
            sa.and_(part == "lesson", row.graph_id.in_(data.lessons)),
        ),
    )
    if data.after is not None:
        # Wrap to the head after all keys past the previous page have had a turn.
        key = sa.tuple_(priority, row.graph_id)
        query = query.order_by(sa.case((key <= data.after, 1), else_=0))
    cap = declaration("review").bounds(now).rows
    due = (await conn.execute(query.order_by(priority, row.graph_id)
                              .limit(cap // (1 + _EVIDENCE_PER_TARGET)))).all()
    if not due:
        return (), (), None
    evidence: list[tuple[str, str]] = []
    for id_, start, end, _ in due:
        if end is None:
            refs = await conn.scalars(sa.select(row.graph_id).where(
                sa.or_(sa.and_(row.source == "lesson_tally",
                              row.image["claim_id"].astext == id_),
                       row.graph_id.in_(lesson_verdict_ids(unit, id_))),
            ).order_by((row.source == "lesson_tally").desc(), row.event_time.desc(),
                       row.graph_id).limit(_EVIDENCE_PER_TARGET))
        else:
            guesses = await _guess_evidence(conn, graph, start, end, now)
            refs = tuple(dict.fromkeys(guesses))
        evidence.extend((ref, id_) for ref in refs)
    last = due[-1]
    return tuple(item.graph_id for item in due), tuple(evidence), (last[3], last.graph_id)


async def _guess_evidence(
    conn: AsyncConnection, graph: FromClause, start: datetime, end: datetime, now: datetime,
) -> tuple[str, ...]:
    row = graph.c
    source_health = row.source == "source_health"
    computed = row.kind == "aggregate"
    ordinary = sa.and_(row.kind.in_(("item", "aggregate")), ~source_health)
    result: list[str] = []
    for condition, cap in (
        (ordinary & row.event_time.between(start, end), _EVIDENCE_PER_TARGET - 1),
        ((source_health | computed) & row.event_time.between(end, now), 1),
    ):
        result.extend(await conn.scalars(sa.select(row.graph_id).where(
            condition, row.state == "live",
        ).order_by(source_health.desc(), row.event_time.desc(), row.graph_id).limit(cap)))
    return tuple(result)
