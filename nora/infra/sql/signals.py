"""`SignalStore` over SQLAlchemy Core: the `signal` table and the
`cognition_wake` log (design/cognition-loop/20-observe.md §3).

Both tables are append-only. A unit's watermark is its newest `cognition_wake`
row in a mode (`nora.domain.signals.Watermark`), so the pending read is one
statement against `idx_signal_unit_id` with the watermark folded in as a
scalar subquery, and nothing is ever updated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.signals import NewSignal, NewWake, Signal, UnitView, WakeRow
from nora.infra.sql.schema import COGNITION_WAKE, SIGNAL


def _at(ms: int) -> datetime:
    """`recorded_at` is timestamptz (1b's system-time shape); the wake
    arithmetic is in epoch milliseconds. Millisecond values round-trip
    exactly through timestamptz's microseconds."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _ms(at: datetime) -> int:
    return round(at.timestamp() * 1000)


#: `recorded_at` as epoch milliseconds, in SQL, for the pending read.
_RECORDED_MS = sa.cast(
    sa.func.round(sa.extract("epoch", SIGNAL.c.recorded_at) * 1000), sa.BigInteger,
)


def _signal(r: Any) -> Signal:
    return Signal(
        id=int(r.id), unit=r.unit, kind=r.kind, item=r.item, ref=r.ref,
        at_ms=int(r.at_ms), recorded_at_ms=_ms(r.recorded_at),
        platform=r.platform, conversation_id=r.conversation_id,
        person_id=r.person_id, cues=tuple(r.cues or ()), waits=bool(r.waits),
        due_ms=None if r.due_ms is None else int(r.due_ms),
    )


def _wake(r: Any) -> WakeRow:
    return WakeRow(
        id=int(r.id), unit=r.unit, mode=r.mode, reason=r.reason,
        from_signal_id=int(r.from_signal_id),
        through_signal_id=int(r.through_signal_id),
        signal_count=int(r.signal_count), eligible_count=int(r.eligible_count),
        oldest_pending_ms=None if r.oldest_pending_ms is None else int(r.oldest_pending_ms),
        cut_cooldown=bool(r.cut_cooldown), at_ms=int(r.at_ms),
        recorded_at_ms=_ms(r.recorded_at), detail=_detail(r.detail),
    )


def _detail(raw: Any) -> dict[str, object]:
    """asyncpg hands `jsonb` back as text unless a codec is registered, and
    this engine registers none."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    return dict(raw)


def pending_query(
    *, unit: str, through_id: int, through_ms: int, now_ms: int, limit: int,
) -> sa.Select[Any]:
    """The unit's signals not consumed by the watermark `(through_id,
    through_ms)` and pending by `now_ms`, ascending id.

    NOT consumed — `NOT (id <= through_id AND pending_since <= through_ms)` —
    is split into three DISJOINT range reads, each answered by its own index
    rather than by walking a unit's whole history (`signal` is append-only
    and grows forever): past the watermark's id (`idx_signal_unit_id`); at or
    under it but due after its time bound (`idx_signal_unit_due`); at or
    under it, undated, and recorded after its time bound
    (`idx_signal_unit_undated_recorded`, the late commit the evaluator's
    settle margin exists for). `tests/infra/test_signal_store_plan.py` holds
    the plan to those indexes."""
    since = sa.func.coalesce(SIGNAL.c.due_ms, _RECORDED_MS)
    base = (SIGNAL.c.unit == unit, since <= now_ms)
    under = SIGNAL.c.id <= through_id
    branches = sa.union_all(
        sa.select(SIGNAL).where(*base, SIGNAL.c.id > through_id),
        sa.select(SIGNAL).where(*base, under, SIGNAL.c.due_ms > through_ms),
        sa.select(SIGNAL).where(
            *base, under, SIGNAL.c.due_ms.is_(None),
            SIGNAL.c.recorded_at > _at(through_ms),
        ),
    ).subquery("pending")
    return sa.select(branches).order_by(branches.c.id.asc()).limit(limit)


def activity_query(*, unit: str) -> sa.Select[Any]:
    """The `recorded_at` of the unit's newest person-authored item; one probe
    of `idx_signal_unit_message_recorded`."""
    return sa.select(sa.func.max(SIGNAL.c.recorded_at)).where(
        SIGNAL.c.unit == unit, SIGNAL.c.kind == "message",
    )


class SqlSignalStore:
    """Satisfies `nora.domain.ports.SignalStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, *, signal: NewSignal, recorded_at_ms: int) -> int | None:
        values: dict[str, Any] = {
            "unit": signal.unit, "kind": signal.kind, "item": signal.item,
            "ref": signal.ref, "platform": signal.platform,
            "conversation_id": signal.conversation_id,
            "person_id": signal.person_id, "cues": list(signal.cues),
            "waits": int(signal.waits), "at_ms": signal.at_ms,
            "due_ms": signal.due_ms, "recorded_at": _at(recorded_at_ms),
        }
        stmt: Any = pg_insert(SIGNAL).values(**values)
        if signal.kind == "time":
            # The partial unique index `uq_signal_time_ref`: a floor raised on
            # every tick is written once. The conflict target must restate
            # the index's predicate for Postgres to infer it.
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[SIGNAL.c.unit, SIGNAL.c.ref],
                index_where=SIGNAL.c.kind == "time",
            )
        stmt = stmt.returning(SIGNAL.c.id)
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else int(row.id)

    async def view(self, *, unit: str, mode: str, now_ms: int, limit: int) -> UnitView:
        """Three reads on one connection: the newest wake (the watermark), the
        pending signals against it, and the newest person-authored item."""
        last_q = (
            sa.select(COGNITION_WAKE)
            .where(COGNITION_WAKE.c.unit == unit, COGNITION_WAKE.c.mode == mode)
            .order_by(COGNITION_WAKE.c.id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            last_row = (await conn.execute(last_q)).first()
            last = None if last_row is None else _wake(last_row)
            through_id = 0 if last is None else last.through_signal_id
            through_ms = 0 if last is None else last.at_ms
            pending_q = pending_query(
                unit=unit, through_id=through_id, through_ms=through_ms,
                now_ms=now_ms, limit=limit,
            )
            pending = tuple(_signal(r) for r in (await conn.execute(pending_q)).all())
            activity_q = activity_query(unit=unit)
            activity = (await conn.execute(activity_q)).scalar_one_or_none()
        return UnitView(
            unit=unit, pending=pending,
            last_activity_ms=None if activity is None else _ms(activity),
            last_wake=last,
        )

    async def record_wake(self, *, wake: NewWake, recorded_at_ms: int) -> int:
        stmt = sa.insert(COGNITION_WAKE).values(
            unit=wake.unit, mode=wake.mode, reason=wake.reason,
            from_signal_id=wake.from_signal_id,
            through_signal_id=wake.through_signal_id,
            signal_count=wake.signal_count, eligible_count=wake.eligible_count,
            oldest_pending_ms=wake.oldest_pending_ms,
            cut_cooldown=int(wake.cut_cooldown),
            # JSON text cast to jsonb: the detail is a small flat mapping and
            # this keeps the dialect's JSON serializer out of the question.
            detail=sa.cast(json.dumps(wake.detail, sort_keys=True), COGNITION_WAKE.c.detail.type),
            at_ms=wake.at_ms, recorded_at=_at(recorded_at_ms),
        ).returning(COGNITION_WAKE.c.id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.scalar_one())

    async def aclose(self) -> None:
        await self._engine.dispose()
