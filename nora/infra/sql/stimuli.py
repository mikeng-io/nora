"""`StimulusStore` over SQLAlchemy Core: the `stimulus` table and the
`cognition_wake` log (design/cognition-loop/20-observe.md §3).

Both tables are append-only. A unit's watermark is its newest `cognition_wake`
row in a mode (`nora.domain.stimuli.Watermark`), so the pending read is one
statement against `idx_stimulus_unit_id` with the watermark folded in as a
scalar subquery, and nothing is ever updated.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.ports import NewCarry
from nora.domain.stimuli import (
    LastSession,
    NewStimulus,
    NewWake,
    SessionState,
    Stimulus,
    UnitView,
    WakeRow,
    Watermark,
)
from nora.infra.sql.schema import (
    COGNITION_CARRY,
    COGNITION_SESSION,
    COGNITION_SESSION_END,
    COGNITION_WAKE,
    STIMULUS,
    STIMULUS_RETRACTION,
)


def _at(ms: int) -> datetime:
    """`recorded_at` is timestamptz (1b's system-time shape); the wake
    arithmetic is in epoch milliseconds. Millisecond values round-trip
    exactly through timestamptz's microseconds."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _ms(at: datetime) -> int:
    return round(at.timestamp() * 1000)


#: `recorded_at` as epoch milliseconds, in SQL, for the pending read.
_RECORDED_MS = sa.cast(
    sa.func.round(sa.extract("epoch", STIMULUS.c.recorded_at) * 1000), sa.BigInteger,
)


def _stimulus(r: Any) -> Stimulus:
    return Stimulus(
        id=int(r.id), unit=r.unit, kind=r.kind, item=r.item, ref=r.ref,
        at_ms=int(r.at_ms), recorded_at_ms=_ms(r.recorded_at),
        platform=r.platform, conversation_id=r.conversation_id,
        person_id=r.person_id, cues=tuple(r.cues or ()), waits=bool(r.waits),
        due_ms=None if r.due_ms is None else int(r.due_ms),
    )


def _wake(r: Any) -> WakeRow:
    return WakeRow(
        id=int(r.id), unit=r.unit, mode=r.mode, reason=r.reason,
        from_stimulus_id=int(r.from_stimulus_id),
        through_stimulus_id=int(r.through_stimulus_id),
        stimulus_count=int(r.stimulus_count), eligible_count=int(r.eligible_count),
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
    """The unit's stimuli not consumed by the watermark `(through_id,
    through_ms)` and pending by `now_ms`, ascending id.

    NOT consumed — `NOT (id <= through_id AND pending_since <= through_ms)` —
    is split into three DISJOINT range reads, each answered by its own index
    rather than by walking a unit's whole history (`stimulus` is append-only
    and grows forever): past the watermark's id (`idx_stimulus_unit_id`); at or
    under it but due after its time bound (`idx_stimulus_unit_due`); at or
    under it, undated, and recorded after its time bound
    (`idx_stimulus_unit_undated_recorded`, the late commit the evaluator's
    settle margin exists for). `tests/infra/test_stimulus_store_plan.py` holds
    the plan to those indexes."""
    since = sa.func.coalesce(STIMULUS.c.due_ms, _RECORDED_MS)
    # A retracted `self` stimulus (its self-wake was cancelled, moved or
    # superseded) is never pending: one probe of `uq_stimulus_retraction_stimulus`.
    retracted = sa.exists().where(STIMULUS_RETRACTION.c.stimulus_id == STIMULUS.c.id)
    base = (STIMULUS.c.unit == unit, since <= now_ms, ~retracted)
    under = STIMULUS.c.id <= through_id
    branches = sa.union_all(
        sa.select(STIMULUS).where(*base, STIMULUS.c.id > through_id),
        sa.select(STIMULUS).where(*base, under, STIMULUS.c.due_ms > through_ms),
        sa.select(STIMULUS).where(
            *base, under, STIMULUS.c.due_ms.is_(None),
            STIMULUS.c.recorded_at > _at(through_ms),
        ),
    ).subquery("pending")
    return sa.select(branches).order_by(branches.c.id.asc()).limit(limit)


def activity_query(*, unit: str) -> sa.Select[Any]:
    """The `recorded_at` of the unit's newest person-authored item; one probe
    of `idx_stimulus_unit_message_recorded`."""
    return sa.select(sa.func.max(STIMULUS.c.recorded_at)).where(
        STIMULUS.c.unit == unit, STIMULUS.c.kind == "message",
    )


def _failed_session(stale_before_ms: int | None) -> Any:
    """SQL for «a session of wake `w` that did not finish well»: an end row that
    is not `closed`, or no end row and started before `stale_before_ms`."""
    ended_badly = sa.and_(
        COGNITION_SESSION_END.c.id.isnot(None),
        COGNITION_SESSION_END.c.outcome != "closed",
    )
    if stale_before_ms is None:
        return ended_badly
    return sa.or_(
        ended_badly,
        sa.and_(
            COGNITION_SESSION_END.c.id.is_(None),
            COGNITION_SESSION.c.started_at_ms < stale_before_ms,
        ),
    )


def _mark_query(*, unit: str, mode: str, stale_before_ms: int | None) -> sa.Select[Any]:
    """The unit's newest wake whose session, if it has one, did not fail: the
    watermark row."""
    failed = (
        sa.select(sa.literal(1))
        .select_from(
            COGNITION_SESSION.outerjoin(
                COGNITION_SESSION_END,
                COGNITION_SESSION_END.c.session_id == COGNITION_SESSION.c.id,
            ),
        )
        .where(COGNITION_SESSION.c.wake_id == COGNITION_WAKE.c.id, _failed_session(stale_before_ms))
        .exists()
    )
    return (
        sa.select(COGNITION_WAKE)
        .where(COGNITION_WAKE.c.unit == unit, COGNITION_WAKE.c.mode == mode, ~failed)
        .order_by(COGNITION_WAKE.c.id.desc())
        .limit(1)
    )


async def _session_of(
    conn: Any, wake_id: int, stale_before_ms: int | None,
) -> LastSession | None:
    q = (
        sa.select(
            COGNITION_SESSION.c.started_at_ms,
            COGNITION_SESSION_END.c.outcome, COGNITION_SESSION_END.c.at_ms,
        )
        .select_from(
            COGNITION_SESSION.outerjoin(
                COGNITION_SESSION_END,
                COGNITION_SESSION_END.c.session_id == COGNITION_SESSION.c.id,
            ),
        )
        .where(COGNITION_SESSION.c.wake_id == wake_id)
        .order_by(COGNITION_SESSION.c.id.desc())
        .limit(1)
    )
    row = (await conn.execute(q)).first()
    if row is None:
        return None
    started = int(row.started_at_ms)
    if row.outcome is not None:
        state: SessionState = "closed" if row.outcome == "closed" else "failed"
        return LastSession(state=state, started_at_ms=started, ended_at_ms=int(row.at_ms))
    if stale_before_ms is not None and started < stale_before_ms:
        return LastSession(state="failed", started_at_ms=started)
    return LastSession(state="running", started_at_ms=started)


def _carry_query(unit: str) -> sa.Select[Any]:
    """The CURRENT carry rows for a unit that are still `carried`: the newest
    row per stimulus, when its state is `carried`."""
    newer = COGNITION_CARRY.alias("newer")
    superseded = sa.exists().where(
        newer.c.unit == COGNITION_CARRY.c.unit,
        newer.c.stimulus_id == COGNITION_CARRY.c.stimulus_id,
        newer.c.id > COGNITION_CARRY.c.id,
    )
    return sa.select(COGNITION_CARRY).where(
        COGNITION_CARRY.c.unit == unit, COGNITION_CARRY.c.state == "carried", ~superseded,
    )


#: The partial unique indexes' predicates, spelled as the indexes spell them.
_KIND_PREDICATE: dict[str, Any] = {
    "time": STIMULUS.c.kind == sa.literal_column("'time'"),
    "rollup": STIMULUS.c.kind == sa.literal_column("'rollup'"),
}


class SqlStimulusStore:
    """Satisfies `nora.domain.ports.StimulusStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, *, stimulus: NewStimulus, recorded_at_ms: int) -> int | None:
        values: dict[str, Any] = {
            "unit": stimulus.unit, "kind": stimulus.kind, "item": stimulus.item,
            "ref": stimulus.ref, "platform": stimulus.platform,
            "conversation_id": stimulus.conversation_id,
            "person_id": stimulus.person_id, "cues": list(stimulus.cues),
            "waits": int(stimulus.waits), "at_ms": stimulus.at_ms,
            "due_ms": stimulus.due_ms, "recorded_at": _at(recorded_at_ms),
        }
        stmt: Any = pg_insert(STIMULUS).values(**values)
        if stimulus.kind in ("time", "rollup"):
            # The partial unique indexes `uq_stimulus_time_ref` and
            # `uq_stimulus_rollup_ref` (0071): a floor raised on every tick,
            # or a Rollup stimulus raised again by a later run or a rebuild,
            # is written once. The conflict target must restate the index's
            # predicate for Postgres to infer it.
            #
            # The predicate is a LITERAL per kind, never `kind == <value>`:
            # a bound parameter (`kind = $1`) does not imply the index's
            # `kind = 'rollup'`, and Postgres answers «no unique or exclusion
            # constraint matching the ON CONFLICT specification» (step 2a's
            # first run on the deployment's data).
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[STIMULUS.c.unit, STIMULUS.c.ref],
                index_where=_KIND_PREDICATE[stimulus.kind],
            )
        stmt = stmt.returning(STIMULUS.c.id)
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else int(row.id)

    async def view(
        self, *, unit: str, mode: str, now_ms: int, limit: int,
        stale_before_ms: int | None = None,
    ) -> UnitView:
        """One connection: the newest wake (for the brakes), the newest wake
        whose session did not fail (the WATERMARK), the pending stimuli
        against it, the stimuli a closed session carried forward, and the
        newest person-authored item."""
        last_q = (
            sa.select(COGNITION_WAKE)
            .where(COGNITION_WAKE.c.unit == unit, COGNITION_WAKE.c.mode == mode)
            .order_by(COGNITION_WAKE.c.id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            last_row = (await conn.execute(last_q)).first()
            last = None if last_row is None else _wake(last_row)
            last_session = None
            mark_wake = None
            if last is not None:
                last_session = await _session_of(conn, last.id, stale_before_ms)
                if last_session is None or last_session.state != "failed":
                    mark_wake = last
                else:
                    mark_row = (await conn.execute(
                        _mark_query(unit=unit, mode=mode, stale_before_ms=stale_before_ms),
                    )).first()
                    mark_wake = None if mark_row is None else _wake(mark_row)
            mark = Watermark.of(mark_wake)
            pending_q = pending_query(
                unit=unit, through_id=mark.through_stimulus_id,
                through_ms=mark.through_ms, now_ms=now_ms, limit=limit,
            )
            pending = [_stimulus(r) for r in (await conn.execute(pending_q)).all()]
            carry_rows = (await conn.execute(_carry_query(unit))).all()
            carry = {int(r.stimulus_id): int(r.strike) for r in carry_rows}
            waiting = {int(r.stimulus_id) for r in carry_rows if r.waits}
            have = {s.id for s in pending}
            missing = [i for i in carry if i not in have]
            if missing:
                extra = (await conn.execute(
                    sa.select(STIMULUS).where(
                        STIMULUS.c.id.in_(missing), STIMULUS.c.unit == unit,
                    ),
                )).all()
                for r in extra:
                    row = _stimulus(r)
                    # A stimulus a person's wait closed rides the next wake and
                    # never starts one.
                    pending.append(
                        replace(row, waits=True) if row.id in waiting else row,
                    )
            pending.sort(key=lambda x: x.id)
            activity_q = activity_query(unit=unit)
            activity = (await conn.execute(activity_q)).scalar_one_or_none()
        return UnitView(
            unit=unit, pending=tuple(pending),
            last_activity_ms=None if activity is None else _ms(activity),
            last_wake=last, mark=mark, last_session=last_session, carry=carry,
        )

    async def record_carry(self, *, carry: NewCarry, recorded_at_ms: int) -> int:
        stmt = sa.insert(COGNITION_CARRY).values(
            unit=carry.unit, stimulus_id=carry.stimulus_id, strike=carry.strike,
            state=carry.state, waits=int(carry.waits), session_id=carry.session_id,
            recorded_at=_at(recorded_at_ms),
        ).returning(COGNITION_CARRY.c.id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.scalar_one())

    async def retract(
        self, *, unit: str, stimulus_id: int, reason: str, recorded_at_ms: int,
    ) -> bool:
        stmt: Any = pg_insert(STIMULUS_RETRACTION).values(
            unit=unit, stimulus_id=stimulus_id, reason=reason,
            recorded_at=_at(recorded_at_ms),
        ).on_conflict_do_nothing(index_elements=[STIMULUS_RETRACTION.c.stimulus_id])
        stmt = stmt.returning(STIMULUS_RETRACTION.c.id)
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).first()
        return row is not None

    async def record_wake(self, *, wake: NewWake, recorded_at_ms: int) -> int:
        stmt = sa.insert(COGNITION_WAKE).values(
            unit=wake.unit, mode=wake.mode, reason=wake.reason,
            from_stimulus_id=wake.from_stimulus_id,
            through_stimulus_id=wake.through_stimulus_id,
            stimulus_count=wake.stimulus_count, eligible_count=wake.eligible_count,
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
