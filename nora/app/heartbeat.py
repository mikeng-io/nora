"""The tick: wake, find what is due, and give each conversation one turn.

WHAT THIS MODULE IS RESPONSIBLE FOR
-----------------------------------
Four things, and deliberately not a fifth:

1. **When** — a fixed tick, and the batch of rows `due_at <= now`.
2. **Whether** — `policy.toml` re-checked at FIRE time, per row.
3. **Where the turn may run** — the conversation's serialisation slot, taken
   with `try_acquire`: never queued, never waited on.
4. **What happened** — the row's terminal state, DERIVED FROM the outcome `fire`
   answered (`_state_for`: only `sent` is DONE), and one counter per reminder.

`recover` and `publish_idle_gauge` are module-level functions rather than
methods, because both must run when the loop does NOT exist: boot recovery is
most needed on the deployment that has the tick switched off, and a liveness
gauge that is only bound when the loop is alive cannot report that it isn't.

It does NOT know what a fire turn IS. `fire` is a collaborator taking one
conversation's due rows and answering an outcome string; `nora.app.fire.FirePath`
supplies the real one. That seam is why every rule above is testable with a fake
clock and a stub `fire`, with no model, no adapter and no network anywhere near
it — the tick's rules are exactly the ones a model call would otherwise hide.

WHY POLICY IS RE-CHECKED HERE
-----------------------------
`policy.toml` is read ONCE at startup, and a reminder may have been set thirty
days ago. Between those two moments trust can be withdrawn — someone leaves the
group, a room is delisted — and the row still says "speak into conv-1". The
create-time check answered a question about a world that no longer exists, so
the fire-time check is the one that counts. A revoked row is FAILED, never
retried, and counted `failed_policy`: R-5 keeps the failure out of the room, so
the counter is the only trace it leaves.

WHY THE SLOT IS `try_acquire`, NOT `acquire`
--------------------------------------------
A tag in flight owns the conversation. The tick neither queues behind it (a
reminder answered forty minutes late, into a conversation that has moved on, is
worse than one answered a tick late) nor sheds the row: it simply leaves it
PENDING and finds it again next tick. `due()` is a query, not a claim, so
"deferred" costs nothing and needs no state of its own.

WHY THE LIVENESS GAUGE IS NOT WRITTEN IN `tick()`
-------------------------------------------------
`nora_heartbeat_last_tick_age_seconds` is bound through
`Metrics.gauge_function`, so it is computed when Prometheus scrapes it. Written
from inside the tick it would freeze at its last value the moment the loop
stopped — reporting perfect health for precisely the failure it exists to catch.
Only `_last_tick_at` is written here, and only by a tick that COMPLETED.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from nora.app.proactive_screen import PROACTIVE_KIND, ProactiveScreen
from nora.domain.reminders import (
    Reminder,
    ReminderState,
    ReminderStore,
    open_proactive_subject,
)
from nora.engagement.slots import ConversationSlots, Key
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.heartbeat")

# A TURN's key: one conversation AND one requester AND one kind. Wider than
# `Key`, the conversation slot's key, and the difference is deliberate — see
# `_by_turn`. The kind component (DEC-139) keeps a mixed batch from being
# rendered through the wrong factory.
TurnKey = tuple[str, str, str, str]

# The ONE outcome that means the group heard something. Everything else the fire
# path can answer (`failed_policy`, `failed_timeout`, `failed_error`) is a
# reminder that did NOT arrive, and the row must say so.
SENT = "sent"

# The ONE outcome that means the turn ran, said nothing, and that was RIGHT: a
# `kind="proactive"` act turn reading its own earlier intention against the room
# now and declining it. DONE, not FAILED — the decision log reads every row of
# Nora's own, and a restraint recorded as a failure teaches the next decider
# that restraint fails. The same string `nora.app.fire.SILENT` answers with; a
# spelling drift here would turn every restraint back into a failure.
SILENT = "silent"

# What the liveness gauge reads when there is no loop to be live. Negative
# because no real tick age can be: `0.0` is what an unbound gauge exports and
# also what a gauge read one instant after a tick exports, so an operator (or an
# alert expression) cannot tell "just ticked" from "never started" — which is
# the single reading this series exists to give. See `publish_idle_gauge`.
HEARTBEAT_NOT_RUNNING = -1.0

# One tick's batch ceiling. Bounds a single tick's work when a backlog builds
# (a long outage, a clock jump); the remainder is still due next tick, in the
# same `due_at, id` order, because `due()` never mutates a row. Not config: an
# operator has no way to choose it usefully, and the number that matters —
# how often we wake — is `[heartbeat].tick_seconds`.
DUE_BATCH_LIMIT = 100


class _Allowlist(Protocol):
    """The ONE method the tick needs from `policy.toml`'s `Policy`.

    A narrow local shape, like `nora.app.runtime._PrunableStore`: this module
    has no business with the rest of `Policy`, and naming only `is_allowed`
    makes the wiring mistake loud rather than plausible. There are two objects
    called "policy" in the composition root — the ENGAGE policy (`CompositePolicy`,
    which decides whether a message is a tag) and the ALLOWLIST (`policy.toml`,
    which decides who may talk to her and where). Only the second has
    `is_allowed`, and passing the first here would defeat the revocation check
    rather than break it.
    """

    def is_allowed(
        self, *, platform: str, conversation_id: str, person_id: str | None,
    ) -> bool: ...


class HeartbeatLoop:
    """One tick, one turn per conversation.

    Every collaborator arrives through `__init__`, the clock included — that is
    what makes `tick()` a pure function of its inputs in tests, with no sleeping
    and no wall clock. `run()` is the only part that touches real time.
    """

    def __init__(
        self,
        *,
        store: ReminderStore,
        slots: ConversationSlots,
        policy: _Allowlist,
        clock: Callable[[], datetime],
        metrics: Metrics,
        tick_seconds: float,
        fire: Callable[[Sequence[Reminder]], Awaitable[str]],
        proactive: ProactiveScreen | None = None,
    ) -> None:
        self._store = store
        self._slots = slots
        self._policy = policy
        self._clock = clock
        self._metrics = metrics
        self._tick_seconds = tick_seconds
        self._fire = fire
        # The `kind="proactive"` screen: the room's `proactive` flag instead
        # of persons membership, and the note the row is about — retired, or
        # revised on the tag path since the row was linked, cancels it. `None`
        # (no proactive adaptor composed) FAILS every proactive row
        # `failed_policy` rather than firing it unscreened.
        self._proactive = proactive
        # Seeded at construction, not left None: before the first tick the
        # honest reading is "this long since we started", which climbs like any
        # other stall. A gauge that published 0 until the first tick would
        # report perfect health for a loop that never started.
        self._last_tick_at = clock()
        metrics.gauge_function(
            "nora_heartbeat_last_tick_age_seconds", self._last_tick_age_seconds,
        )

    def _last_tick_age_seconds(self) -> float:
        """Evaluated by prometheus-client at scrape time. Deliberately reads the
        clock ITSELF rather than being handed a value — that is the whole
        difference between a liveness signal and a stale number."""
        return (self._clock() - self._last_tick_at).total_seconds()

    async def tick(self) -> None:
        """One pass: due rows, grouped by conversation AND requester, one
        turn each.

        Raises nothing a caller must handle for a single group's sake — a group
        whose turn explodes ANYWHERE (screening, claiming, firing, resolving) is
        resolved as far as it can be, counted, and stepped over, because one bad
        reminder must not strand the others due in the same tick.
        """
        now = self._clock()
        due = await self._store.due(now=now, limit=DUE_BATCH_LIMIT)
        for turn_key, rows in _by_turn(due).items():
            # The SLOT is per conversation, not per turn: two requesters with
            # rows due in one group are two turns contending for one lock, and
            # the second simply finds it free after the first releases.
            slot: Key = (turn_key[0], turn_key[1])
            try:
                await self._one_turn(rows, slot=slot, now=now)
            except Exception:
                # Anything this group's screening, claiming or resolving
                # raises stops HERE — every conversation ordered after it in the
                # same batch still gets its tick, and the batch's tail is not
                # silently skipped until the fault clears.
                log.exception(
                    "heartbeat: %d reminder(s) for %s in %s failed; "
                    "the rest of the tick continues",
                    len(rows), rows[0].requester_person_id, turn_key[1],
                )
                self._metrics.inc("nora_errors_total", stage="heartbeat")
        # Only a tick that got here counts as a tick. A pass that raised leaves
        # this stamp alone, so the liveness gauge climbs through a failing loop
        # exactly as it does through a stopped one.
        self._last_tick_at = self._clock()

    async def run(self) -> None:
        """The `while True` `BackgroundTasks.spawn` drives.

        `prune_loop`'s never-die discipline (`nora.app.runtime`): a failing pass
        is logged and counted, never fatal to the loop and never fatal to the
        process. Sleep FIRST — startup is the least useful moment to speak, and
        the boot sweep (`recover()`, called by the composition root) has only
        just resolved whatever the last crash stranded.
        """
        log.info("heartbeat started: tick every %.1fs", self._tick_seconds)
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                await self.tick()
            except Exception:
                log.exception("heartbeat tick failed")
                self._metrics.inc("nora_errors_total", stage="heartbeat")

    async def _one_turn(
        self, rows: Sequence[Reminder], *, slot: Key, now: datetime,
    ) -> None:
        """One group: screened, then given the conversation's slot, or deferred.

        Split out of `tick()` so the per-group `try/except` there wraps ALL of a
        group's work — the policy screen and the terminal writes included, not
        just the fire call: a store that failed while claiming would otherwise
        leave rows FIRING with no counter and take the whole tick down.
        """
        allowed = await self._screen(rows, now=now)
        if not allowed:
            return
        if not self._slots.try_acquire(slot):
            # A tag owns this conversation. The rows stay PENDING and the next
            # tick finds them again — never queued, never shed.
            #
            # COUNTED, not merely logged: a deferral is the one way a reminder
            # goes hours late with every other signal reading healthy. Nothing
            # fired, so `nora_reminders_fired_total` does not move; the tick
            # itself completed, so the liveness gauge returns to zero. A group
            # under continuous tag load could starve its own reminders
            # indefinitely. The series counts TURNS per tick rather than
            # distinct reminders.
            log.info("heartbeat deferred %d reminder(s) in %s: slot held",
                     len(allowed), slot[1])
            self._metrics.inc("nora_heartbeat_deferrals_total")
            return
        try:
            await self._run_turn(allowed, now=now)
        finally:
            # ONE call, in a `finally`, for the same reason the tag path's
            # release is: a fire turn that dies must not strand whatever
            # queued behind it, and a queued tag must not have to wait for
            # the NEXT tag to be dispatched.
            self._slots.release_and_dispatch(slot)

    async def _screen(
        self, rows: Sequence[Reminder], *, now: datetime,
    ) -> list[Reminder]:
        """The fire-time policy check, per ROW.

        The grant is (room, person), and two people may have reminders due in
        the same group with only one of them having since lost the room — which
        is why the whole tick can never screen a CONVERSATION as a unit. The
        group is already one requester's, so every row here gets the same
        answer; the loop stays per row because that is what the predicate is
        about, and because it keeps the revoked person's row failing without
        touching the other person's group.
        """
        allowed: list[Reminder] = []
        for row in rows:
            if row.kind == PROACTIVE_KIND:
                # A DIFFERENT grant: the owner is Nora's own ai_peer id, which
                # `is_allowed` can never admit, so the screen is the room's flag
                # plus the note's state. Fail-closed with no screen wired: an
                # unscreened proactive row must not fire on the strength of
                # nothing.
                if self._proactive is None:
                    log.error(
                        "proactive reminder %s not fired: no ProactiveScreen is "
                        "wired for this heartbeat", row.id,
                    )
                    await self._resolve_all(
                        [row], ReminderState.FAILED, outcome="failed_policy",
                        now=now,
                    )
                    continue
                refusal = await self._proactive.refusal(row)
                if refusal is None:
                    allowed.append(row)
                    continue
                state, outcome = refusal
                log.info(
                    "proactive reminder %s not fired: %s", row.id, outcome,
                )
                await self._resolve_all([row], state, outcome=outcome, now=now)
                continue
            if self._policy.is_allowed(
                platform=row.platform,
                conversation_id=row.conversation_id,
                person_id=row.requester_person_id,
            ):
                allowed.append(row)
                continue
            log.warning(
                "reminder %s not fired: %s is no longer allowed in %s",
                row.id, row.requester_person_id, row.conversation_id,
            )
            await self._resolve_all(
                [row], ReminderState.FAILED, outcome="failed_policy", now=now,
            )
        return allowed

    async def _run_turn(self, rows: Sequence[Reminder], *, now: datetime) -> None:
        """One conversation's fire turn, from FIRING to a terminal state.

        The rows are CLAIMED — `PENDING -> FIRING`, compare-and-set — before
        `fire` is called, which makes delivery at-most-once: a crash mid-turn
        leaves them FIRING, and boot recovery resolves those to FAILED rather
        than re-queueing them. Nothing links a row to a delivered message, so a
        retry would double-send anything that died after egress.

        `claim`, not `mark`: `due()`'s batch is a snapshot, and this coroutine
        awaits a whole turn for the groups ahead of it, so a row here may since
        have been cancelled. `mark` matches on id alone and would resurrect it.

        A recurring row's SUCCESSOR is written by the same `claim` (DEC-85): the
        tick owns *when* a reminder fires and *what the row then says*, never
        the calendar. Writing the successor after the turn instead would let a
        crash mid-fire — the one case `recover()` exists for — end the series
        silently. `now` is the tick's instant, threaded through for the same
        reason it is threaded to `due()`: the catch-up rule is a comparison
        against it, and a store reading its own clock would put that beyond
        this module's tests.
        """
        firing: list[Reminder] = []
        try:
            for row in rows:
                if await self._store.claim(row.id, now=now):
                    # `replace`, so what `fire` receives says what is true of the
                    # row it is about to speak for.
                    firing.append(replace(row, state=ReminderState.FIRING))
                else:
                    # Cancelled (or otherwise resolved) since `due()` read it.
                    # Not ours to fire, and not ours to write a terminal state
                    # over either — whoever moved it already recorded one.
                    log.info("reminder %s was resolved before it could be "
                             "claimed; skipping", row.id)
        except Exception:
            # Whatever this raised, the rows already moved to FIRING are ours
            # and nothing else will close them out before the next boot. Resolve
            # them here, then let `tick()`'s per-group guard count the failure.
            await self._resolve_all(
                firing, ReminderState.FAILED, outcome="failed_error", now=now,
            )
            raise
        if not firing:
            return
        # The screen ran before `claim`; those rows are OWNED now and a claimed
        # due row is not proof the act is still needed — the room's flag or the
        # note it named may have moved while this coroutine awaited its turn.
        # Rechecking here is the fire-time boundary a screen seconds ago cannot
        # be. A screen that cannot check is `failed_error`, never a send on an
        # unverified premise.
        if self._proactive is not None:
            still: list[Reminder] = []
            for row in firing:
                if row.kind != PROACTIVE_KIND:
                    still.append(row)
                    continue
                try:
                    refusal = await self._proactive.refusal(row)
                except Exception:
                    log.exception(
                        "proactive screen recheck failed for %s", row.id,
                    )
                    refusal = (ReminderState.FAILED, "failed_error")
                if refusal is None:
                    refusal = await self._superseded(row)
                if refusal is None:
                    still.append(row)
                    continue
                state, outcome = refusal
                log.info(
                    "proactive reminder %s resolved after claim: %s", row.id, outcome,
                )
                await self._resolve_all([row], state, outcome=outcome, now=now)
            firing = still
        if not firing:
            return
        try:
            outcome = await self._fire(firing)
        except Exception:
            log.exception("fire turn failed in %s", firing[0].conversation_id)
            await self._resolve_all(
                firing, ReminderState.FAILED, outcome="failed_error", now=now,
            )
            return
        # The STATE comes from the OUTCOME. Only `sent` is DONE — a turn that
        # timed out or reported its own error answers without raising, and
        # writing those `done` records a delivery that never happened.
        await self._resolve_all(firing, _state_for(outcome), outcome=outcome, now=now)

    async def _superseded(
        self, row: Reminder,
    ) -> tuple[ReminderState, str] | None:
        """The delivered-fact recheck the screen cannot make: a claimed act
        whose (subject, action, destination) already shows a DELIVERED row
        does not fire — the code-level duplicate refusal the check-seconds-ago
        screen has no eyes for. `None` when nothing delivered it yet; a read
        that fails refuses — firing on an unchecked premise is the failure
        mode this boundary exists for."""
        subject = open_proactive_subject(row)
        if subject is None:
            return None
        try:
            delivered = [
                r for r in await self._store.recent_in(
                    [(row.platform, row.conversation_id)],
                    requester_person_id=row.requester_person_id,
                    since=row.created_at, limit=50,
                )
                if r.id != row.id
                and open_proactive_subject(r) == subject
                and r.outcome == "sent"
            ]
        except Exception:
            log.exception(
                "delivered-act recheck failed for %s", row.id,
            )
            return ReminderState.FAILED, "failed_error"
        if delivered:
            return ReminderState.FAILED, "failed_superseded"
        return None

    async def _resolve_all(
        self, rows: Sequence[Reminder], state: ReminderState, *,
        outcome: str, now: datetime,
    ) -> None:
        """`_resolve` over a turn's rows, where one failure costs ONE row.

        Per row rather than per loop: a store that failed writing the first
        row's terminal state would otherwise skip every row after it, leaving
        them FIRING — recoverable only at the next boot. The row whose write
        failed is still FIRING here too, but it is the only one, and
        `recover()` sweeps it.
        """
        for row in rows:
            try:
                await self._resolve(row, state, outcome=outcome, now=now)
            except Exception:
                log.exception(
                    "reminder %s could not be resolved to %s; it stays FIRING "
                    "and boot recovery will sweep it (§8.2)", row.id, state,
                )
                self._metrics.inc("nora_errors_total", stage="heartbeat")

    async def _resolve(
        self, row: Reminder, state: ReminderState, *, outcome: str, now: datetime,
    ) -> None:
        """Terminal state, `fired_at`, counter — the three together, once.

        `fired_at` is stamped even on a row that never spoke: it records when
        the tick HANDLED the reminder, and a NULL there loses the one fact an
        operator asking "when did she stop being allowed in that group?" needs.
        """
        await self._store.mark(row.id, state, fired_at=now, outcome=outcome)
        self._metrics.inc("nora_reminders_fired_total", outcome=outcome)


def _state_for(outcome: str) -> ReminderState:
    """The fire outcome, as `done|failed`.

    The outcome decides the state, not just an exception: `failed_timeout`,
    `failed_error` and the `not_wired` stub are FAILED. Writing them `done`
    would read as "delivered" to every query that filters on state, with the
    truth surviving only in a free-text column nothing joins on.

    `silent` is DONE too. Only a proactive act turn can answer it
    (`nora.app.fire`), and for that turn saying nothing is the healthy default,
    not a delivery that never happened. Mapped to FAILED it would read as one in
    every query, in the metric, and in the decision log the next decider learns
    from.
    """
    return ReminderState.DONE if outcome in (SENT, SILENT) else ReminderState.FAILED


def _by_turn(rows: Sequence[Reminder]) -> dict[TurnKey, list[Reminder]]:
    """Group into ONE turn per (conversation, requester, kind), preserving
    `due()`'s order.

    One CONVERSATION, one REQUESTER and one KIND (DEC-139):

    - One CONVERSATION, because a turn has one Room, one policy decision and one
      Deliverer binding. Rows for two conversations sharing a turn would leak
      one room's reminder into the other. `(platform, conversation_id)` because
      a conversation id alone is not unique across platforms.
    - One REQUESTER, because the entire turn derives from a single
      `requester_person_id` — the `tagger`, the `Room` scope that tagger grants,
      the peer card, and the synthetic message's `sender_name`. Two people's
      rows in one turn would put person B's sentence into a turn wearing person
      A's identity, A's memory and A's room.
    - One KIND, because `FirePath.run` renders the batch through the factory
      `rows[0].kind` selects — a mixed batch (one plain reminder + one
      proactive row, same person, same conversation, same tick) would have
      its kind decided by whichever row sorts first and the other row's payload
      rendered through the wrong factory. Same conversation, same tick, two
      kinds: two turns, no cross-kind mixing.

    Two requesters due in the same group are therefore two turns — as are two
    kinds for the same requester. They are safe precisely because they are
    separate: each takes the conversation slot in turn, so they are serialised
    exactly as a tag and a fire turn are. The deliberate accounting
    consequence of splitting kinds is that turns and per-turn deferrals double
    while `nora_reminders_fired_total`, which is per-row, does not move.
    """
    groups: dict[TurnKey, list[Reminder]] = {}
    for row in rows:
        key = (
            row.platform, row.conversation_id, row.requester_person_id, row.kind,
        )
        groups.setdefault(key, []).append(row)
    return groups


def publish_idle_gauge(metrics: Metrics) -> None:
    """Bind `nora_heartbeat_last_tick_age_seconds` to the "not running" sentinel.

    Called unconditionally at startup, because the gauge's whole purpose is to
    be readable when the loop is NOT. Bound only inside `HeartbeatLoop.__init__`
    it would be absent from every deployment running with
    `[heartbeat] enabled = false`, and an unbound prometheus gauge exports
    `0.0` — the same number a healthy loop exports a moment after it ticks.

    A climbing age would be the other option and is worse: a deliberately
    disabled heartbeat would page someone every time the threshold passed. The
    sentinel says "off", not "stalled", and no real tick age can be negative.

    `HeartbeatLoop.__init__` rebinds the same series when the loop IS built
    (`Gauge.set_function` replaces its callback), so the order of the two calls
    does not matter.
    """
    metrics.gauge_function(
        "nora_heartbeat_last_tick_age_seconds", lambda: HEARTBEAT_NOT_RUNNING,
    )


async def recover(*, store: ReminderStore, metrics: Metrics, now: datetime) -> int:
    """Boot recovery: rows a crash left FIRING resolve to FAILED, counted.

    `now` is the moment the sweep ran, stamped onto every row it resolves. The
    clock arrives as a VALUE rather than being read here for the reason it
    arrives as a callable on `HeartbeatLoop`: this module reads no wall clock of
    its own, so recovery is assertable at a fixed instant.

    A module-level function, not a method on `HeartbeatLoop`, and called
    UNCONDITIONALLY — a heartbeat that has been switched off is exactly the
    state in which a stranded FIRING row would otherwise sit forever, so
    at-most-once must not stop applying on the config that looks safest. Only
    the LOOP is gated by `[heartbeat] enabled`.

    The counter matters: a reminder that vanishes must never vanish from the
    metrics too (`nora_reminders_fired_total`), and the sweep is the ONE path
    that resolves reminders with no tick running; without it the rows a crash
    ate would appear on no dashboard and in no alert.
    """
    swept = await store.sweep_firing(now=now)
    if not swept:
        return 0
    log.warning(
        "boot recovery: %d reminder(s) were left FIRING by a previous run and "
        "are now FAILED — at-most-once, never re-fired (§8.2)", swept,
    )
    # One increment per REMINDER, matching every other site on this series: the
    # metric counts reminders resolved, so a sweep of three must move it by
    # three or the totals stop reconciling.
    metrics.inc("nora_reminders_fired_total", swept, outcome="failed_error")
    return swept
