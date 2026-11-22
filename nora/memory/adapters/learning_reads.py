"""Read scoped learning measures from intention heads and their computed verdicts."""

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, INTENTION, INTENTION_VERDICT
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.memory.adapters.intention_lineage import intention_lineages
from nora.memory.domain.learning import (
    Efficacy,
    IntentionClass,
    LearningMissing,
    LearningResult,
    LearningValue,
    TrackRecord,
    efficacy,
    share,
    track_record,
)

T = TypeVar("T")
_I, _V = INTENTION.c, INTENTION_VERDICT.c
_HEAD = _I.state.in_(("open", "ended"))


class SqlLearningReads:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def verdict_coverage(
        self, unit: str, start: datetime, end: datetime,
    ) -> LearningResult[float]:
        stmt = sa.select(
            sa.func.count(_V.ending).filter(_V.ending != "unknown"), sa.func.count(),
        ).select_from(INTENTION.outerjoin(
            INTENTION_VERDICT,
            sa.and_(
                _V.unit == _I.unit, _V.intention_id == _I.id, visible_to_reads(INTENTION_VERDICT),
            ),
        )).where(
            _I.unit == unit, _I.form == "achieve", _I.state == "ended",
            _I.ended_at >= start, _I.ended_at < end, visible_to_reads(INTENTION),
        )
        return await self._read(stmt, share)

    async def loose_share(
        self, unit: str, start: datetime, end: datetime,
    ) -> LearningResult[float]:
        loose = sa.or_(*(
            _I[f"{role}_form"] == "untyped" for role in ("done_when", "holds_while", "drop_when")
        ))
        stmt = sa.select(sa.func.count().filter(loose), sa.func.count()).where(
            _I.unit == unit, _HEAD, _I.asserted_at >= start, _I.asserted_at < end,
            visible_to_reads(INTENTION),
        )
        return await self._read(stmt, share)

    async def declared_dependence(
        self, unit: str, start: datetime, end: datetime,
    ) -> LearningResult[float]:
        lineage = intention_lineages(unit, sa.select(
            _I.id.label("head"), _I.replaces, _I.asserted_at,
        ).where(_I.unit == unit, _HEAD, visible_to_reads(INTENTION)))
        stmt = sa.select(
            sa.func.count().filter(_rests_on_lesson(unit, _I.id)), sa.func.count(),
        ).select_from(lineage.join(
            INTENTION, sa.and_(_I.unit == unit, _I.id == lineage.c.head),
        )).where(
            lineage.c.replaces.is_(None),
            lineage.c.asserted_at >= start, lineage.c.asserted_at < end,
            visible_to_reads(INTENTION),
        )
        return await self._read(stmt, share)

    async def efficacy(self, unit: str, start: datetime, end: datetime) -> LearningResult[Efficacy]:
        rests = _rests_on_lesson(unit, _V.intention_id)
        achieved = _V.ending == "achieved"
        stmt = sa.select(
            sa.func.count().filter(rests, achieved), sa.func.count().filter(rests),
            sa.func.count().filter(~rests, achieved), sa.func.count().filter(~rests),
        ).where(
            _V.unit == unit, _V.state == "live", _V.ended_at >= start, _V.ended_at < end,
            _V.ending != "unknown", visible_to_reads(INTENTION_VERDICT),
        )
        return await self._read(stmt, efficacy)

    async def track_record(
        self, unit: str, start: datetime, end: datetime, intention_class: IntentionClass,
    ) -> LearningResult[TrackRecord]:
        stmt = sa.select(*(
            sa.func.count().filter(_V.ending == ending)
            for ending in ("achieved", "dropped", "leaked", "unknown")
        )).where(
            _V.unit == unit, _V.state == "live", _V.ended_at >= start, _V.ended_at < end,
            _V.form == intention_class.form, _V.owed_to == intention_class.owed_to,
            _V.condition_form.is_not_distinct_from(intention_class.condition_form),
            visible_to_reads(INTENTION_VERDICT),
        )
        return await self._read(stmt, track_record)

    async def _read(self, stmt: sa.Select, measure: Callable[..., T | None]) -> LearningResult[T]:
        try:
            async with self._engine.connect() as conn:
                counts = (await conn.execute(stmt)).one()
        except (sa.exc.SQLAlchemyError, OSError, TimeoutError):
            return LearningMissing("unavailable")
        value = measure(*counts)
        return LearningValue(value) if value is not None else LearningMissing("nothing_found")


def _rests_on_lesson(unit: str, intention_id: sa.ColumnElement[str]) -> sa.ColumnElement[bool]:
    e, c = EDGE.c, CLAIM.c
    return sa.exists(sa.select(e.id).join(
        CLAIM, sa.and_(c.unit == e.unit, c.id == e.dst_id),
    ).where(
        e.unit == unit, e.src_id == intention_id, e.type == "rests_on", e.state == "live",
        c.part == "lesson", visible_to_reads(EDGE), visible_to_reads(CLAIM),
    ))
