"""`ParticipationTrigger` — the quiescence-window trigger for the participation
occasion.

WHAT THIS MODULE IS
-------------------
The ONLY place an untagged message becomes a candidate for the agent. The
dispatcher feeds it messages that `RelevanceGatePolicy` marked
`relevance_gate=True` (an untagged message in an opted-in room); everything
else in the room's traffic never reaches it. It answers one question: has
this room gone quiet for `settle_seconds` since its last message, past the
last window already evaluated? When so, it runs exactly ONE participation
turn for the whole closed window, through `FirePath.speak`, with the full
window in context (the fire path assembles the transcript itself).

THE INVARIANTS THIS ENFORCES
----------------------------
* **try_acquire-and-skip.** The turn takes the conversation's
  `ConversationSlots` slot only if free; a tag holding it means the window is
  marked closed and skipped, silently — a tag never waits behind an ambient
  thought, and evaluating a stale burst after the tag would answer a
  conversation that has already moved on.
* **Send cooldown, NOT an evaluation cooldown.** Every closed window
  is evaluated, cooldown or not; the cooldown only decides
  `send_permitted` on the `speak` call — a "speak" conclusion inside the
  cooldown has its message held by the `Deliverer` at the last inch, logged
  for shadow review, and the turn still answers `SILENT`.
* **Per-room cursor state, never per-message.** Five dicts keyed
  by `(platform, conversation_id)`: the last message seen, the unjudged count,
  the last window end evaluated, the last send, and the conversation type.
  This is the same treatment the tag path's `Debounce` gets — bounded by
  `sweep` from the runtime loop, and retained state is per-ROOM cursors, not
  per-message session state.

WHAT SURVIVES A RESTART, AND WHAT DOES NOT
------------------------------------------
`nora` is force-recreated routinely, so a cursor held only in process memory
is lost: the send cooldown would reset (she could speak again immediately
after a restart however recently she had spoken) and a judged window could be
judged twice.

TWO cursors are STORED, and both are cold path — `_last_window_end` is written
once per window close and `_last_send_at` once per send.

TWO are RE-DERIVED at hydration from `messages`, which already holds them:
`_last_message_at` (the newest inbound row's `ts`) and `_conversation_type`
(that same row's column). They move on `note_message`, which is synchronous on
the dispatcher's path and must not await, so they could not be stored even if
that were the simpler answer.

ONE is NEITHER, and that is a decision: **`_unjudged` starts at zero
after a restart.** A `messages`-derived count does NOT reproduce it. A tagged
message, a voice note and a voice-wake utterance are all rows in that table
that never reach `note_message` (the engage policy short-circuits before the
`relevance_gate` verdict), and Nora's own replies are rows too. A count that
included them would close windows EARLIER than the trigger does live — more
evaluations, more model calls, possibly more speech — which is a change to
what this class DECIDES wearing a persistence change's clothes. Storing it
instead would put a database write on `note_message`, which its own docstring
forbids. So the volume arm resumes from zero, and the quiet arm resumes from
the record.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from nora.domain.models import NormalizedMessage
from nora.domain.participation import ParticipationCursor
from nora.engagement.slots import ConversationSlots
from nora.ingest.relevance_gate import RelevanceGate

from .fire import SENT, participation_occasion

log = logging.getLogger("nora.app.participation")

Key = tuple[str, str]


class ParticipationStateStore(Protocol):
    """The three verbs this module needs from
    `nora.infra.sql.participation_state.SqlParticipationStateStore` — a narrow
    local shape, like `nora.app.runtime`'s Protocols, so this class cannot
    reach for anything else the concrete store grows later."""

    async def read_cursors(self) -> list[ParticipationCursor]: ...

    async def record_window_end(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None: ...

    async def record_send(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None: ...


class ParticipationTrigger:
    """One participation evaluation per quiescence-closed window, per room.

    `speak` is `FirePath.speak` itself (a bound method, like
    `HeartbeatLoop._fire` — the composition test asserts it), called as
    `speak(occasion, send_permitted=...)`; it is typed as the narrow callable
    this class uses, not the whole `FirePath`, for the same reason
    `nora.app.runtime`'s Protocols are narrow.

    `clock` is injected for the reason `Debounce`'s is: the settle window and
    the cooldown are durations, and a module that read the clock itself could
    not be tested without sleeping.

    **The clock is `time.time`, not `time.monotonic`.** Every comparison here
    is a DIFFERENCE of two readings of the same clock, but a monotonic epoch is
    arbitrary per process, so the two stored cursors would be numbers that mean
    nothing to the process reading them back. Absence is `None`, never an
    instant: `monotonic()` on Linux is uptime, so a `0.0` default for "never
    sent" would refuse the first send after a container start for
    `cooldown_seconds`.

    `store` is OPTIONAL and `None` is the dormant default — every offline and
    eval wiring builds this class without one: cursors then live in memory and
    a restart empties them. A missing store is never an error.
    """

    def __init__(
        self,
        *,
        speak: Callable[..., Awaitable[str]],
        slots: ConversationSlots,
        settle_seconds: float,
        cooldown_seconds: float,
        max_window_messages: int = 10,
        gate: RelevanceGate | None = None,
        gate_shadow: Callable[..., Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.time,
        store: ParticipationStateStore | None = None,
    ) -> None:
        self._speak = speak
        self._slots = slots
        # The Relevance Gate. `None` is the shape for the tests that drive the
        # turn directly: with no gate every closed window runs a turn, which is
        # exactly what the gate exists to stop paying for. Production always
        # wires one.
        self._gate = gate
        # `FirePath.post_gate_verdict` when `[relevance_gate] shadow = true`.
        # Its presence IS shadow mode: set, a GO is REVIEWED and no turn runs;
        # unset, a GO runs the turn. One switch, so there is no state in which
        # the room hears something the reviewer did not.
        self._gate_shadow = gate_shadow
        self._settle_seconds = settle_seconds
        self._cooldown_seconds = cooldown_seconds
        # The VOLUME arm of the batch (see `close_due_windows`). A cap at the
        # gate's 40-row transcript window would fill about once a day in a busy
        # room, i.e. would not exist; ten is dense enough to fire inside a real
        # conversation.
        self._max_window_messages = max_window_messages
        self._clock = clock
        self._store = store
        # Hydration is ONCE per process and happens on the first
        # `close_due_windows`, not in `__init__`: this constructor is
        # synchronous and the read is not, and the runtime loop's own
        # try/except is already the right place for a read that can fail —
        # a failed hydration leaves this False, so the next tick retries
        # rather than evaluating against a cursor set it knows is incomplete.
        self._hydrated = False
        self._last_message_at: dict[Key, float] = {}
        # Messages seen since this room's last evaluation — the volume arm's
        # counter. Reset when a window closes, for the same reason
        # `_last_window_end` exists: a window that has been judged is spent.
        self._unjudged: dict[Key, int] = {}
        self._last_window_end: dict[Key, float] = {}
        self._last_send_at: dict[Key, float] = {}
        # The occasion factory needs the room's conversation_type; remembered
        # from the candidate message rather than re-derived from policy, which
        # this class deliberately does not hold.
        self._conversation_type: dict[Key, str] = {}

    def note_message(self, message: NormalizedMessage) -> None:
        """Record one participation-eligible message. Cheap and synchronous:
        called from the dispatcher, which must not await anything here — the
        evaluation happens on `close_due_windows`, never per message."""
        key = (message.platform, message.conversation_id)
        self._last_message_at[key] = self._clock()
        self._unjudged[key] = self._unjudged.get(key, 0) + 1
        self._conversation_type[key] = message.conversation_type

    async def close_due_windows(self) -> int:
        """Run the ONE evaluation for every window that has closed since the
        last call; returns how many were evaluated. The runtime loop calls
        this on its tick; tests call it directly with an injected clock.

        A window closes on EITHER arm, whichever comes first — the shape a
        message queue uses (`WaitTimeSeconds` + `MaxNumberOfMessages`):

        * **quiet** — the room has said nothing for `settle_seconds`;
        * **volume** — `max_window_messages` have arrived since this room was
          last judged.

        The volume arm is not an optimisation, it closes a hole. The quiet arm
        alone reads `now - last_message_at`, and that resets on EVERY message,
        so a room whose gaps stay under `settle_seconds` never closes a window
        at all. A room whose messages arrive six seconds apart for twenty minutes
        would produce ZERO evaluations, the first only after the room fell
        silent, by which time a question would have scrolled away. The gate
        would be blind exactly when a room is busiest.

        The watermark (`_last_window_end`) is set BEFORE the evaluation, not
        after it: exactly one evaluation per closed window, even if the turn
        faults — a failed evaluation is a missed thought, and re-opening the
        window would make one quiet burst cost a turn every tick until it
        succeeds. `_unjudged` is cleared with it, for the same reason: a
        window that has been judged is spent, however it closed.

        The FIRST call of a process hydrates the per-room cursors from the
        store (see the module docstring). It runs before `due` is computed, and
        it is the reason the watermark is worth storing at all: with
        `_last_message_at` restored from the record, a burst that was already
        judged before the restart reads as `last <= last_window_end` and is
        skipped — where an unhydrated watermark and an unhydrated message
        cursor simply cancel out and prove nothing.
        """
        await self._hydrate()
        now = self._clock()
        due = [
            key for key, last in self._last_message_at.items()
            # ABSENT, not zero (`nora.domain.participation`): a room that has
            # never had a window judged has unjudged traffic by definition, and
            # `0.0` would say the same thing only by being a 1970 instant that
            # every real message happens to beat.
            if self._is_unjudged(key, last)
            and (
                now - last >= self._settle_seconds                    # quiet
                or self._unjudged.get(key, 0) >= self._max_window_messages
            )
        ]
        for key in due:
            self._last_window_end[key] = now
            self._unjudged[key] = 0
            await self._record_window_end(key, now)
            platform, conversation_id = key
            # Take the slot only if free. A tag holding it skips the
            # window ENTIRELY (the watermark above already closed it) — never
            # queued, never waited on.
            if not self._slots.try_acquire(key):
                log.debug(
                    "participation window skipped: slot held (%s/%s)",
                    platform, conversation_id,
                )
                continue
            try:
                admitted, score, verdict = await self._admitted(platform, conversation_id)
                if not admitted:
                    # NO-GO, or the gate failed closed. Nothing runs, nothing
                    # is posted, nothing is counted here — the gate counted
                    # its own verdict, and a second count at the trigger would
                    # be the same fact in two series.
                    continue
                if self._gate_shadow is not None:
                    # SHADOW: the verdict is REVIEWED and Nora is never
                    # involved — no turn, no agent, no cooldown spent,
                    # whatever the score. The slot was still taken above, on
                    # purpose: shadow has to model live, and a shadow that
                    # skipped the slot check would show the operator a GO rate
                    # higher than the live path could ever produce.
                    await self._gate_shadow(
                        platform=platform, conversation_id=conversation_id,
                        score=score,
                    )
                    continue
                # ABSENT means «she has never spoken here», which permits the
                # send — not «she spoke in 1970», which permits it too and is
                # indistinguishable until the day the value comes from a store
                # rather than from a `.get` default.
                last_send = self._last_send_at.get(key)
                send_permitted = (
                    last_send is None
                    or now - last_send >= self._cooldown_seconds
                )
                outcome = await self._speak(
                    participation_occasion(
                        platform=platform, conversation_id=conversation_id,
                        conversation_type=self._conversation_type.get(key, "group"),
                        relevance_score=score,
                    ),
                    send_permitted=send_permitted,
                    gate_verdict=verdict,
                    gate_score=score,
                )
                # Only a DELIVERED message spends the cooldown: a held,
                # silent or failed turn left the room exactly as it was, and
                # the cadence caps what the room HEARS.
                if outcome == SENT:
                    self._last_send_at[key] = now
                    await self._record_send(key, now)
            finally:
                self._slots.release_and_dispatch(key)
        return len(due)

    def _is_unjudged(self, key: Key, last_message_at: float) -> bool:
        """Has anything arrived in this room since its last judged window?"""
        end = self._last_window_end.get(key)
        return end is None or last_message_at > end

    async def _hydrate(self) -> None:
        """Restore the per-room cursors from the store, once per process.

        `setdefault` on every dict, never assignment: the dispatcher can note a
        message before the first tick, and a live note is NEWER than anything
        the record can say. Restoring over it would move the room's message
        cursor BACKWARDS and re-open a window that is already accumulating.

        `_unjudged` is deliberately not seeded — see the module docstring.
        `_hydrated` is set only after the read returns, so a store that is
        briefly unreachable costs one tick and is retried on the next; it never
        leaves this class evaluating against half a cursor set, which is the
        state the whole table exists to make unreachable.
        """
        if self._hydrated or self._store is None:
            return
        for cursor in await self._store.read_cursors():
            key = (cursor.platform, cursor.conversation_id)
            self._last_window_end.setdefault(key, cursor.last_window_end_at / 1000.0)
            if cursor.last_send_at is not None:
                self._last_send_at.setdefault(key, cursor.last_send_at / 1000.0)
            if cursor.last_message_at is not None:
                self._last_message_at.setdefault(key, cursor.last_message_at / 1000.0)
            if cursor.conversation_type is not None:
                self._conversation_type.setdefault(key, cursor.conversation_type)
        self._hydrated = True

    async def _record_window_end(self, key: Key, at: float) -> None:
        """Mirror the watermark this tick just set. See `_record_send` for why
        a failure is logged rather than raised."""
        if self._store is None:
            return
        try:
            await self._store.record_window_end(
                platform=key[0], conversation_id=key[1], at_ms=int(at * 1000),
            )
        except Exception:
            log.exception(
                "participation window-end cursor not persisted (%s/%s)", *key,
            )

    async def _record_send(self, key: Key, at: float) -> None:
        """Mirror a DELIVERED message's cooldown, and never let the write
        change what this class does.

        The in-memory dict is already authoritative for THIS process — it was
        assigned on the line before — so the store is a mirror, and a mirror
        that raised here would turn a database blip into a swallowed turn or a
        skipped evaluation. What a failure costs is the NEXT restart's
        accuracy, so it is logged at ERROR: this class holds no metrics
        collaborator, and an operator reading "cursor not persisted" learns
        exactly which room to distrust.
        """
        if self._store is None:
            return
        try:
            await self._store.record_send(
                platform=key[0], conversation_id=key[1], at_ms=int(at * 1000),
            )
        except Exception:
            log.exception("participation send cursor not persisted (%s/%s)", *key)

    async def _admitted(
        self, platform: str, conversation_id: str,
    ) -> tuple[bool, float | None, str | None]:
        """`(admitted, score, verdict)`.

        Two values and not a nullable score, because "admitted with no score"
        is a real state and collapsing it into `0.0` would put
        «this room scored zero» in front of her — the opposite of what
        happened. The score is reference only, so a
        missing one must render as nothing, never as a low one.

        The `False` case covers NO-GO and every fail-closed reason together,
        which is the ONE place they are allowed to be the same: the trigger's
        job is "run a turn or not" and both answers are "not". They are kept
        apart where it matters, on the two counters the gate itself moves.

        `verdict` is the gate's verdict string ("go"/"no_go"), threaded to the
        fire path's trace so the turn announces which gate verdict admitted
        it. `None` when no gate is wired (pre-gate behaviour) or when the
        gate failed closed — "go" is the only value that reaches a turn.

        No gate wired admits unconditionally with no score — the pre-gate
        behaviour. A trigger that refused without a gate would make an
        optional component mandatory by silently turning the path off.
        """
        if self._gate is None:
            return True, None, None
        verdict = await self._gate.decide(
            platform=platform, conversation_id=conversation_id,
        )
        return (
            verdict.admits,
            (verdict.score if verdict.admits else None),
            (verdict.verdict if verdict.admits else None),
        )

    def sweep(self, older_than: float) -> int:
        """Drop rooms idle longer than `older_than` and return how many.

        All five dicts are otherwise unbounded — one entry per opted-in room
        ever seen. This is `Debounce.sweep`'s hook, for the same reason; the
        runtime loop calls it, this class never calls it itself.
        """
        now = self._clock()
        stale = [
            key for key, last in self._last_message_at.items()
            if now - last > older_than
        ]
        for key in stale:
            del self._last_message_at[key]
            self._last_window_end.pop(key, None)
            self._unjudged.pop(key, None)
            self._last_send_at.pop(key, None)
            self._conversation_type.pop(key, None)
        return len(stale)

    def __len__(self) -> int:
        """Rooms with any retained cursor — for bounded-state assertions."""
        return len(self._last_message_at)
