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
from nora.domain.ticks import due_tick_of_ms, tick_of_ms
from nora.infra.sql.schema import (
    COGNITION_CARRY,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
    STIMULUS,
    STIMULUS_CLAIM,
    STIMULUS_RETRACTION,
)


def _at(ms: int) -> datetime:
    """`recorded_at` is timestamptz; the wake
    arithmetic is in epoch milliseconds. Millisecond values round-trip
    exactly through timestamptz's microseconds."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _ms(at: datetime) -> int:
    return round(at.timestamp() * 1000)


def pending_query(
    *, unit: str, mode: str, now_ms: int, floor: int, through_id: int,
    stale_before_ms: int | None, limit: int,
) -> sa.Select[Any]:
    """The unit's stimuli pending by `now_ms`, ascending id: live, eligible,
    and NOT claimed by a wake that did not fail — consumption is a per-row
    `stimulus_claim` fact (a wake claims a SET, `decide`), never a range the
    next wake must enumerate to honour.

    Two bounded reads, each an `idx_stimulus_unit_id` walk that stops at
    `limit` rows:

    - `id > through_id` — never claimed at all: a wake's claims always sit
      at or under its own `through`, and `through` never retreats.
    - `floor <= id <= through_id` — the residual under the watermark. The
      wake recorded `floor` (`detail.pending_floor`, the lowest id that
      could still be pending under it — computed from claims by `closed`
      wakes, which can never release), so the range is a cursor over the
      real backlog, not a walk of the unit's history. Marks written before
      the floor existed pass `floor=0`: the whole under-mark range, once —
      the next wake lands a floor.

    ELIGIBILITY splits on whether the row carries a deadline (node 18: tick
    arithmetic applies to timed deadlines; an undated stimulus keeps exact
    commit-order provenance — a message recorded at 90000 is not pending to
    a read at 88000, though both floor to the same tick)."""
    now_tick = tick_of_ms(now_ms)
    eligible = sa.or_(
        sa.and_(
            STIMULUS.c.due_ms.isnot(None),
            STIMULUS.c.due_tick <= now_tick,
        ),
        sa.and_(
            STIMULUS.c.due_ms.is_(None),
            STIMULUS.c.recorded_at <= _at(now_ms),
        ),
    )
    claimed = sa.exists().where(
        STIMULUS_CLAIM.c.stimulus_id == STIMULUS.c.id,
        COGNITION_WAKE.c.id == STIMULUS_CLAIM.c.wake_id,
        COGNITION_WAKE.c.unit == unit,
        COGNITION_WAKE.c.mode == mode,
        ~_failed_wake(COGNITION_WAKE, stale_before_ms),
    )
    base = (STIMULUS.c.unit == unit, eligible, live_stimulus(STIMULUS.c.id))
    above = (
        sa.select(STIMULUS)
        .where(*base, STIMULUS.c.id > through_id)
        .order_by(STIMULUS.c.id.asc()).limit(limit)
    )
    below = (
        sa.select(STIMULUS)
        .where(*base, STIMULUS.c.id.between(floor, through_id), ~claimed)
        .order_by(STIMULUS.c.id.asc()).limit(limit)
    )
    both = sa.union_all(above, below).subquery("pending")
    return sa.select(both).order_by(both.c.id.asc()).limit(limit)


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
        tracked=bool(r.tracked), unclaimed_ids=unclaimed_ids(r.detail),
        claimed_ids=claimed_ids(r.detail), pending_floor=pending_floor(r.detail),
    )


def _detail_ids(raw: Any, key: str) -> tuple[int, ...]:
    ids: Any = jsonb_object(raw).get(key) or ()
    while isinstance(ids, str):
        ids = json.loads(ids)
    return tuple(int(i) for i in ids or ())


def unclaimed_ids(raw: Any) -> tuple[int, ...]:
    """A wake row's unclaimed stimulus ids, whichever form its detail was written in."""
    return _detail_ids(raw, _UNCLAIMED_KEY)


def claimed_ids(raw: Any) -> tuple[int, ...]:
    """A wake row's claimed stimulus ids (`detail.claimed`), whichever form
    its detail was written in."""
    return _detail_ids(raw, _CLAIMED_KEY)


def pending_floor(raw: Any) -> int | None:
    """A wake row's `detail.pending_floor`: the lowest id under its `through`
    that may still be pending. None when the row predates it."""
    floor: Any = jsonb_object(raw).get(_PENDING_FLOOR_KEY)
    while isinstance(floor, str):
        floor = json.loads(floor)
    return None if floor is None else int(floor)


def _wake_detail(raw: Any) -> dict[str, object]:
    return {
        k: v for k, v in jsonb_object(raw).items()
        if k not in (_UNCLAIMED_KEY, _CLAIMED_KEY, _PENDING_FLOOR_KEY)
    }


def jsonb_object(raw: Any) -> dict[str, object]:
    """The jsonb object, from either form a row was written in: an object
    or a jsonb STRING holding the JSON (rows written with a double encoding;
    also, on some drivers, the text of the column)."""
    if raw is None:
        return {}
    while isinstance(raw, str):
        raw = json.loads(raw)
    return dict(raw)


#: The `cognition_wake.detail` key that holds `NewWake.unclaimed_ids`: the
#: stimuli inside the wake's range it did not claim (DEC-245) — the early-door
#: list a reader re-offers, not what keeps them pending. In the existing
#: jsonb, so no migration; `WakeRow.detail` never carries it.
_UNCLAIMED_KEY = "unclaimed"

#: The `detail` key that holds `NewWake.claimed_ids` — the same set
#: `stimulus_claim` indexes, as the wake row's own provenance.
_CLAIMED_KEY = "claimed"

#: The `detail` key `record_wake` computes: the lowest stimulus id under the
#: wake's `through` that may still be pending — the residual read's bound.
_PENDING_FLOOR_KEY = "pending_floor"


def live_stimulus(stimulus_id: Any) -> Any:
    """THE retraction predicate: the stimulus `stimulus_id` names has not been
    taken back. Every read that can surface a stimulus applies it, from this
    one function: the pending read's three branches, the carry side door, and
    the ledger's pending self-wakes (`nora.infra.sql.cognition_store`). One
    probe of `uq_stimulus_retraction_stimulus`. The in-memory double's twin is
    `nora.testing.stimuli.live_stimulus`."""
    return ~sa.exists().where(STIMULUS_RETRACTION.c.stimulus_id == stimulus_id)


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
            "due_ms": stimulus.due_ms,
            # The writable eligibility authority (node 18): the writer names
            # the tick its instant means; `ck_stimulus_due_tick` binds it.
            "due_tick": (
                None if stimulus.due_ms is None
                else due_tick_of_ms(stimulus.due_ms)
            ),
            "recorded_at": _at(recorded_at_ms),
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

    async def existing_id(self, *, unit: str, kind: str, ref: str) -> int | None:
        """`(unit, kind, ref)` carries no unique index for `kind='message'`,
        so a durable caller proves whether its append already committed
        instead of blindly writing a second row. Newest id wins."""
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(STIMULUS.c.id).where(
                    STIMULUS.c.unit == unit,
                    STIMULUS.c.kind == kind,
                    STIMULUS.c.ref == ref,
                ).order_by(STIMULUS.c.id.desc()).limit(1)
            )).first()
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
                unit=unit, mode=mode, now_ms=now_ms,
                floor=mark.pending_floor or 0,
                through_id=mark.through_stimulus_id,
                stale_before_ms=stale_before_ms, limit=limit,
            )
            raw = [_stimulus(r) for r in (await conn.execute(pending_q)).all()]
            carry_rows = (await conn.execute(_carry_query(unit))).all()
            carry = {int(r.stimulus_id): int(r.strike) for r in carry_rows}
            waiting = {int(r.stimulus_id) for r in carry_rows if r.waits}
            have = {s.id for s in raw}
            # Two side doors past the range read: what a closed session carried,
            # and what the watermark's wake left UNCLAIMED — named so a reader
            # can re-offer them before they are due (they stay pending on their
            # own; no claim was ever written for them). Both sit under the
            # watermark by construction, so the fetch is bounded by `limit`
            # like the raw read: at most `limit` of them can be shown anyway
            # (the merge is oldest-first), and the rest simply wait in their
            # claim-free state.
            missing = sorted(({*carry, *mark.unclaimed}) - have)
            pending = list(raw)
            if missing:
                extra = (await conn.execute(
                    sa.select(STIMULUS).where(
                        STIMULUS.c.id.in_(missing), STIMULUS.c.unit == unit,
                        live_stimulus(STIMULUS.c.id),
                    ).order_by(STIMULUS.c.id.asc()).limit(limit),
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
            # side doors are not exempt from `limit`. Nothing else is
            # enumerated: a stimulus the bound hides stays pending on the
            # claim probe alone — the residual read's `floor` cursor bounds
            # where it is looked for, never a list of it.
            pending.sort(key=lambda x: x.id)
            del pending[limit:]
            activity_q = activity_query(unit=unit)
            activity = (await conn.execute(activity_q)).scalar_one_or_none()
            recent = (await conn.execute(_recent_wakes_query(unit, mode))).all()
            # `claimed` on every wake the view returns carries the TABLE's
            # rows, not just `detail.claimed`: marks written before the column
            # family existed got theirs reconstructed by 0151's backfill, so a
            # strike (`failure_run`) sees the same set the pending read does.
            wake_ids = {
                w.id for w in (last, mark_wake) if w is not None
            } | {int(r.id) for r in recent}
            if wake_ids:
                rows = (await conn.execute(sa.select(
                    STIMULUS_CLAIM.c.wake_id, STIMULUS_CLAIM.c.stimulus_id,
                ).where(STIMULUS_CLAIM.c.wake_id.in_(wake_ids)))).all()
                by_wake: dict[int, list[int]] = {}
                for wid, sid in rows:
                    by_wake.setdefault(int(wid), []).append(int(sid))
                def _with_claims(w: WakeRow) -> WakeRow:
                    if w.id not in by_wake:
                        return w
                    return replace(
                        w, claimed_ids=tuple(sorted({*w.claimed_ids, *by_wake[w.id]})),
                    )
                if last is not None:
                    last = _with_claims(last)
                if mark_wake is not None:
                    mark_wake = _with_claims(mark_wake)
                recent_pairs = [
                    (_with_claims(_wake(r)), r) for r in recent
                ]
            else:
                recent_pairs = [(_wake(r), r) for r in recent]
            mark = Watermark.of(mark_wake)
        failures, agenda_failures, failed_mark = failure_run(
            (
                w,
                wake_state(
                    tracked=bool(r.tracked), recorded_at_ms=_ms(r.recorded_at),
                    outcome=None if r.outcome is None else str(r.outcome),
                    outcome_at_ms=None if r.outcome_at_ms is None else int(r.outcome_at_ms),
                    stale_before_ms=stale_before_ms,
                    cause=None if r.cause is None else str(r.cause),
                ),
            )
            for w, r in recent_pairs
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
            floor = await _pending_floor(conn, wake)
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
                # reads NULL); `jsonb_object` reads either form.
                detail=_with_detail(wake, floor),
                at_ms=wake.at_ms, recorded_at=_at(recorded_at_ms), tracked=int(wake.tracked),
            ).returning(COGNITION_WAKE.c.id)
            result = await conn.execute(stmt)
            wake_id = int(result.scalar_one())
            if wake.claimed_ids:
                await conn.execute(sa.insert(STIMULUS_CLAIM), [
                    {"unit": wake.unit, "stimulus_id": sid, "wake_id": wake_id}
                    for sid in sorted(wake.claimed_ids)
                ])
            return wake_id

    async def aclose(self) -> None:
        await self._engine.dispose()


async def _pending_floor(conn: Any, wake: NewWake) -> int:
    """The lowest stimulus id under the new wake's `through` that may still be
    pending — `detail.pending_floor`, the residual read's cursor.

    Starting at the previous floor (the newest wake's, for this unit and
    mode), the first row that is neither retracted nor claimed BY A WAKE THAT
    CLOSED — a closed wake's claims are permanent; an open or failed wake's
    are not, so only `closed` may advance the floor — is it. `through + 1`
    when nothing under `through` qualifies: the residual range is empty, and
    the floor says so instead of unbounded."""
    prev = (await conn.execute(
        sa.select(COGNITION_WAKE.c.detail)
        .where(
            COGNITION_WAKE.c.unit == wake.unit, COGNITION_WAKE.c.mode == wake.mode,
            COGNITION_WAKE.c.detail[_PENDING_FLOOR_KEY].isnot(None),
        ).order_by(COGNITION_WAKE.c.id.desc()).limit(1),
    )).scalar_one_or_none()
    floor = pending_floor(prev) or 0
    closed_claim = sa.exists().where(
        STIMULUS_CLAIM.c.stimulus_id == STIMULUS.c.id,
        COGNITION_WAKE.c.id == STIMULUS_CLAIM.c.wake_id,
        COGNITION_WAKE_OUTCOME.c.wake_id == COGNITION_WAKE.c.id,
        COGNITION_WAKE_OUTCOME.c.outcome == "closed",
    )
    lowest = (await conn.execute(
        sa.select(sa.func.min(STIMULUS.c.id)).where(
            STIMULUS.c.unit == wake.unit,
            STIMULUS.c.id.between(floor, wake.through_stimulus_id),
            live_stimulus(STIMULUS.c.id),
            ~closed_claim,
        ),
    )).scalar_one_or_none()
    return wake.through_stimulus_id + 1 if lowest is None else int(lowest)


def _with_detail(wake: NewWake, floor: int) -> dict[str, object]:
    detail: dict[str, object] = {**wake.detail, _PENDING_FLOOR_KEY: floor}
    if wake.unclaimed_ids:
        detail[_UNCLAIMED_KEY] = sorted(wake.unclaimed_ids)
    if wake.claimed_ids:
        detail[_CLAIMED_KEY] = sorted(wake.claimed_ids)
    return detail


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
