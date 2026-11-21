"""Select the current review docket and its citable graph evidence."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql import FromClause

from nora.infra.sql.cognitive_graph_schema import CLAIM, LESSON_TALLY
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.memory.adapters.review_grades import current_grade, lesson_verdict_ids
from nora.memory.domain.pass_declarations import declaration
from nora.memory.domain.pass_inputs import ReviewCursor, ReviewInput, ReviewTarget

_STATES = ("live", "awaiting_weighing")
_EVIDENCE_PER_TARGET = 3


def _due_guesses(unit: str, now: datetime) -> sa.Select:
    c = CLAIM.c
    return sa.select(c.id, c.asserted_at, c.origin_room, c.part).where(
        c.unit == unit, c.part == "guess", c.state.in_(_STATES), c.window_end <= now,
        c.exposure != "operator", visible_to_reads(CLAIM), ~current_grade(unit, c.id),
    )


def _due_lessons(unit: str) -> sa.Select:
    t, c = LESSON_TALLY.c, CLAIM.c
    return sa.select(t.claim_id, t.asserted_at, c.origin_room, c.part).join(
        CLAIM, sa.and_(c.unit == t.unit, c.id == t.claim_id),
    ).where(
        t.unit == unit, t.contradiction > 0, c.part == "lesson", c.state.in_(_STATES),
        t.exposure != "operator", c.exposure != "operator",
        visible_to_reads(LESSON_TALLY), visible_to_reads(CLAIM),
        ~current_grade(unit, t.claim_id, newer_than=t.asserted_at),
    )


class SqlReviewTargets:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def due_review_targets(self, unit: str, now: datetime) -> tuple[ReviewTarget, ...]:
        due = _due_guesses(unit, now).union_all(_due_lessons(unit))
        async with self._engine.connect() as conn:
            return tuple(ReviewTarget(*row) for row in await conn.execute(due))


async def review_anchors(
    conn: AsyncConnection, graph: FromClause, unit: str, data: ReviewInput, now: datetime,
    room_aliases: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], ReviewCursor | None, datetime | None]:
    row = graph.c
    part = row.image["part"].astext
    priority = sa.case((part == "lesson", 0), else_=1)
    origin = row.origin_room.in_(room_aliases)
    if data.include_roomless:
        origin = origin | row.origin_room.is_(None)
    due_guesses = _due_guesses(unit, now).with_only_columns(CLAIM.c.id)
    if data.guesses is not None:
        due_guesses = due_guesses.where(CLAIM.c.id.in_(data.guesses))
    query = sa.select(
        row.graph_id, row.asserted_at, row.event_time, priority.label("priority"),
        row.image["window_end"].astext.cast(sa.DateTime(timezone=True)).label("window_end"),
    ).where(
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
        before = (key < data.after.target if data.after.after_verdict is not None
                  else key <= data.after.target)
        query = query.order_by(sa.case((before, 1), else_=0))
    cap = declaration("review").bounds(now).rows
    minimum = 1 + _EVIDENCE_PER_TARGET
    candidates = (await conn.execute(query.order_by(priority, row.graph_id)
                                    .limit(cap // minimum))).all()
    due: list[str] = []
    evidence: list[tuple[str, str]] = []
    cursor = None
    since = None
    remaining = cap
    for row in candidates:
        if remaining < minimum:
            break
        id_ = row.graph_id
        target = row.priority, id_
        cursor_after = None
        if row.window_end is None:
            after_verdict = (data.after.after_verdict
                             if data.after is not None and data.after.target == target else None)
            evidence_rows = await _lesson_evidence(
                conn, graph, unit, id_, now, after_verdict, remaining,
            )
            if len(evidence_rows) == remaining:
                # Start a fresh docket before splitting a lesson's evidence.
                if due:
                    break
                evidence_rows = evidence_rows[:-1]
                last = evidence_rows[-1]
                cursor_after = last.priority, last.graph_id
        else:
            evidence_rows = await _guess_evidence(conn, graph, row.asserted_at, row.window_end, now)
        refs = tuple(dict.fromkeys(item.graph_id for item in evidence_rows))
        since = min(since or now, row.event_time, *(item.event_time for item in evidence_rows))
        due.append(id_)
        evidence.extend((ref, id_) for ref in refs)
        remaining -= 1 + len(refs)
        cursor = ReviewCursor(target, cursor_after)
    return tuple(due), tuple(evidence), cursor, since


async def _lesson_evidence(
    conn: AsyncConnection, graph: FromClause, unit: str, lesson: str, now: datetime,
    after: tuple[int, str] | None, limit: int,
) -> list[sa.Row]:
    row = graph.c
    tally = sa.and_(row.source == "lesson_tally", row.image["claim_id"].astext == lesson)
    priority = sa.case((tally, 0), (row.image["ending"].astext == "leaked", 1), else_=2)
    attached = tally | row.graph_id.in_(lesson_verdict_ids(unit, lesson))
    if after is not None:
        attached &= tally | (sa.tuple_(priority, row.graph_id) > after)
    result = await conn.execute(sa.select(
        row.graph_id, row.event_time, priority.label("priority"),
    ).where(attached, row.event_time <= now).order_by(priority, row.graph_id).limit(limit))
    return list(result)


async def _guess_evidence(
    conn: AsyncConnection, graph: FromClause, start: datetime, end: datetime, now: datetime,
) -> list[sa.Row]:
    row = graph.c
    source_health = row.source == "source_health"
    computed = row.kind == "aggregate"
    ordinary = sa.and_(row.kind.in_(("item", "aggregate")), ~source_health)
    result: list[sa.Row] = []
    for condition, cap in (
        (ordinary & row.event_time.between(start, end), _EVIDENCE_PER_TARGET - 1),
        ((source_health | computed) & row.event_time.between(end, now), 1),
    ):
        result.extend(await conn.execute(sa.select(row.graph_id, row.event_time).where(
            condition, row.state == "live",
        ).order_by(source_health.desc(), row.event_time.desc(), row.graph_id).limit(cap)))
    return result
