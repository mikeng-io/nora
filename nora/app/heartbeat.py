"""The tick: wake, find what is due, and give each conversation one turn.

WHAT THIS MODULE IS RESPONSIBLE FOR
-----------------------------------
Four things, and deliberately not a fifth:

1. **When** — a fixed tick, and the batch of rows `due_at <= now`.
2. **Whether** — `policy.toml` re-checked at FIRE time, per row.
3. **Where the turn may run** — the conversation's serialisation slot, taken
   with `try_acquire`: never queued, never waited on.
4. **What happened** — the row's terminal state, and one counter per reminder.

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

from nora.domain.reminders import Reminder, ReminderState, ReminderStore
from nora.engagement.slots import ConversationSlots, Key
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.heartbeat")

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
    ) -> None:
        self._store = store
        self._slots = slots
        self._policy = policy
        self._clock = clock
        self._metrics = metrics
        self._tick_seconds = tick_seconds
        self._fire = fire
        # Seeded at construction, not left None: before the first tick the
        # honest reading is "this long since we started", which climbs like any
        # other stall. A None-guarded gauge that publishes 0 until the first
        # tick would report perfect health for a loop that never started.
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
        """One pass: due rows, grouped by conversation, one turn each.

        Raises nothing a caller must handle for a single conversation's sake —
        a conversation whose fire turn explodes is resolved, counted and stepped
        over, because one bad reminder must not strand the others due in the
        same tick.
        """
        now = self._clock()
        due = await self._store.due(now=now, limit=DUE_BATCH_LIMIT)
        for key, rows in _by_conversation(due).items():
            allowed = await self._screen(rows, now=now)
            if not allowed:
                continue
            if not self._slots.try_acquire(key):
                # A tag owns this conversation. The rows stay PENDING and the
                # next tick finds them again — never queued, never shed.
                log.info("heartbeat deferred %d reminder(s) in %s: slot held",
                         len(allowed), key[1])
                continue
            try:
                await self._run_turn(allowed, now=now)
            finally:
                # ONE call, in a `finally`, for the same reason the tag path's
                # release is: a fire turn that dies must not strand whatever
                # queued behind it, and a queued tag must not have to wait for
                # the NEXT tag to be dispatched.
                self._slots.release_and_dispatch(key)
        # Only a tick that got here counts as a tick. A pass that raised leaves
        # this stamp alone, so the liveness gauge climbs through a failing loop
        # exactly as it does through a stopped one.
        self._last_tick_at = self._clock()

    async def run(self) -> None:
        """The `while True` `BackgroundTasks.spawn` drives.

        `prune_loop`'s never-die discipline (`nora.app.runtime`): a failing pass
        is logged and counted, never fatal to the loop and never fatal to the
        process. Sleep FIRST — startup is the least useful moment to speak, and
        the boot sweep (`store.sweep_firing()`, in the composition root) has
        only just resolved whatever the last crash stranded.
        """
        log.info("heartbeat started: tick every %.1fs", self._tick_seconds)
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                await self.tick()
            except Exception:
                log.exception("heartbeat tick failed")
                self._metrics.inc("nora_errors_total", stage="heartbeat")

    async def _screen(
        self, rows: Sequence[Reminder], *, now: datetime,
    ) -> list[Reminder]:
        """§5.1's fire-time policy check, per ROW.

        Per row rather than per conversation because the grant is
        (room, person): two people may have set reminders in the same group and
        only one of them may since have lost the room. Screening the group as a
        whole would either fire the revoked one or silence the other.
        """
        allowed: list[Reminder] = []
        for row in rows:
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
            await self._resolve(row, ReminderState.FAILED, outcome="failed_policy", now=now)
        return allowed

    async def _run_turn(self, rows: Sequence[Reminder], *, now: datetime) -> None:
        """One conversation's fire turn, from FIRING to a terminal state.

        The rows are marked FIRING BEFORE `fire` is called, which is what makes
        §8.2's at-most-once real: a crash mid-turn leaves them FIRING, and boot
        recovery resolves those to FAILED rather than re-queueing them. Nothing
        links a row to a delivered message, so a retry would double-send
        anything that died after egress.
        """
        firing: list[Reminder] = []
        for row in rows:
            if await self._store.mark(row.id, ReminderState.FIRING):
                # `replace`, so what `fire` receives says what is true of the
                # row it is about to speak for.
                firing.append(replace(row, state=ReminderState.FIRING))
        if not firing:
            return
        try:
            outcome = await self._fire(firing)
        except Exception:
            log.exception("fire turn failed in %s", firing[0].conversation_id)
            for row in firing:
                await self._resolve(row, ReminderState.FAILED, outcome="failed_error", now=now)
            return
        for row in firing:
            await self._resolve(row, ReminderState.DONE, outcome=outcome, now=now)

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


def _by_conversation(rows: Sequence[Reminder]) -> dict[Key, list[Reminder]]:
    """Group into ONE turn per conversation, preserving `due()`'s order.

    §5.2: a turn has one conversation scope — one Room, one policy decision, one
    Deliverer binding. Two conversations due in the same tick are two turns;
    sharing one would leak one room's reminder into the other. The key is
    `(platform, conversation_id)`, the same tuple `ConversationSlots` locks on,
    because a conversation id alone is not unique across platforms.
    """
    groups: dict[Key, list[Reminder]] = {}
    for row in rows:
        groups.setdefault((row.platform, row.conversation_id), []).append(row)
    return groups
