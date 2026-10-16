"""`InMemoryStimulusStore` and `InMemorySourceHealthLog` — the doubles for
`nora.infra.sql.stimuli.SqlStimulusStore` and
`nora.infra.sql.source_health.SqlSourceHealthLog`, bound to them by
`tests/contract/test_stimulus_store_contract.py` and
`tests/contract/test_source_health_log_contract.py`.

Both are append-only lists, as the tables are. The watermark is computed from
the newest wake row exactly as the SQL does (`nora.domain.stimuli.Watermark`),
so a test on the evaluator runs against the same consumption rule production
has, not a copy of it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from typing import Any

from nora.domain.ports import CARRY_STATES, NewCarry, NewWakeOutcome, RecordedOutcome
from nora.domain.ports import HealthRow as DomainHealthRow
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


def live_stimulus(retracted: Collection[int], stimulus_id: int | None) -> bool:
    """THE retraction predicate, the double's twin of
    `nora.infra.sql.stimuli.live_stimulus`: every read that can surface a
    stimulus (pending, the carry side door, the ledger's pending self-wakes)
    asks it, and nothing else decides it."""
    return stimulus_id is None or stimulus_id not in retracted


@dataclass(frozen=True)
class OutcomeRow:
    wake_id: int
    unit: str
    outcome: str
    at_ms: int
    detail: str
    session_id: int | None
    cause: str = "infra"


class InMemoryStimulusStore:
    def __init__(self, ledger: Any = None) -> None:
        """`ledger`: the `InMemoryCognitionStore` that shares this
        store's database, as the SQL twins share one: the retraction set is
        ONE set both read (`ledger.retracted`), so the ledger's pending
        self-wakes apply the same predicate the pending read does."""
        self.stimuli: list[Stimulus] = []
        self.wakes: list[WakeRow] = []
        #: `(id, wake_id, NewCarry)` in append order; the newest counted row
        #: per stimulus is current.
        self.carries: list[tuple[int, int, NewCarry]] = []
        self.outcomes: dict[int, OutcomeRow] = {}
        self.retracted: set[int] = ledger.retracted if ledger is not None else set()
        #: Set True to make every call raise, as a database outage does.
        self.fail = False
        #: Method names that raise, one outage at a time (`"append"`,
        #: `"record_outcome"`, ...).
        self.fail_on: set[str] = set()
        #: Ids appended but not yet COMMITTED: invisible to `view`, the way a
        #: concurrent transaction's row is to another connection until it
        #: commits. Lets a test commit ids out of order.
        self.uncommitted: set[int] = set()

    def _check(self, method: str) -> None:
        if self.fail or method in self.fail_on:
            raise RuntimeError(f"stimulus store unavailable ({method})")

    def _live(self, stimulus_id: int) -> bool:
        return live_stimulus(self.retracted, stimulus_id)

    async def append(self, *, stimulus: NewStimulus, recorded_at_ms: int) -> int | None:
        self._check("append")
        if stimulus.kind in ("time", "rollup", "social") and any(
            s.kind == stimulus.kind and s.unit == stimulus.unit and s.ref == stimulus.ref
            for s in self.stimuli
        ):
            return None
        row = Stimulus(
            id=len(self.stimuli) + 1, unit=stimulus.unit, kind=stimulus.kind,
            item=stimulus.item, ref=stimulus.ref, at_ms=stimulus.at_ms,
            recorded_at_ms=recorded_at_ms, platform=stimulus.platform,
            conversation_id=stimulus.conversation_id, person_id=stimulus.person_id,
            cues=tuple(stimulus.cues), waits=stimulus.waits, due_ms=stimulus.due_ms,
        )
        self.stimuli.append(row)
        return row.id

    def _state(self, wake: WakeRow, stale_before_ms: int | None) -> Any:
        row = self.outcomes.get(wake.id)
        return wake_state(
            tracked=wake.tracked, recorded_at_ms=wake.recorded_at_ms,
            outcome=None if row is None else row.outcome,
            outcome_at_ms=None if row is None else row.at_ms,
            stale_before_ms=stale_before_ms,
            cause=None if row is None else row.cause,
        )

    def _counted(self, wake_id: int) -> bool:
        row = self.outcomes.get(wake_id)
        return row is not None and row.outcome == "closed"

    async def view(
        self, *, unit: str, mode: str, now_ms: int, limit: int,
        stale_before_ms: int | None = None,
    ) -> UnitView:
        self._check("view")
        mine = sorted(
            (w for w in self.wakes if w.unit == unit and w.mode == mode), key=lambda w: w.id,
        )
        last = mine[-1] if mine else None
        last_session = None if last is None else self._state(last, stale_before_ms)
        mark_wake = None
        for wake in reversed(mine):
            state = self._state(wake, stale_before_ms)
            if state is None or state.state != "failed":
                mark_wake = wake
                break
        mark = Watermark.of(mark_wake)
        # The range read: the watermark's OWN test (id and time), which does not
        # know the wake's `unclaimed` list, limited like the SQL read.
        pending = [
            s for s in self.stimuli
            if s.unit == unit and s.id not in self.uncommitted and self._live(s.id)
            and s.pending_since_ms <= now_ms
            and not (s.id <= mark.through_stimulus_id and s.pending_since_ms <= mark.through_ms)
        ][:limit]
        current: dict[int, NewCarry] = {}
        for _, wake_id, carry in self.carries:
            if carry.unit == unit and self._counted(wake_id):
                current[carry.stimulus_id] = carry
        carried = {
            i: c for i, c in current.items() if c.state == "carried" and self._live(i)
        }
        # The two side doors past the range read: what a closed session carried
        # and what the watermark's wake left unclaimed. Then ONE bound, oldest
        # first: the same as `SqlStimulusStore.view`.
        have = {s.id for s in pending}
        for s in self.stimuli:
            if s.unit != unit or s.id in have or not self._live(s.id):
                continue
            if s.id in carried or s.id in mark.unclaimed:
                waits = s.id in carried and carried[s.id].waits
                pending.append(replace(s, waits=True) if waits else s)
        pending.sort(key=lambda x: x.id)
        del pending[limit:]
        activity = [
            s.recorded_at_ms for s in self.stimuli
            if s.unit == unit and s.kind == "message" and s.id not in self.uncommitted
        ]
        failures, agenda_failures, failed_mark = failure_run(
            (w, self._state(w, stale_before_ms)) for w in reversed(mine)
        )
        return UnitView(
            unit=unit, pending=tuple(pending),
            last_activity_ms=max(activity) if activity else None, last_wake=last,
            mark=mark, last_session=last_session,
            carry={i: c.strike for i, c in carried.items()},
            failures=failures, agenda_failures=agenda_failures, failed_mark=failed_mark,
        )

    async def record_outcome(
        self, *, outcome: NewWakeOutcome, carries: Sequence[NewCarry] = (),
        recorded_at_ms: int,
    ) -> RecordedOutcome:
        self._check("record_outcome")
        # The same argument checks the SQL store makes before its transaction.
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
        # The same wake checks the SQL store makes under its lock.
        owner = next((w.unit for w in self.wakes if w.id == outcome.wake_id), None)
        if owner is None:
            raise ValueError(f"no cognition wake {outcome.wake_id} to record an outcome for")
        if owner != outcome.unit:
            raise ValueError(
                f"wake {outcome.wake_id} belongs to unit {owner!r}, not {outcome.unit!r}",
            )
        # Everything below runs with no await: one transaction, as the SQL
        # store's is under its lock. A carry the table's CHECK would refuse
        # refuses the WHOLE write, before any of it lands.
        existing = self.outcomes.get(outcome.wake_id)
        if existing is not None:
            return RecordedOutcome(outcome=existing.outcome, written=False)
        if any(c.state not in CARRY_STATES for c in carries):
            raise ValueError("carry state outside ck_cognition_carry_state")
        value, detail, rows = outcome.outcome, outcome.detail, list(carries)
        cause = outcome.cause
        if value == "closed":
            this = next((w for w in self.wakes if w.id == outcome.wake_id), None)
            later = None if this is None else min(
                (w.id for w in self.wakes
                 if w.unit == this.unit and w.mode == this.mode and w.id > this.id),
                default=None,
            )
            if later is not None:
                value, rows, cause = "timeout", [], "infra"
                note = (
                    f"expired: wake {later} superseded this one and took its stimuli; "
                    "its carries were not written"
                )
                detail = f"{note}; {detail}" if detail else note
        self.outcomes[outcome.wake_id] = OutcomeRow(
            wake_id=outcome.wake_id, unit=outcome.unit, outcome=value,
            at_ms=outcome.at_ms, detail=detail, session_id=outcome.session_id,
            cause=stored_cause(value, cause),
        )
        for carry in rows:
            self.carries.append((len(self.carries) + 1, outcome.wake_id, carry))
        return RecordedOutcome(outcome=value, written=True)

    async def open_wakes(self, *, recorded_before_ms: int) -> Sequence[WakeRow]:
        self._check("open_wakes")
        return sorted(
            (
                w for w in self.wakes
                if w.tracked and w.id not in self.outcomes
                and w.recorded_at_ms < recorded_before_ms
            ),
            key=lambda w: w.id,
        )

    async def count_rollup(
        self, *, unit: str, person_id: str, item_prefix: str, from_ms: int, to_ms: int,
    ) -> int:
        self._check("count_rollup")
        return sum(
            1 for s in self.stimuli
            if s.unit == unit and s.kind == "rollup" and s.person_id == person_id
            and s.item.startswith(item_prefix) and from_ms <= s.at_ms < to_ms
        )

    async def retract(
        self, *, unit: str, stimulus_id: int, reason: str, recorded_at_ms: int,
    ) -> bool:
        self._check("retract")
        if stimulus_id in self.retracted:
            return False
        self.retracted.add(stimulus_id)
        return True

    async def retract_self(
        self, *, unit: str, wake_ref: str, keep: int | None, reason: str,
        recorded_at_ms: int,
    ) -> int:
        self._check("retract_self")
        standing = [
            s.id for s in self.stimuli
            if s.unit == unit and s.kind == "self" and s.item == wake_ref
            and s.id != keep and self._live(s.id)
        ]
        self.retracted.update(standing)
        return len(standing)

    async def record_wake(
        self, *, wake: NewWake, recorded_at_ms: int, guard: WakeGuard | None = None,
    ) -> int | None:
        self._check("record_wake")
        if wake.tracked and guard is None:
            raise ValueError("a tracked wake must be recorded under a WakeGuard")
        if guard is not None:
            mine = [w.id for w in self.wakes if w.unit == wake.unit and w.mode == wake.mode]
            if (max(mine) if mine else None) != guard.last_wake_id:
                return None
            if guard.last_wake_id is not None:
                row = self.outcomes.get(guard.last_wake_id)
                if (None if row is None else row.outcome) != guard.last_outcome:
                    return None
        row_ = WakeRow(
            id=len(self.wakes) + 1, unit=wake.unit, mode=wake.mode,
            reason=wake.reason, from_stimulus_id=wake.from_stimulus_id,
            through_stimulus_id=wake.through_stimulus_id,
            stimulus_count=wake.stimulus_count, eligible_count=wake.eligible_count,
            oldest_pending_ms=wake.oldest_pending_ms,
            cut_cooldown=wake.cut_cooldown, at_ms=wake.at_ms,
            recorded_at_ms=recorded_at_ms, detail=dict(wake.detail), tracked=wake.tracked,
            unclaimed_ids=tuple(sorted(wake.unclaimed_ids)),
        )
        self.wakes.append(row_)
        return row_.id

    def seed_wake(self, unit: str, wake_id: int, *, tracked: bool = True) -> None:
        """A wake row with this id, as the evaluator would have written it, for
        a test that drives a session hook directly: an outcome names a wake
        that exists (`record_outcome` refuses one that does not)."""
        self.wakes.append(WakeRow(
            id=wake_id, unit=unit, mode="live", reason="settled", from_stimulus_id=0,
            through_stimulus_id=0, stimulus_count=0, eligible_count=0,
            oldest_pending_ms=None, cut_cooldown=False, at_ms=0, recorded_at_ms=0,
            tracked=tracked,
        ))

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class HealthRow:
    source: str
    scope: str
    event: str
    outcome: str
    at_ms: int
    detail: str = ""


class InMemorySourceHealthLog:
    def __init__(self) -> None:
        self.rows: list[HealthRow] = []
        #: Set True to make `record` answer False, as a failed INSERT does.
        self.fail = False

    async def record(
        self, *, source: str, scope: str, event: Any, outcome: str, at_ms: int,
        detail: str = "",
    ) -> bool:
        if self.fail:
            return False
        self.rows.append(HealthRow(
            source=source, scope=scope, event=str(event), outcome=outcome,
            at_ms=at_ms, detail=detail,
        ))
        return True

    async def transitions(
        self, *, source: str, since_ms: int,
    ) -> tuple[DomainHealthRow, ...] | None:
        if self.fail:
            return None
        return tuple(
            DomainHealthRow(source=r.source, scope=r.scope, event=r.event,
                            outcome=r.outcome, at_ms=r.at_ms, detail=r.detail)
            for r in sorted(self.rows, key=lambda r: r.at_ms)
            if r.source == source and r.event == "transition" and r.at_ms >= since_ms
        )

    def outcomes(self, source: str) -> list[str]:
        return [r.outcome for r in self.rows if r.source == source]


__all__ = [
    "HealthRow", "InMemorySourceHealthLog", "InMemoryStimulusStore", "OutcomeRow",
    "live_stimulus",
]
