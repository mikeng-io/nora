"""The wake evaluator — the wake policy.

Every ~30 s it reads each unit's pending stimuli (one read per unit, no model),
applies the wake table and the brakes (`nora.domain.stimuli.decide`), and when a
unit wakes it RECORDS the wake — its reason, the stimulus range it consumes, the
counts — as a `cognition_wake` row and hands it to the session hook
(`nora.app.cognition_session.CognitionSessionRunner.on_wake`). That row is also
the unit's watermark, so the next tick sees those stimuli as consumed.

**The daily floors are raised here.** `time` stimuli are the only ones a
clock raises, and the two daily floors (07:00 and 00:00 HK by default,
`[cognition] time_floors`) are the only `time` producers. Each tick raises
the floors whose instant has passed within `floor_grace_seconds`, once per
unit per day (`uq_stimulus_time_ref` makes the repeat a no-op). The grace is
what stops a deploy at 15:00 from raising that morning's 07:00 as if due now;
a restart at 07:02 still raises it. Anchors, revisits and event dates are
`time` stimuli too and arrive with the agenda, which is what reads
the Register rows they come from.

**One unit's failure is its own.** Each unit's read and write is bounded and
caught; a unit whose store call fails is skipped this tick and counted
(`nora_errors_total{stage="cognition_wake"}`), and the others still run. The
watermark is only ever a written row, so a failure never moves it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from typing import Any

from nora.domain.ports import NewWakeOutcome, StimulusStore
from nora.domain.stimuli import (
    NO_CAUSE,
    NewStimulus,
    NewWake,
    UnitView,
    Verdict,
    WakeGuard,
    WakeHook,
    WakeMode,
    WakePolicy,
    WakeReason,
    can_wake,
    decide,
    inert_kinds_for,
    restart_detail,
    unit_in_flight,
)

log = logging.getLogger("nora.app.wake")

#: How many pending stimuli one tick reads per unit. Well above `batch_max`
#: (80): a wake consumes what it read, and anything past this bound is
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
        store: StimulusStore,
        units: Callable[[], Sequence[str]],
        policy: WakePolicy,
        metrics: Any,
        floors: Sequence[Floor] = (),
        tz: dt.tzinfo = dt.UTC,
        floor_grace_seconds: float = 3600.0,
        mode: WakeMode = "live",
        store_timeout_seconds: float = 5.0,
        pending_limit: int = DEFAULT_PENDING_LIMIT,
        settle_seconds: float | None = None,
        clock: Callable[[], float] = time.time,
        on_wake: WakeHook | None = None,
        session_units: Callable[[], Collection[str]] | None = None,
        floors_ready: bool = True,
        upcoming: Callable[[str, int], Awaitable[Sequence[tuple[str, str]]]] | None = None,
    ) -> None:
        """`settle_seconds` (default: twice `store_timeout_seconds`) is how
        far behind the clock the pending read looks. Ids are assigned at
        INSERT and become visible at COMMIT, so two concurrent ingests can
        commit out of id order: a read that saw id N+1 before N committed
        would set the watermark past N, and N, once committed, would be
        consumed unseen. Every stimulus append is bounded by the same store
        timeout, so a stimulus recorded before `now - settle` has committed or
        failed; the read takes only those, and the wake's `at_ms` — the
        watermark's time bound — is that cutoff, so a stimulus recorded after
        it stays pending whatever its id.

        `on_wake`: the session hook, typed by ONE Protocol, `nora.domain.stimuli.
        WakeHook` (keyword-only: `unit`, `view`, `wake_id`, `now_ms`), which
        `nora.app.cognition_session.CognitionSessionRunner.wake_hook` also
        returns, so the two sides cannot drift apart. Called right
        after a wake is recorded, awaited in a background task, and
        never imported by name, so this module's own `nora` imports stay
        `{nora.domain.ports, nora.domain.stimuli}`
        (`tests/architecture/test_the_wake_evaluator_cannot_act.py`'s
        invariant: the evaluator still imports nothing that can act, it only
        offers a slot the composition root may fill with something that
        does). The composition root always passes it (`[cognition] enabled`
        builds the evaluator only with a runner); without one, which only a
        test builds, a wake is recorded and run by nothing, and it occupies its
        unit for `session_seconds`.

        **With a hook the brakes count REAL sessions** (single-flight and
        `max_concurrent_sessions`; `nora.domain.stimuli`'s state machine). Every
        wake is written `tracked`; a unit is in flight while
        its task is alive in this process or its newest wake is OPEN (no
        outcome row, younger than `session_timeout_seconds`; a restart
        recomputes it from the rows), whether or not a session row was ever
        written, and it cools down from the outcome. The watermark is the
        newest wake that did not fail or expire, so a failed, timed-out,
        cancelled or never-recorded session's stimuli are pending again. The
        wake row is written under a `WakeGuard`: if, between this tick's read
        and its write, the unit's newest wake or its outcome changed, nothing
        is written and the unit is read again next tick. A hook that raises is
        logged, counted and contained: it never aborts the tick for another
        unit.

        `floors_ready` (DEC-245): whether a skill ships for the `time`
        agenda kind (`AgendaSkills.by_kind`). False, a unit whose wakes start sessions raises NO
        floor, because a session woken by one would have nothing to do.

        `upcoming(unit, now_ms)` (DEC-303): the unit's rooms holding an event
        that is coming up. With each floor it raises one `time` stimulus per
        such room, BOUND to that room (`item = "upcoming"`), so the daily look
        reads and writes where the event is. Whether the room hears anything,
        and when, is the session's judgement: no event owns a reminder."""
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
        self._on_wake = on_wake
        self._session_units = session_units
        self._floors_ready = floors_ready
        self._upcoming = upcoming
        #: Held so a background task is never garbage-collected mid-flight
        #: (the classic "Task was destroyed but it is pending" loss) and so
        #: tests can drain them deterministically instead of sleeping.
        self._background: set[asyncio.Task[None]] = set()
        #: The unit -> its live session task. Single-flight's in-process truth:
        #: a session whose row is not yet written (or whose task is alive
        #: past what the rows can say) still holds its unit.
        self._live: dict[str, asyncio.Task[None]] = {}

    @property
    def has_wake_hook(self) -> bool:
        """True when a session hook is wired. The composition root's boot check
        reads it: `[cognition] enabled` with an evaluator that holds no hook
        would record wakes forever and run nothing."""
        return self._on_wake is not None

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
        flying = {v.unit for v in views if unit_in_flight(v, self._policy, now_ms)}
        flying |= {u for u, t in self._live.items() if not t.done()}
        written: list[int] = []
        for view in views:
            self._publish_age(view, now_ms)
            verdict = decide(view, self._policy, now_ms)
            if verdict.brake is not None:
                self._metrics.inc("nora_cognition_wake_deferred_total", brake=verdict.brake)
                continue
            if verdict.reason is None:
                continue
            if len(flying) >= self._policy.max_concurrent_sessions:
                # The global bound. The unit stays pending and is looked at again next
                # tick.
                self._metrics.inc("nora_cognition_wake_deferred_total", brake="concurrency")
                continue
            wake_id = await self._record(view, verdict, verdict.reason, cutoff_ms)
            if wake_id is not None:
                written.append(wake_id)
                flying.add(view.unit)
                if view.tracked:
                    # The session sees what the wake CLAIMED and nothing else: the
                    # stimuli it left pending are the next wake's.
                    claimed = replace(
                        view, pending=tuple(
                            x for x in view.pending if x.id in verdict.claimed_ids
                        ),
                    )
                    self._schedule_on_wake(claimed, wake_id, now_ms)
        return written

    def _schedule_on_wake(self, view: UnitView, wake_id: int, now_ms: int) -> None:
        hook = self._on_wake
        if hook is None:
            return
        unit = view.unit
        try:
            outcome = hook(unit=unit, view=view, wake_id=wake_id, now_ms=now_ms)
        except Exception:
            # A hook that raises WHILE BEING CALLED (not one that fails inside
            # its awaited body) must not abort the tick for the units after
            # this one: their wakes are already decided.
            log.exception("cognition wake hook raised (unit=%s wake_id=%s)", unit, wake_id)
            self._metrics.inc("nora_errors_total", stage="cognition_session")
            return
        if not inspect.isawaitable(outcome):
            return

        async def _run() -> None:
            try:
                await outcome
            except Exception:
                log.exception("cognition wake hook failed (unit=%s wake_id=%s)", unit, wake_id)
                self._metrics.inc("nora_errors_total", stage="cognition_session")

        task = asyncio.ensure_future(_run())
        self._background.add(task)
        self._live[unit] = task

        def _done(finished: asyncio.Task[None]) -> None:
            self._background.discard(finished)
            if self._live.get(unit) is finished:
                del self._live[unit]

        task.add_done_callback(_done)

    async def reclaim_orphans(self, *, process_started_ms: int) -> int:
        """Release every wake a PREVIOUS process left open, at this process's
        start, and return how many. A tracked wake with no outcome row that was
        recorded before this process started has no session left to own it: the
        process that ran it was killed or stopped, and nothing in this one knows
        it. Left alone it holds its unit single-flight (`unit_in_flight`) for the
        rest of `session_timeout_seconds` and then expires,
        which reads as a FAILED session and doubles the unit's backoff: a restart
        would cost the unit an hour and a strike toward the failure backoff.

        Each is recorded `cancelled` with a `restart` detail. `cancelled` is the
        outcome a shutdown already writes: the watermark skips it, so the wake's
        stimuli are pending again and none is consumed unseen, and it neither counts
        toward the failure streak nor resets it (`failure_streak`), so a restart is
        never a strike (DEC-244: an outage never strikes). The cooldown then runs
        from now, as it does after any ended session.

        Never raises. A store that cannot be read or written leaves the wake open,
        and it expires on its own clock. The write
        is `record_outcome`, first-write-wins under the unit's lock, so a wake that
        was meanwhile closed (a second process, a late write) keeps its outcome."""
        try:
            orphans = await asyncio.wait_for(
                self._store.open_wakes(recorded_before_ms=process_started_ms),
                timeout=self._timeout,
            )
        except Exception:
            log.exception("wake evaluator: could not list the wakes a restart left open")
            self._metrics.inc("nora_errors_total", stage="cognition_reclaim")
            return 0
        released = 0
        for wake in orphans:
            now_ms = int(self._clock() * 1000)
            try:
                recorded = await asyncio.wait_for(
                    self._store.record_outcome(
                        outcome=NewWakeOutcome(
                            wake_id=wake.id, unit=wake.unit, outcome="cancelled", at_ms=now_ms,
                            detail=restart_detail(
                                "no process owns this wake: the one that ran it ended",
                            ),
                            cause=NO_CAUSE,
                        ),
                        recorded_at_ms=now_ms,
                    ),
                    timeout=self._timeout,
                )
            except Exception:
                log.exception("wake evaluator: could not release orphaned wake %s", wake.id)
                self._metrics.inc("nora_errors_total", stage="cognition_reclaim")
                continue
            if recorded.written:
                released += 1
                self._metrics.inc("nora_cognition_wake_reclaimed_total")
        if released:
            log.warning(
                "cognition: released %d wake(s) a previous process left open (cancelled, "
                "restart): their stimuli are pending again and no unit is held",
                released,
            )
        return released

    async def wait_in_flight(self, timeout_seconds: float) -> None:
        """Wait up to `timeout_seconds` for the sessions this evaluator started
        to finish on their own. Cancels nothing: a session still running at the
        deadline is left to `cancel_in_flight`. The shutdown's grace: a session
        in its last seconds is allowed to close, one that is not is cancelled
        and releases its wake."""
        tasks = tuple(self._background)
        if tasks and timeout_seconds > 0:
            await asyncio.wait(tasks, timeout=timeout_seconds)

    async def cancel_in_flight(self) -> None:
        """Cancel every session task this evaluator started and wait for them
        to unwind (shutdown). The hook records its wake `cancelled` while it
        unwinds (`CognitionSessionRunner.on_wake`); a task cancelled before
        its first step writes nothing, and its wake stays OPEN until it
        expires, which reads as failed: its stimuli are never consumed
        unseen. The composition root must call this at shutdown (DEC-242)."""
        tasks = tuple(self._background)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def drain(self) -> None:
        """Wait for every background `on_wake` task this evaluator has
        scheduled so far. Tests only — production never awaits its own
        background work, by design (see `on_wake`'s own docstring)."""
        if self._background:
            await asyncio.gather(*tuple(self._background), return_exceptions=True)

    def _floor_stimuli(self, unit: str, now_ms: int) -> list[NewStimulus]:
        """The floors whose instant passed within the grace, today's and
        (just after midnight) yesterday's, as `time` stimuli."""
        local_now = dt.datetime.fromtimestamp(now_ms / 1000, tz=self._tz)
        out: list[NewStimulus] = []
        for day in (local_now.date() - dt.timedelta(days=1), local_now.date()):
            for floor in self._floors:
                instant = dt.datetime(
                    day.year, day.month, day.day, floor.hour, floor.minute, tzinfo=self._tz,
                )
                at_ms = int(instant.timestamp() * 1000)
                if at_ms > now_ms or now_ms - at_ms > self._floor_grace_ms:
                    continue
                out.append(NewStimulus(
                    unit=unit, kind="time", item="floor",
                    ref=f"floor:{floor.label}@{day.isoformat()}",
                    at_ms=at_ms, due_ms=at_ms,
                ))
        return out

    async def _upcoming_stimuli(
        self, unit: str, now_ms: int, floors: Sequence[NewStimulus],
    ) -> list[NewStimulus]:
        """One room-bound `time` stimulus per room `upcoming` names, for each
        floor raised this tick; its ref carries the floor's, so a floor's look
        ahead is written once however many ticks fall in its grace. A read
        that fails is logged and raises nothing: the floor itself still
        stands, and the next tick in the grace reads again."""
        if self._upcoming is None or not floors:
            return []
        try:
            rooms = await asyncio.wait_for(self._upcoming(unit, now_ms), timeout=self._timeout)
        except Exception:
            log.exception("cognition wake: could not read the upcoming events of %s", unit)
            return []
        return [
            NewStimulus(
                unit=unit, kind="time", item="upcoming",
                ref=f"upcoming:{platform}:{conversation_id}@{floor.ref.removeprefix('floor:')}",
                at_ms=floor.at_ms, due_ms=floor.due_ms,
                platform=platform, conversation_id=conversation_id,
            )
            for floor in floors for platform, conversation_id in rooms
        ]

    async def _read(self, unit: str, now_ms: int, cutoff_ms: int) -> UnitView | None:
        try:
            tracked = self._on_wake is not None and (
                self._session_units is None or unit in set(self._session_units())
            )
            floors = self._floor_stimuli(unit, now_ms) if self._floors_ready or not tracked else []
            for stimulus in [*floors, *await self._upcoming_stimuli(unit, now_ms, floors)]:
                sid = await asyncio.wait_for(
                    self._store.append(stimulus=stimulus, recorded_at_ms=now_ms),
                    timeout=self._timeout,
                )
                if sid is not None:
                    self._metrics.inc("nora_stimulus_total", kind="time", cue="none")
            view = await asyncio.wait_for(
                self._store.view(
                    unit=unit, mode=self._mode, now_ms=cutoff_ms, limit=self._limit,
                    stale_before_ms=(
                        now_ms - int(self._policy.session_timeout_seconds * 1000)
                        if tracked else None
                    ),
                ),
                timeout=self._timeout,
            )
            live = unit in self._live and not self._live[unit].done()
            # With no skill for `time`, a `time` stimulus that is ALREADY
            # pending must not wake a session either: not raising a floor is half
            # of it.
            inert = inert_kinds_for(tracked=tracked, floors_ready=self._floors_ready)
            return await self._strike_failed(
                replace(view, tracked=tracked, live=live, inert_kinds=inert), now_ms,
            )
        except Exception:
            log.exception("wake evaluator: could not read unit %s; skipped this tick", unit)
            self._metrics.inc("nora_errors_total", stage="cognition_wake")
            return None

    async def _strike_failed(self, view: UnitView, now_ms: int) -> UnitView:
        """At `max_consecutive_failures` AGENDA failures strike what those wakes
        claimed: every pending stimulus their newest watermark consumes is
        retracted with reason `failed`, so an agenda that always fails stops, and
        a stimulus that arrived after the last agenda-failed wake is never struck
        unseen. INFRASTRUCTURE failures (provider outage, kill, database) back
        the unit off (`view.failures`) and never strike, however many in a row
        (DEC-244): a human message is not dropped for an
        outage."""
        mark = view.failed_mark
        if (
            not view.tracked or mark is None
            or view.agenda_failures < self._policy.max_consecutive_failures
        ):
            return view
        struck = [s for s in view.pending if mark.consumed(s)]
        if not struck:
            return view
        wrote = 0
        for stimulus in struck:
            if await asyncio.wait_for(
                self._store.retract(
                    unit=view.unit, stimulus_id=stimulus.id, reason="failed",
                    recorded_at_ms=now_ms,
                ),
                timeout=self._timeout,
            ):
                wrote += 1
        self._metrics.inc("nora_cognition_stimuli_struck_total", float(wrote), reason="failed")
        log.error(
            "cognition: unit %s failed %d sessions in a row because of its agenda; struck "
            "%d stimulus(es) it could not process (reason=failed)",
            view.unit, view.agenda_failures, wrote,
        )
        gone = {s.id for s in struck}
        return replace(view, pending=tuple(s for s in view.pending if s.id not in gone))

    def _publish_age(self, view: UnitView, now_ms: int) -> None:
        """`nora_stimulus_pending_age_seconds{unit}`: the oldest stimulus that
        could wake the unit, 0 when none is pending. A stimulus that waits, or
        whose kind is inert (a `time` floor while no skill ships for it), can
        never wake the unit: counting it would read a unit that is not stuck as
        backed up."""
        eligible = [s.pending_since_ms for s in view.pending if can_wake(s, view)]
        age = (now_ms - min(eligible)) / 1000 if eligible else 0.0
        self._metrics.gauge("nora_stimulus_pending_age_seconds", max(0.0, age), unit=view.unit)

    async def _record(
        self, view: UnitView, verdict: Verdict, reason: WakeReason, at_ms: int,
    ) -> int | None:
        """`at_ms` is the read's cutoff: the watermark's time bound."""
        mark = view.watermark
        items: dict[str, int] = {}
        cues: set[str] = set()
        claimed = [s for s in view.pending if s.id in verdict.claimed_ids]
        for s in claimed:
            items[f"{s.kind}:{s.item}"] = items.get(f"{s.kind}:{s.item}", 0) + 1
            cues.update(s.cues)
        tracked = view.tracked
        wake = NewWake(
            unit=view.unit, mode=self._mode, reason=reason,
            from_stimulus_id=mark.through_stimulus_id,
            through_stimulus_id=verdict.through_stimulus_id,
            stimulus_count=verdict.stimulus_count, eligible_count=verdict.eligible_count,
            oldest_pending_ms=verdict.oldest_pending_ms,
            cut_cooldown=verdict.cut_cooldown, at_ms=at_ms, tracked=tracked,
            detail={
                "items": items,
                "cues": sorted(cues),
                "waits": sum(1 for s in claimed if s.waits),
                "rooms": len({s.conversation_id for s in claimed if s.conversation_id}),
            },
            unclaimed_ids=verdict.unclaimed_ids,
        )
        # Tracked: the write re-checks, under the unit's lock, that the view
        # this verdict came from still holds (`WakeGuard`).
        guard = None
        if tracked:
            last = view.last_wake
            guard = WakeGuard(
                last_wake_id=None if last is None else last.id,
                last_outcome=None if view.last_session is None else view.last_session.outcome,
            )
        try:
            wake_id = await asyncio.wait_for(
                self._store.record_wake(
                    wake=wake, recorded_at_ms=int(self._clock() * 1000), guard=guard,
                ),
                timeout=self._timeout,
            )
        except Exception:
            log.exception("wake evaluator: could not record a wake for %s", view.unit)
            self._metrics.inc("nora_errors_total", stage="cognition_wake")
            return None
        if wake_id is None:
            log.info(
                "wake evaluator: %s changed between its read and its write; read again "
                "next tick", view.unit,
            )
            self._metrics.inc("nora_cognition_wake_deferred_total", brake="single_flight")
            return None
        self._metrics.inc(
            "nora_cognition_wake_total", reason=reason, mode=self._mode,
        )
        log.info(
            "cognition wake (%s): unit=%s reason=%s stimuli=%d eligible=%d "
            "through=%d cut_cooldown=%s",
            self._mode, view.unit, reason, verdict.stimulus_count,
            verdict.eligible_count, verdict.through_stimulus_id, verdict.cut_cooldown,
        )
        return wake_id


async def wake_loop(
    evaluator: WakeEvaluator, metrics: Any, *, tick_seconds: float,
) -> None:
    """The evaluator's clock: `tick` every `tick_seconds`, forever. A failing
    tick is logged and counted and never takes the loop down."""
    log.info("cognition wake evaluator started: tick every %gs", tick_seconds)
    while True:
        await asyncio.sleep(tick_seconds)
        try:
            await evaluator.tick()
        except Exception:
            log.exception("cognition wake tick failed")
            metrics.inc("nora_errors_total", stage="cognition_wake")
