"""`CognitionStore` over SQLAlchemy Core: `cognition_session`,
`cognition_session_close` and `cognition_self_wake`.

All three tables are append-only (`nora.infra.sql.history.APPEND_ONLY`):
every method here INSERTs once and never updates or deletes a row.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.cognition_pace import PaceRow, PendingSelfWake, fold_self_wakes
from nora.domain.ports import (
    NewCognitionPace,
    NewCognitionSession,
    NewCognitionSessionClose,
)
from nora.infra.sql.schema import (
    COGNITION_SELF_WAKE,
    COGNITION_SESSION,
    COGNITION_SESSION_CLOSE,
)
from nora.infra.sql.stimuli import live_stimulus


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _rows(raw: Any) -> list[str]:
    """asyncpg hands `jsonb` back as text unless a codec is registered."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [str(x) for x in raw]


def _json(value: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
    """The value for a `jsonb` column, AS ITSELF. SQLAlchemy's `JSONB` serializes
    what it is handed; handing it `json.dumps(value)` would store a JSON STRING
    containing the JSON (`jsonb_typeof = 'string'`), so `situation->'grounding'`
    reads NULL and every reader must decode twice. Legacy rows stored as strings
    still exist: `_rows` reads either."""
    return value


class SqlCognitionStore:
    """Satisfies `nora.domain.ports.CognitionStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record_session(
        self, *, session: NewCognitionSession, recorded_at_ms: int,
    ) -> int:
        stmt = (
            sa.insert(COGNITION_SESSION)
            .values(
                unit=session.unit,
                mode=session.mode,
                wake_id=session.wake_id,
                started_at_ms=session.started_at_ms,
                from_stimulus_id=session.from_stimulus_id,
                through_stimulus_id=session.through_stimulus_id,
                situation=_json(session.situation),
                agenda=_json(session.agenda),
                doctrine_sha256=session.doctrine_sha256,
                skill_versions=_json(session.skill_versions),
                config_revision_id=session.config_revision_id,
                model_id=session.model_id,
                model_route=session.model_route,
                recorded_at=_at(recorded_at_ms),
            )
            .returning(COGNITION_SESSION.c.id)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).one()
        return int(row.id)

    async def record_close(
        self, *, close: NewCognitionSessionClose, recorded_at_ms: int,
    ) -> int:
        stmt = (
            sa.insert(COGNITION_SESSION_CLOSE)
            .values(
                session_id=close.session_id,
                item_index=close.item_index,
                kind=close.kind,
                reason=close.reason,
                strike=close.strike,
                recorded_at=_at(recorded_at_ms),
            )
            .returning(COGNITION_SESSION_CLOSE.c.id)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).one()
        return int(row.id)

    async def record_pace(
        self, *, pace: NewCognitionPace, recorded_at_ms: int,
    ) -> int:
        stmt = (
            sa.insert(COGNITION_SELF_WAKE)
            .values(
                session_id=pace.session_id,
                unit=pace.unit,
                verb=pace.verb,
                wake_ref=pace.wake_ref,
                transition=pace.transition,
                subject_rows=_json(list(pace.subject_rows)),
                why=pace.why,
                at_ms=pace.at_ms,
                outcome=pace.outcome,
                refusal_reason=pace.refusal_reason,
                refusal_detail=pace.refusal_detail,
                stimulus_id=pace.stimulus_id,
                supersedes=pace.supersedes,
                recorded_at=_at(recorded_at_ms),
            )
            .returning(COGNITION_SELF_WAKE.c.id)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).one()
        return int(row.id)

    async def pending_self_wakes(
        self, *, unit: str, since_ms: int,
    ) -> Sequence[PendingSelfWake]:
        t = COGNITION_SELF_WAKE
        recent = sa.select(t.c.wake_ref).where(
            t.c.unit == unit, t.c.recorded_at >= _at(since_ms), t.c.wake_ref.isnot(None),
        )
        stmt = sa.select(
            t.c.wake_ref, t.c.transition, t.c.outcome, t.c.subject_rows, t.c.session_id,
            t.c.stimulus_id, t.c.at_ms, t.c.supersedes,
            # The one retraction predicate the stimulus store reads with.
            sa.case(
                (t.c.stimulus_id.is_(None), sa.false()),
                else_=~live_stimulus(t.c.stimulus_id),
            ).label("retracted"),
        ).where(
            t.c.unit == unit, t.c.outcome == "recorded", t.c.wake_ref.in_(recent),
        ).order_by(t.c.id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return fold_self_wakes([
            PaceRow(
                wake_ref=r.wake_ref, transition=r.transition, outcome=r.outcome,
                subject_rows=tuple(_rows(r.subject_rows)), session_id=r.session_id,
                stimulus_id=r.stimulus_id, at_ms=r.at_ms, supersedes=r.supersedes,
                retracted=bool(r.retracted),
            )
            for r in rows
        ])

    async def count_self_wakes_created(self, *, unit: str, since_ms: int) -> int:
        t = COGNITION_SELF_WAKE
        stmt = sa.select(sa.func.count()).where(
            t.c.unit == unit, t.c.verb == "wake_at", t.c.outcome == "recorded",
            t.c.recorded_at >= _at(since_ms),
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def aclose(self) -> None:
        await self._engine.dispose()
