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

from dataclasses import dataclass
from typing import Any

from nora.domain.stimuli import NewStimulus, NewWake, Stimulus, UnitView, WakeRow, Watermark


class InMemoryStimulusStore:
    def __init__(self) -> None:
        self.stimuli: list[Stimulus] = []
        self.wakes: list[WakeRow] = []
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

    async def view(self, *, unit: str, mode: str, now_ms: int, limit: int) -> UnitView:
        self._check()
        mine = [w for w in self.wakes if w.unit == unit and w.mode == mode]
        last = max(mine, key=lambda w: w.id) if mine else None
        mark = Watermark.of(last)
        pending = [
            s for s in self.stimuli
            if s.unit == unit and s.id not in self.uncommitted
            and s.pending_since_ms <= now_ms and not mark.consumed(s)
        ][:limit]
        activity = [
            s.recorded_at_ms for s in self.stimuli
            if s.unit == unit and s.kind == "message" and s.id not in self.uncommitted
        ]
        return UnitView(
            unit=unit, pending=tuple(pending),
            last_activity_ms=max(activity) if activity else None, last_wake=last,
        )

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
