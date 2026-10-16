"""`StimulusStore` over SQLAlchemy Core: the `stimulus` table, the
`cognition_wake` log and a tracked wake's outcome and carries
(`nora.domain.stimuli`'s state machine, DEC-242).

Every table is append-only. A unit's watermark is its newest `cognition_wake`
row in a mode that did not fail or expire (`nora.domain.stimuli.Watermark`),
so the pending read is range reads against the stimulus indexes with the
watermark folded in, and nothing is ever updated. The two writes that decide a
wake's fate (`record_wake` with a guard, `record_outcome`) serialize on one
transaction-scoped advisory lock per unit (`_lock_unit`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.ports import NewCarry, NewWakeOutcome, RecordedOutcome
from nora.domain.stimuli import (
    NO_CAUSE,
    OUTCOME_CAUSES,
    WAKE_OUTCOMES,
    NewStimulus,
    NewWake,
    Stimulus,
    UnitView,
    WakeGuard,
    WakeRow,
    Watermark,
    failure_run,
    stored_cause,
    wake_state,
)
from nora.infra.sql.schema import (
    COGNITION_CARRY,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
    STIMULUS,
    STIMULUS_RETRACTION,
)


def _at(ms: int) -> datetime:
    """`recorded_at` is timestamptz; the wake
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
        recorded_at_ms=_ms(r.recorded_at), detail=_wake_detail(r.detail),
        tracked=bool(r.tracked), unclaimed_ids=_unclaimed(r.detail),
    )


#: The `cognition_wake.detail` key that holds `NewWake.unclaimed_ids`: the
#: stimuli inside the wake's range it did not claim (DEC-245). In the
#: existing jsonb, so no migration; `WakeRow.detail` never carries it.
_UNCLAIMED_KEY = "unclaimed"


def _unclaimed(raw: Any) -> tuple[int, ...]:
    return tuple(int(i) for i in _detail(raw).get(_UNCLAIMED_KEY, ()))  # type: ignore[attr-defined]


def _wake_detail(raw: Any) -> dict[str, object]:
    return {k: v for k, v in _detail(raw).items() if k != _UNCLAIMED_KEY}


def _detail(raw: Any) -> dict[str, object]:
    """The jsonb object, from either form a row was written in: an object
    or a jsonb STRING holding the JSON (rows written with a double encoding;
    also, on some drivers, the text of the column)."""
    if raw is None:
        return {}
    while isinstance(raw, str):
        raw = json.loads(raw)
    return dict(raw)


def live_stimulus(stimulus_id: Any) -> Any:
    """THE retraction predicate: the stimulus `stimulus_id` names has not been
    taken back. Every read that can surface a stimulus applies it, from this
    one function: the pending read's three branches, the carry side door, and
    the ledger's pending self-wakes (`nora.infra.sql.cognition_store`). One
    probe of `uq_stimulus_retraction_stimulus`. The in-memory double's twin is
    `nora.testing.stimuli.live_stimulus`."""
    return ~sa.exists().where(STIMULUS_RETRACTION.c.stimulus_id == stimulus_id)


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
    # superseded) is never pending.
    base = (STIMULUS.c.unit == unit, since <= now_ms, live_stimulus(STIMULUS.c.id))
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


def _failed_wake(wake: Any, stale_before_ms: int | None) -> Any:
    """SQL for `nora.domain.stimuli.wake_state(...).state == "failed"` on the
    wake row `wake`: an outcome row that is not `closed` (FAILED), or a
    TRACKED wake with no outcome row recorded before `stale_before_ms`
    (EXPIRED). The contract suite holds the two spellings together."""
    outcome = COGNITION_WAKE_OUTCOME
    ended_badly = sa.exists().where(
        outcome.c.wake_id == wake.c.id, outcome.c.outcome != "closed",
    )
    if stale_before_ms is None:
        return ended_badly
    expired = sa.and_(
        wake.c.tracked == 1,
        ~sa.exists().where(outcome.c.wake_id == wake.c.id),
        wake.c.recorded_at < _at(stale_before_ms),
    )
    return sa.or_(ended_badly, expired)


def _mark_query(*, unit: str, mode: str, stale_before_ms: int | None) -> sa.Select[Any]:
    """The unit's newest wake that did not fail or expire: the watermark row."""
    return (
        sa.select(COGNITION_WAKE)
        .where(
            COGNITION_WAKE.c.unit == unit, COGNITION_WAKE.c.mode == mode,
            ~_failed_wake(COGNITION_WAKE, stale_before_ms),
        )
        .order_by(COGNITION_WAKE.c.id.desc())
        .limit(1)
    )


#: How many of a unit's newest wakes the failure streak looks back over. The
#: streak is capped by it, and every consumer of the streak acts long before
#: (`WakePolicy.max_consecutive_failures`), so it only has to exceed that.
FAILURE_LOOKBACK = 64


def _recent_wakes_query(unit: str, mode: str) -> sa.Select[Any]:
    """The unit's newest wakes in the mode WITH their outcome (or NULL), newest
    first: what `nora.domain.stimuli.failure_run` reads."""
    outcome = COGNITION_WAKE_OUTCOME
    return (
        sa.select(
            COGNITION_WAKE, outcome.c.outcome.label("outcome"),
            outcome.c.at_ms.label("outcome_at_ms"), outcome.c.cause.label("cause"),
        )
        .select_from(COGNITION_WAKE.outerjoin(outcome, outcome.c.wake_id == COGNITION_WAKE.c.id))
        .where(COGNITION_WAKE.c.unit == unit, COGNITION_WAKE.c.mode == mode)
        .order_by(COGNITION_WAKE.c.id.desc())
        .limit(FAILURE_LOOKBACK)
    )


def _counted_carry(carry: Any) -> Any:
    """A carry row counts only when its wake's outcome is `closed`. The write
    already guarantees it (`record_outcome` writes carries only with a
    `closed` outcome, in one transaction); this read holds it regardless of
    how a row got there."""
    return sa.exists().where(
        COGNITION_WAKE_OUTCOME.c.wake_id == carry.c.wake_id,
        COGNITION_WAKE_OUTCOME.c.outcome == "closed",
    )


def _carry_query(unit: str) -> sa.Select[Any]:
    """The CURRENT carry rows for a unit that are still `carried`: the newest
    counted row per stimulus, when its state is `carried` and its stimulus is
    live (a retracted self-wake never returns through carry)."""
    newer = COGNITION_CARRY.alias("newer")
    superseded = sa.exists().where(
        newer.c.unit == COGNITION_CARRY.c.unit,
        newer.c.stimulus_id == COGNITION_CARRY.c.stimulus_id,
        newer.c.id > COGNITION_CARRY.c.id,
        _counted_carry(newer),
    )
    return sa.select(COGNITION_CARRY).where(
        COGNITION_CARRY.c.unit == unit, COGNITION_CARRY.c.state == "carried",
        _counted_carry(COGNITION_CARRY), ~superseded,
        live_stimulus(COGNITION_CARRY.c.stimulus_id),
    )


async def _lock_unit(conn: Any, unit: str) -> None:
    """The per-unit critical section `record_wake` (guarded) and
    `record_outcome` share: a transaction-scoped advisory lock, released at
    COMMIT or ROLLBACK. Without it the two can write-skew under READ
    COMMITTED: a guard sees «no outcome» while a close sees «no later wake»,
    and both commit."""
    await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(
        sa.literal(_LOCK_CLASS), sa.func.hashtext(sa.literal(unit)),
    )))


#: The advisory lock's first key: this lock's namespace (an arbitrary constant,
#: "cognition wake" in ASCII-ish), so it cannot collide with another lock that
#: hashes a unit name.
_LOCK_CLASS = 0x636F_6777


#: The partial unique indexes' predicates, spelled as the indexes spell them.
_KIND_PREDICATE: dict[str, Any] = {
    "time": STIMULUS.c.kind == sa.literal_column("'time'"),
    "rollup": STIMULUS.c.kind == sa.literal_column("'rollup'"),
    "social": STIMULUS.c.kind == sa.literal_column("'social'"),
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
        if stimulus.kind in _KIND_PREDICATE:
            # The partial unique indexes `uq_stimulus_time_ref`,
            # `uq_stimulus_rollup_ref` (0071) and `uq_stimulus_social_ref`
            # (0108): a floor raised on every tick, a Rollup stimulus raised
            # again by a later run or a rebuild, or a read announced again, is
            # written once. The conflict target must restate the index's
            # predicate for Postgres to infer it.
            #
            # The predicate is a LITERAL per kind, never `kind == <value>`:
            # a bound parameter (`kind = $1`) does not imply the index's
            # `kind = 'rollup'`, and Postgres answers «no unique or exclusion
            # constraint matching the ON CONFLICT specification».
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
                outcome_row = (await conn.execute(
                    sa.select(
                        COGNITION_WAKE_OUTCOME.c.outcome, COGNITION_WAKE_OUTCOME.c.at_ms,
                        COGNITION_WAKE_OUTCOME.c.cause,
                    ).where(COGNITION_WAKE_OUTCOME.c.wake_id == last.id),
                )).first()
                last_session = wake_state(
                    tracked=last.tracked, recorded_at_ms=last.recorded_at_ms,
                    outcome=None if outcome_row is None else str(outcome_row.outcome),
                    outcome_at_ms=None if outcome_row is None else int(outcome_row.at_ms),
                    stale_before_ms=stale_before_ms,
                    cause=None if outcome_row is None else str(outcome_row.cause),
                )
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
            # Two side doors past the range read: what a closed session carried,
            # and what the watermark's wake left UNCLAIMED, which the range
            # would call consumed. Both name stimuli that were pending.
            missing = sorted(({*carry, *mark.unclaimed}) - have)
            if missing:
                extra = (await conn.execute(
                    sa.select(STIMULUS).where(
                        STIMULUS.c.id.in_(missing), STIMULUS.c.unit == unit,
                        live_stimulus(STIMULUS.c.id),
                    ),
                )).all()
                for r in extra:
                    row = _stimulus(r)
                    # A stimulus a person's wait closed rides the next wake and
                    # never starts one.
                    pending.append(
                        replace(row, waits=True) if row.id in waiting else row,
                    )
            # ONE bound for the whole read, oldest first, the same in both stores
            # (`InMemoryStimulusStore.view`, bound by the contract suite): the
            # side doors are not exempt from `limit`. Oldest-first is what keeps
            # a truncated read safe: whatever is cut has a higher id than
            # everything shown, and a wake's `through_stimulus_id` is the
            # highest id it claimed, so the cut stimuli stay pending.
            pending.sort(key=lambda x: x.id)
            del pending[limit:]
            activity_q = activity_query(unit=unit)
            activity = (await conn.execute(activity_q)).scalar_one_or_none()
            recent = (await conn.execute(_recent_wakes_query(unit, mode))).all()
        failures, agenda_failures, failed_mark = failure_run(
            (
                _wake(r),
                wake_state(
                    tracked=bool(r.tracked), recorded_at_ms=_ms(r.recorded_at),
                    outcome=None if r.outcome is None else str(r.outcome),
                    outcome_at_ms=None if r.outcome_at_ms is None else int(r.outcome_at_ms),
                    stale_before_ms=stale_before_ms,
                    cause=None if r.cause is None else str(r.cause),
                ),
            )
            for r in recent
        )
        return UnitView(
            unit=unit, pending=tuple(pending),
            last_activity_ms=None if activity is None else _ms(activity),
            last_wake=last, mark=mark, last_session=last_session, carry=carry,
            failures=failures, agenda_failures=agenda_failures, failed_mark=failed_mark,
        )

    async def record_outcome(
        self, *, outcome: NewWakeOutcome, carries: Sequence[NewCarry] = (),
        recorded_at_ms: int,
    ) -> RecordedOutcome:
        _check_outcome(outcome, carries)
        o = COGNITION_WAKE_OUTCOME
        async with self._engine.begin() as conn:
            await _lock_unit(conn, outcome.unit)
            wake_unit = (await conn.execute(
                sa.select(COGNITION_WAKE.c.unit).where(COGNITION_WAKE.c.id == outcome.wake_id),
            )).scalar_one_or_none()
            _check_outcome_wake(outcome, wake_unit)
            existing = (await conn.execute(
                sa.select(o.c.outcome).where(o.c.wake_id == outcome.wake_id),
            )).first()
            if existing is not None:
                return RecordedOutcome(outcome=str(existing.outcome), written=False)
            value, detail, rows = outcome.outcome, outcome.detail, list(carries)
            cause = outcome.cause
            if value == "closed":
                this = COGNITION_WAKE.alias("this")
                later = (await conn.execute(
                    sa.select(sa.func.min(COGNITION_WAKE.c.id)).select_from(
                        COGNITION_WAKE.join(
                            this,
                            sa.and_(
                                this.c.id == outcome.wake_id,
                                this.c.unit == COGNITION_WAKE.c.unit,
                                this.c.mode == COGNITION_WAKE.c.mode,
                            ),
                        ),
                    ).where(COGNITION_WAKE.c.id > outcome.wake_id),
                )).scalar_one_or_none()
                if later is not None:
                    value, rows, cause = "timeout", [], "infra"
                    detail = _superseded(later, detail)
            await conn.execute(sa.insert(o).values(
                wake_id=outcome.wake_id, unit=outcome.unit, session_id=outcome.session_id,
                outcome=value, detail=detail, at_ms=outcome.at_ms, cause=stored_cause(value, cause),
                recorded_at=_at(recorded_at_ms),
            ))
            if rows:
                await conn.execute(sa.insert(COGNITION_CARRY), [
                    {
                        "unit": c.unit, "stimulus_id": c.stimulus_id, "strike": c.strike,
                        "state": c.state, "waits": int(c.waits), "wake_id": outcome.wake_id,
                        "session_id": c.session_id, "recorded_at": _at(recorded_at_ms),
                    }
                    for c in rows
                ])
        return RecordedOutcome(outcome=value, written=True)

    async def open_wakes(self, *, recorded_before_ms: int) -> Sequence[WakeRow]:
        stmt = (
            sa.select(COGNITION_WAKE)
            .where(
                COGNITION_WAKE.c.tracked == 1,
                COGNITION_WAKE.c.recorded_at < _at(recorded_before_ms),
                ~sa.exists().where(COGNITION_WAKE_OUTCOME.c.wake_id == COGNITION_WAKE.c.id),
            )
            .order_by(COGNITION_WAKE.c.id)
        )
        async with self._engine.connect() as conn:
            return [_wake(r) for r in (await conn.execute(stmt)).all()]

    async def retract_self(
        self, *, unit: str, wake_ref: str, keep: int | None, reason: str,
        recorded_at_ms: int,
    ) -> int:
        standing = sa.select(
            STIMULUS.c.unit, STIMULUS.c.id, sa.literal(reason),
            sa.literal(_at(recorded_at_ms)),
        ).where(
            STIMULUS.c.unit == unit, STIMULUS.c.kind == "self",
            STIMULUS.c.item == wake_ref, live_stimulus(STIMULUS.c.id),
        )
        if keep is not None:
            standing = standing.where(STIMULUS.c.id != keep)
        stmt: Any = pg_insert(STIMULUS_RETRACTION).from_select(
            ["unit", "stimulus_id", "reason", "recorded_at"], standing,
        ).on_conflict_do_nothing(index_elements=[STIMULUS_RETRACTION.c.stimulus_id])
        stmt = stmt.returning(STIMULUS_RETRACTION.c.id)
        async with self._engine.begin() as conn:
            rows = (await conn.execute(stmt)).all()
        return len(rows)

    async def count_rollup(
        self, *, unit: str, person_id: str, item_prefix: str, from_ms: int, to_ms: int,
    ) -> int:
        stmt = sa.select(sa.func.count()).select_from(STIMULUS).where(
            STIMULUS.c.unit == unit, STIMULUS.c.kind == "rollup",
            STIMULUS.c.person_id == person_id,
            STIMULUS.c.item.startswith(item_prefix, autoescape=True),
            STIMULUS.c.at_ms >= from_ms, STIMULUS.c.at_ms < to_ms,
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

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

    async def record_wake(
        self, *, wake: NewWake, recorded_at_ms: int, guard: WakeGuard | None = None,
    ) -> int | None:
        check_wake_guard(wake, guard)
        stmt = sa.insert(COGNITION_WAKE).values(
            unit=wake.unit, mode=wake.mode, reason=wake.reason,
            from_stimulus_id=wake.from_stimulus_id,
            through_stimulus_id=wake.through_stimulus_id,
            stimulus_count=wake.stimulus_count, eligible_count=wake.eligible_count,
            oldest_pending_ms=wake.oldest_pending_ms,
            cut_cooldown=int(wake.cut_cooldown),
            # The mapping AS ITSELF: the JSONB type serializes what it is
            # handed. `sa.cast(json.dumps(...), JSONB)` would store a jsonb STRING
            # holding the JSON (`jsonb_typeof = 'string'`, so `detail->'unclaimed'`
            # reads NULL); `_detail` reads either form.
            detail=_with_unclaimed(wake),
            at_ms=wake.at_ms, recorded_at=_at(recorded_at_ms), tracked=int(wake.tracked),
        ).returning(COGNITION_WAKE.c.id)
        async with self._engine.begin() as conn:
            if guard is not None:
                await _lock_unit(conn, wake.unit)
                newest = (await conn.execute(
                    sa.select(sa.func.max(COGNITION_WAKE.c.id)).where(
                        COGNITION_WAKE.c.unit == wake.unit, COGNITION_WAKE.c.mode == wake.mode,
                    ),
                )).scalar_one_or_none()
                if newest != guard.last_wake_id:
                    return None
                if guard.last_wake_id is not None:
                    now_outcome = (await conn.execute(
                        sa.select(COGNITION_WAKE_OUTCOME.c.outcome).where(
                            COGNITION_WAKE_OUTCOME.c.wake_id == guard.last_wake_id,
                        ),
                    )).scalar_one_or_none()
                    if now_outcome != guard.last_outcome:
                        return None
            result = await conn.execute(stmt)
            return int(result.scalar_one())

    async def aclose(self) -> None:
        await self._engine.dispose()


def _with_unclaimed(wake: NewWake) -> dict[str, object]:
    if not wake.unclaimed_ids:
        return dict(wake.detail)
    return {**wake.detail, _UNCLAIMED_KEY: sorted(wake.unclaimed_ids)}


def _check_outcome(outcome: NewWakeOutcome, carries: Sequence[NewCarry]) -> None:
    """The arguments `record_outcome` refuses before any write, in both
    implementations (`nora.testing.stimuli` calls this too)."""
    if outcome.outcome not in WAKE_OUTCOMES:
        raise ValueError(f"unknown wake outcome {outcome.outcome!r}")
    if outcome.cause not in OUTCOME_CAUSES:
        raise ValueError(f"unknown failure cause {outcome.cause!r}")
    if outcome.cause == NO_CAUSE and outcome.outcome in ("failed", "timeout"):
        raise ValueError(f"a {outcome.outcome} outcome names its cause, not {NO_CAUSE!r}")
    if carries and outcome.outcome != "closed":
        raise ValueError("carry rows are written only with a `closed` outcome")
    for carry in carries:
        if carry.unit != outcome.unit:
            raise ValueError(f"carry for unit {carry.unit!r} on a wake of {outcome.unit!r}")


def _check_outcome_wake(outcome: NewWakeOutcome, wake_unit: str | None) -> None:
    """An outcome names a wake that exists and is THIS unit's, in both
    implementations (`nora.testing.stimuli` calls this too): an outcome keyed
    by a wake nobody wrote, or by another unit's, would be a row no reader
    ever finds and a `closed` that advances nothing."""
    if wake_unit is None:
        raise ValueError(f"no cognition wake {outcome.wake_id} to record an outcome for")
    if wake_unit != outcome.unit:
        raise ValueError(
            f"wake {outcome.wake_id} belongs to unit {wake_unit!r}, not {outcome.unit!r}",
        )


def check_wake_guard(wake: NewWake, guard: WakeGuard | None) -> None:
    """A TRACKED wake is written under a guard, in both implementations
    (`nora.testing.stimuli` calls this too): an unguarded tracked write is the
    write-skew the per-unit lock exists to prevent."""
    if wake.tracked and guard is None:
        raise ValueError("a tracked wake must be recorded under a WakeGuard")


def _superseded(later_wake_id: int, detail: str) -> str:
    """The detail a refused `closed` is recorded with."""
    note = (
        f"expired: wake {later_wake_id} superseded this one and took its stimuli; "
        "its carries were not written"
    )
    return f"{note}; {detail}" if detail else note
