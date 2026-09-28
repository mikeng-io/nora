"""The wake evaluator — 20 §3's wake policy, run in SHADOW
(design/cognition-loop/20-observe.md §3, migration step 1).

**It fires nothing.** Every ~30 s it reads each unit's pending signals (one
read per unit, no model), applies the wake table and the brakes exactly as it
would to start a Cognition session (`nora.domain.signals.decide`), and when a
unit would wake it RECORDS the would-wake — its reason, the signal range it
would consume, the counts — as a `cognition_wake` row in `mode = "shadow"`.
That row is also the unit's shadow watermark, so the next tick sees those
signals as consumed, exactly as a session would have left them. Nothing
else: no session, no model call, no adaptor started, no message. The clocked
Dreamer adaptors keep running as they did; nothing reads the signal table for
a decision (70 §2 step 1: «the clocks still run everything»).

The week of rows this writes is the step's gate: 20 §3's starting values are
sized from it (`nora_cognition_wake_total{reason}` counts them live, the
table holds the ranges).

**The daily floors are raised here.** `time` signals are the only ones a
clock raises (20 §1), and the two daily floors (proactive's 07:00 HK and the
curator's 00:00 HK) are the only `time` producers at step 1. Each tick raises
the floors whose instant has passed within `floor_grace_seconds`, once per
unit per day (`uq_signal_time_ref` makes the repeat a no-op). The grace is
what stops a deploy at 15:00 from raising that morning's 07:00 as if due now;
a restart at 07:02 still raises it. Anchors, revisits and event dates are
`time` signals too and arrive with the agenda (step 3), which is what reads
the Register rows they come from.

**One unit's failure is its own.** Each unit's read and write is bounded and
caught; a unit whose store call fails is skipped this tick and counted
(`nora_errors_total{stage="cognition_wake"}`), and the others still run. The
watermark is only ever a written row, so a failure never moves it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from nora.domain.ports import SignalStore
from nora.domain.signals import (
    NewSignal,
    NewWake,
    UnitView,
    Verdict,
    WakeMode,
    WakePolicy,
    WakeReason,
    Watermark,
    decide,
    in_flight,
)

log = logging.getLogger("nora.app.wake")

#: How many pending signals one tick reads per unit. Well above `batch_max`
#: (80): a would-wake consumes what it read, and anything past this bound is
#: consumed by the next one.
DEFAULT_PENDING_LIMIT = 1000


@dataclass(frozen=True)
class Floor:
    """A daily floor, a local wall-clock time in the evaluator's zone."""

    hour: int
    minute: int

    @classmethod
    def parse(cls, text: str) -> Floor:
        hh, mm = text.split(":")
        return cls(int(hh), int(mm))

    @property
    def label(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


class WakeEvaluator:
    """One `tick` evaluates every unit once. Stateless between ticks: every
    brake is recomputed from the durable rows, so a restart changes nothing."""

    def __init__(
        self,
        *,
        store: SignalStore,
        units: Callable[[], Sequence[str]],
        policy: WakePolicy,
        metrics: Any,
        floors: Sequence[Floor] = (),
        tz: dt.tzinfo = dt.UTC,
        floor_grace_seconds: float = 3600.0,
        mode: WakeMode = "shadow",
        store_timeout_seconds: float = 5.0,
        pending_limit: int = DEFAULT_PENDING_LIMIT,
        settle_seconds: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`settle_seconds` (default: twice `store_timeout_seconds`) is how
        far behind the clock the pending read looks. Ids are assigned at
        INSERT and become visible at COMMIT, so two concurrent ingests can
        commit out of id order: a read that saw id N+1 before N committed
        would set the watermark past N, and N, once committed, would be
        consumed unseen. Every signal append is bounded by the same store
        timeout, so a signal recorded before `now - settle` has committed or
        failed; the read takes only those, and the wake's `at_ms` — the
        watermark's time bound — is that cutoff, so a signal recorded after
        it stays pending whatever its id."""
        self._store = store
        self._units = units
        self._policy = policy
        self._metrics = metrics
        self._floors = tuple(floors)
        self._tz = tz
        self._floor_grace_ms = int(floor_grace_seconds * 1000)
        self._mode: WakeMode = mode
        self._timeout = store_timeout_seconds
        self._limit = pending_limit
        self._clock = clock
        self._settle_ms = int(
            (2 * store_timeout_seconds if settle_seconds is None else settle_seconds) * 1000
        )

    async def tick(self) -> list[int]:
        """Evaluate every unit; return the ids of the wake rows written."""
        now_ms = int(self._clock() * 1000)
        cutoff_ms = now_ms - self._settle_ms
        units = sorted(set(self._units()))
        views: list[UnitView] = []
        for unit in units:
            view = await self._read(unit, now_ms, cutoff_ms)
            if view is not None:
                views.append(view)
        flying = sum(in_flight(v.last_wake, self._policy, now_ms) for v in views)
        written: list[int] = []
        for view in views:
            self._publish_age(view, now_ms)
            verdict = decide(view, self._policy, now_ms)
            if verdict.brake is not None:
                self._metrics.inc("nora_cognition_wake_deferred_total", brake=verdict.brake)
                continue
            if verdict.reason is None:
                continue
            if flying >= self._policy.max_concurrent_sessions:
                # The global bound (20 §3; 06-trigger.md §6). The unit stays
                # pending and is looked at again next tick.
                self._metrics.inc("nora_cognition_wake_deferred_total", brake="concurrency")
                continue
            wake_id = await self._record(view, verdict, verdict.reason, cutoff_ms)
            if wake_id is not None:
                written.append(wake_id)
                flying += 1
        return written

    def _floor_signals(self, unit: str, now_ms: int) -> list[NewSignal]:
        """The floors whose instant passed within the grace, today's and
        (just after midnight) yesterday's, as `time` signals."""
        local_now = dt.datetime.fromtimestamp(now_ms / 1000, tz=self._tz)
        out: list[NewSignal] = []
        for day in (local_now.date() - dt.timedelta(days=1), local_now.date()):
            for floor in self._floors:
                instant = dt.datetime(
                    day.year, day.month, day.day, floor.hour, floor.minute, tzinfo=self._tz,
                )
                at_ms = int(instant.timestamp() * 1000)
                if at_ms > now_ms or now_ms - at_ms > self._floor_grace_ms:
                    continue
                out.append(NewSignal(
                    unit=unit, kind="time", item="floor",
                    ref=f"floor:{floor.label}@{day.isoformat()}",
                    at_ms=at_ms, due_ms=at_ms,
                ))
        return out

    async def _read(self, unit: str, now_ms: int, cutoff_ms: int) -> UnitView | None:
        try:
            for signal in self._floor_signals(unit, now_ms):
                sid = await asyncio.wait_for(
                    self._store.append(signal=signal, recorded_at_ms=now_ms),
                    timeout=self._timeout,
                )
                if sid is not None:
                    self._metrics.inc("nora_signal_total", kind="time", cue="none")
            return await asyncio.wait_for(
                self._store.view(unit=unit, mode=self._mode, now_ms=cutoff_ms,
                                 limit=self._limit),
                timeout=self._timeout,
            )
        except Exception:
            log.exception("wake evaluator: could not read unit %s; skipped this tick", unit)
            self._metrics.inc("nora_errors_total", stage="cognition_wake")
            return None

    def _publish_age(self, view: UnitView, now_ms: int) -> None:
        """`nora_signal_pending_age_seconds{unit}`: the oldest signal that
        could wake the unit, 0 when none is pending."""
        eligible = [s.pending_since_ms for s in view.pending if not s.waits]
        age = (now_ms - min(eligible)) / 1000 if eligible else 0.0
        self._metrics.gauge("nora_signal_pending_age_seconds", max(0.0, age), unit=view.unit)

    async def _record(
        self, view: UnitView, verdict: Verdict, reason: WakeReason, at_ms: int,
    ) -> int | None:
        """`at_ms` is the read's cutoff: the watermark's time bound."""
        mark = Watermark.of(view.last_wake)
        items: dict[str, int] = {}
        cues: set[str] = set()
        for s in view.pending:
            items[f"{s.kind}:{s.item}"] = items.get(f"{s.kind}:{s.item}", 0) + 1
            cues.update(s.cues)
        wake = NewWake(
            unit=view.unit, mode=self._mode, reason=reason,
            from_signal_id=mark.through_signal_id,
            through_signal_id=verdict.through_signal_id,
            signal_count=verdict.signal_count, eligible_count=verdict.eligible_count,
            oldest_pending_ms=verdict.oldest_pending_ms,
            cut_cooldown=verdict.cut_cooldown, at_ms=at_ms,
            detail={
                "items": items,
                "cues": sorted(cues),
                "waits": sum(1 for s in view.pending if s.waits),
                "rooms": len({s.conversation_id for s in view.pending if s.conversation_id}),
            },
        )
        try:
            wake_id = await asyncio.wait_for(
                self._store.record_wake(wake=wake, recorded_at_ms=int(self._clock() * 1000)),
                timeout=self._timeout,
            )
        except Exception:
            log.exception("wake evaluator: could not record a would-wake for %s", view.unit)
            self._metrics.inc("nora_errors_total", stage="cognition_wake")
            return None
        self._metrics.inc(
            "nora_cognition_wake_total", reason=reason, mode=self._mode,
        )
        log.info(
            "cognition would-wake (%s): unit=%s reason=%s signals=%d eligible=%d "
            "through=%d cut_cooldown=%s",
            self._mode, view.unit, reason, verdict.signal_count,
            verdict.eligible_count, verdict.through_signal_id, verdict.cut_cooldown,
        )
        return wake_id


async def wake_loop(
    evaluator: WakeEvaluator, metrics: Any, *, tick_seconds: float,
) -> None:
    """The evaluator's clock: `tick` every `tick_seconds`, forever. A failing
    tick is logged and counted and never takes the loop down."""
    log.info("cognition wake evaluator started (shadow): tick every %gs", tick_seconds)
    while True:
        await asyncio.sleep(tick_seconds)
        try:
            await evaluator.tick()
        except Exception:
            log.exception("cognition wake tick failed")
            metrics.inc("nora_errors_total", stage="cognition_wake")
