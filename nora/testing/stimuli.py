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

from dataclasses import dataclass, replace
from typing import Any

from nora.domain.ports import NewCarry
from nora.domain.stimuli import (
    LastSession,
    NewStimulus,
    NewWake,
    Stimulus,
    UnitView,
    WakeRow,
    Watermark,
)


class InMemoryStimulusStore:
    def __init__(self, ledger: Any = None) -> None:
        """`ledger`: the `InMemoryCognitionDryRunStore` whose `sessions` and
        `ends` say how each wake's session went, the way the SQL store joins
        `cognition_session` and `cognition_session_end`. None: no session ever
        exists, so every wake is a watermark (the pre-session behaviour)."""
        self._ledger = ledger
        self.stimuli: list[Stimulus] = []
        self.wakes: list[WakeRow] = []
        #: `(id, NewCarry)` in append order; the newest per stimulus is current.
        self.carries: list[tuple[int, NewCarry]] = []
        self.retracted: set[int] = set()
        #: Set True to make every call raise, as a database outage does.
        self.fail = False
        #: Ids appended but not yet COMMITTED: invisible to `view`, the way a
        #: concurrent transaction's row is to another connection until it
        #: commits. Lets a test commit ids out of order.
        self.uncommitted: set[int] = set()

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("stimulus store unavailable")

    async def append(self, *, stimulus: NewStimulus, recorded_at_ms: int) -> int | None:
        self._check()
        if stimulus.kind in ("time", "rollup") and any(
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

    def _session_of(self, wake_id: int, stale_before_ms: int | None) -> LastSession | None:
        if self._ledger is None:
            return None
        found = [(sid, sess) for sid, sess, _ in self._ledger.sessions if sess.wake_id == wake_id]
        if not found:
            return None
        session_id, session = max(found, key=lambda x: x[0])
        ends = [e for _, e, _ in self._ledger.ends if e.session_id == session_id]
        if ends:
            end = ends[0]
            return LastSession(
                state="closed" if end.outcome == "closed" else "failed",
                started_at_ms=session.started_at_ms, ended_at_ms=end.at_ms,
            )
        if stale_before_ms is not None and session.started_at_ms < stale_before_ms:
            return LastSession(state="failed", started_at_ms=session.started_at_ms)
        return LastSession(state="running", started_at_ms=session.started_at_ms)

    async def view(
        self, *, unit: str, mode: str, now_ms: int, limit: int,
        stale_before_ms: int | None = None,
    ) -> UnitView:
        self._check()
        mine = sorted(
            (w for w in self.wakes if w.unit == unit and w.mode == mode), key=lambda w: w.id,
        )
        last = mine[-1] if mine else None
        last_session = None if last is None else self._session_of(last.id, stale_before_ms)
        mark_wake = None
        for wake in reversed(mine):
            state = self._session_of(wake.id, stale_before_ms)
            if state is None or state.state != "failed":
                mark_wake = wake
                break
        mark = Watermark.of(mark_wake)
        pending = [
            s for s in self.stimuli
            if s.unit == unit and s.id not in self.uncommitted and s.id not in self.retracted
            and s.pending_since_ms <= now_ms and not mark.consumed(s)
        ][:limit]
        current: dict[int, NewCarry] = {}
        for _, carry in self.carries:
            if carry.unit == unit:
                current[carry.stimulus_id] = carry
        carried = {i: c for i, c in current.items() if c.state == "carried"}
        have = {s.id for s in pending}
        for s in self.stimuli:
            if s.unit == unit and s.id in carried and s.id not in have:
                pending.append(replace(s, waits=True) if carried[s.id].waits else s)
        pending.sort(key=lambda x: x.id)
        activity = [
            s.recorded_at_ms for s in self.stimuli
            if s.unit == unit and s.kind == "message" and s.id not in self.uncommitted
        ]
        return UnitView(
            unit=unit, pending=tuple(pending),
            last_activity_ms=max(activity) if activity else None, last_wake=last,
            mark=mark, last_session=last_session,
            carry={i: c.strike for i, c in carried.items()},
        )

    async def record_carry(self, *, carry: NewCarry, recorded_at_ms: int) -> int:
        self._check()
        row_id = len(self.carries) + 1
        self.carries.append((row_id, carry))
        return row_id

    async def retract(
        self, *, unit: str, stimulus_id: int, reason: str, recorded_at_ms: int,
    ) -> bool:
        self._check()
        if stimulus_id in self.retracted:
            return False
        self.retracted.add(stimulus_id)
        return True

    async def record_wake(self, *, wake: NewWake, recorded_at_ms: int) -> int:
        self._check()
        row = WakeRow(
            id=len(self.wakes) + 1, unit=wake.unit, mode=wake.mode,
            reason=wake.reason, from_stimulus_id=wake.from_stimulus_id,
            through_stimulus_id=wake.through_stimulus_id,
            stimulus_count=wake.stimulus_count, eligible_count=wake.eligible_count,
            oldest_pending_ms=wake.oldest_pending_ms,
            cut_cooldown=wake.cut_cooldown, at_ms=wake.at_ms,
            recorded_at_ms=recorded_at_ms, detail=dict(wake.detail),
        )
        self.wakes.append(row)
        return row.id

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

    def outcomes(self, source: str) -> list[str]:
        return [r.outcome for r in self.rows if r.source == source]


__all__ = ["HealthRow", "InMemorySourceHealthLog", "InMemoryStimulusStore"]
