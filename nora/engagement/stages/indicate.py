"""Stage: indicate — the progress reaction Nora puts on the message that
tagged her, and the resolution she replaces it with when the turn ends.

Two public entry points, not one `run()`, for the same reason
`nora.engagement.stages.deliver` has two: the pair is needed at two different
points of a tag. `begin()` runs the moment admission accepts the tag;
`resolve()` runs from the pipeline's `finally`, after the turn has ended one
way or another.

Three states, and a person who tagged her can tell them apart at a glance:

    👀  I have this and I am working on it     (begin)
    ✅  done — the reply is the one below      (resolve, delivered)
    ⚠️  done — nothing is coming               (resolve, not delivered)


WHY ⚠️ AND NOT ✅ ON FAILURE
---------------------------
A ✅ on a tag that produced no reply claims something was delivered when
nothing was — the group would sit waiting for a message that already isn't
coming. It is the *inverse* of R-5: not an error leaking out, but a success
leaking out that never happened, which is worse, because a silent failure is
at least honestly ambiguous and a false ✅ is confidently wrong.

The two obvious alternatives are both the wrong-silence bug, in user-facing
form:

- Leaving the 👀 in place forever makes "thinking" and "broken" the same
  observation.
- Removing the 👀 and adding nothing makes "gave up" and "never triggered"
  the same observation, so the tagger cannot even tell whether Nora heard
  them.

⚠️ is the honest resolution, and it does not violate R-5. R-5 forbids error
*text* reaching the group — a stack trace, an exception message, an "I broke"
apology, anything that discloses what failed or invites a debugging
conversation in a group chat. ⚠️ discloses none of that. It carries exactly
one bit, and that bit is *already public*: the reply is missing, and the group
can see it is missing. All the reaction does is remove the ambiguity about
whether more is still coming. It says nothing about WHY — a model timeout, a
guard veto, an audit write failure and a leak block are indistinguishable
under it, which is what DEC-4 requires of `INFRASTRUCTURE`/`GUARD_VETO`
failures. The operator reads the real reason in the log and in
`nora_silent_failures_total{reason=...}`; the group learns only that the
conversation is theirs again.

❌ is not used: it reads as an incident report and invites exactly the "what's
wrong with the bot?" thread R-5 exists to prevent. ⚠️ reads as an empty outcome
and nothing more.

WHY A SIGN AND NOT A PERSON
---------------------------
A person emoji carries skin tone, gender and human affect, and a shrug is
somebody being dismissive — a lot of tone to attach to "the model timed out".
⚠️ is a sign, not a face; nobody reads a caution triangle as sarcasm. It stays
unmistakable against 👀 and ✅ at a glance, which matters more than subtlety for
a marker whose only job is to be unambiguous.

Unlike the other two it is a VARIATION SEQUENCE — U+26A0 plus U+FE0F, two
codepoints forming one grapheme cluster, which is what signal-cli's `--emoji`
requires. Dropping the selector renders a monochrome glyph on some clients and
is a different reaction to Signal, so both halves are load-bearing.


WHY THE REACTION IS SPAWNED, NEVER AWAITED
------------------------------------------
Both `begin()` and `resolve()` schedule their work through the injected
`spawn` and return immediately — the same shape
`nora.ingest.stages.memory_feed` uses.

Awaiting the 👀 would put an HTTP round-trip to signal-cli in front of the
model call on every single tag, and `nora_overhead_seconds` (tag latency minus
the model call) is the metric that exists to catch precisely that. The tail is
the real argument, not the median: `SignalReactor` runs a 10s total timeout,
so an unreachable or wedged daemon would add up to ten seconds to EVERY tag
before Nora even started thinking. Spawning bounds the blast radius of a sick
daemon to the decoration itself.

Spawning introduces an ordering hazard, handled here: `begin()` and `resolve()`
can otherwise race, and a resolution that lands before its own 👀 leaves the 👀
as the final state. `Indicator.placed` is an `asyncio.Event` set in `_place`'s
`finally`, and `_resolve` waits on it before touching anything, so the two are
serialised with respect to each other without either being on the reply's
path.


WHY R-2, R-2b, R-3 AND R-7 ALL STILL HOLD
-----------------------------------------
A reaction is an outbound side effect, so this is stated precisely.

- **It is not a tool, and cannot become one by accident.** Nothing here is
  registered with `nora.agent.registry.ToolRegistry`, so `ReplyGuard`'s
  injected `budgets` mapping is unchanged and no reply budget is consumed.
  R-7's other half does the rest: a tool name absent from `budgets` is vetoed
  by default, so a model that hallucinates a `react` tool is refused before
  anything runs. The model has no way to emit a reaction at all — the pipeline
  drives this, not the agent.
- **R-2 / R-2b are untouched.** `Deliverer._attempted` and `_delivered` are
  written only by `Deliverer.deliver`, whose only caller is the `send_reply`
  tool. This module never constructs, reads or calls a `Deliverer`; it takes
  `delivered` as a plain `bool` the pipeline already computed. The attempt
  latch cannot be consumed, refreshed or re-armed from here, so a tag still
  produces at most one text reply.
- **R-3 is untouched, and unreachable from here.** R-3 vetoes a `send_reply`
  whose `conversation_id` argument is not the tagging conversation, and it
  works on *model-supplied* input. The reaction takes no model input
  whatsoever: its `ConversationTarget` is `message.target()` — the tagging
  message's own conversation, computed in the pipeline from the message that
  triggered the tag. There is no argument for a model to get wrong.
- **`Deliverer` remains the only caller of `adapter.send()`.** This module
  calls `adapter.react()`, a different port method that cannot deliver text.
- **A reaction failure can never block or duplicate the reply.** `_react`
  catches every exception, `SignalReactor.react` never raises, and both
  coroutines run detached under `spawn`, so nothing here can propagate into
  the pipeline's `try`/`finally`, trigger its `except BaseException`, or cause
  an early return. The reply path does not observe this module's outcome at
  all — only the reverse.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from nora.domain.models import ConversationTarget, NormalizedMessage

log = logging.getLogger("nora.engagement.indicate")

ACK = "\N{EYES}"                       # 👀 — accepted, working
DONE = "\N{WHITE HEAVY CHECK MARK}"    # ✅ — replied
GAVE_UP = "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}"  # ⚠️ — finished with nothing to say


@dataclass
class Indicator:
    """One tag's reaction lifecycle. Per-tag state lives here and dies with the
    tag — never on the adapter, which outlives every tag and would accumulate
    one entry per conversation forever (the `Handler._prefills` leak shape)."""

    adapter: Any
    target: ConversationTarget
    author: str
    timestamp: int
    tag_id: str
    metrics: Any
    # The platform's OWN handle for the tagging message, when it has one.
    # Empty on Signal, which addresses a message by (author, timestamp); a
    # snowflake on Discord, whose reaction endpoint accepts nothing else. Both
    # are passed to `react` and each adapter uses the one its platform speaks.
    message_id: str = ""
    placed: asyncio.Event = field(default_factory=asyncio.Event)
    # First-resolution-wins latch (DEC-83). `resolve` may be called twice —
    # once the moment a reply is delivered, and once from the pipeline's
    # `finally` on the way out — and only the first may place a terminal emoji.
    # An `Event` rather than a bool because it is also the honest name for the
    # state: set exactly once, never cleared, read from more than one task.
    settled: asyncio.Event = field(default_factory=asyncio.Event)


async def _react(indicator: Indicator, emoji: str, *, phase: str, remove: bool = False) -> None:
    """One reaction attempt, counted, and silent on every failure.

    An adapter with no `react` attribute at all is the "future platform that
    cannot react" case: it is counted as `unsupported` and skipped, never
    crashed on. That check is `getattr`, not `isinstance` against the port,
    because a partially-implemented or older adapter is exactly the thing that
    must degrade here — asking the port whether it *should* have the method
    answers the wrong question.
    """
    react = getattr(indicator.adapter, "react", None)
    platform = indicator.target.platform
    if react is None:
        indicator.metrics.inc(
            "nora_reactions_total", platform=platform, phase=phase, status="unsupported",
        )
        return
    try:
        result = await react(
            target=indicator.target, emoji=emoji, to_author=indicator.author,
            to_timestamp=indicator.timestamp, to_message_id=indicator.message_id,
            remove=remove,
        )
        status = "sent" if result.ok else ("failed" if result.supported else "unsupported")
    except Exception:
        # R-5: decoration failing is never allowed to become the group's problem,
        # or the tag's. Logged for the operator, counted, and dropped.
        log.warning("reaction %s failed (tag=%s)", phase, indicator.tag_id, exc_info=True)
        status = "failed"
    indicator.metrics.inc(
        "nora_reactions_total", platform=platform, phase=phase, status=status,
    )


async def _place(indicator: Indicator) -> None:
    try:
        await _react(indicator, ACK, phase="ack")
    finally:
        # `finally`, not a trailing statement: `_resolve` waits on this event,
        # and a cancelled or failed placement must still release it or the
        # resolution never runs and the 👀 is exactly as stuck as if we had
        # never tried to resolve it.
        indicator.placed.set()


async def _resolve(indicator: Indicator, *, delivered: bool) -> None:
    with contextlib.suppress(Exception):
        await indicator.placed.wait()
    # Retract the 👀 explicitly before placing the terminal emoji. Signal's
    # data model appears to allow one reaction per author per message, which
    # would make the second `sendReaction` replace the first on its own and
    # this call redundant, but that is unverified. The two failure
    # modes are wildly asymmetric: a redundant retract costs one off-path RPC
    # nobody sees, while a missing one leaves 👀 and ✅ stuck on the message
    # together, which is the ambiguity the whole feature exists to remove. So
    # it is sent unconditionally, and a failure of it is ignored on purpose —
    # landing on "no reaction" is degraded but never misleading.
    await _react(indicator, ACK, phase="clear", remove=True)
    await _react(
        indicator, DONE if delivered else GAVE_UP,
        phase="done" if delivered else "gave_up",
    )


def begin(
    *,
    adapter: Any,
    message: NormalizedMessage,
    tag_id: str,
    metrics: Any,
    spawn: Callable[[Coroutine[Any, Any, None]], Any],
) -> Indicator | None:
    """Schedule the 👀 on the tagging message. Returns the handle `resolve`
    needs, or None if this message cannot be reacted to at all.

    `sender_id` is the reaction's target author, and it is the right value: it
    is whatever `nora.platform.signal.parser` resolved from `source`,
    `sourceNumber` or `sourceUuid`, and signal-cli accepts a phone number or a
    bare ACI UUID interchangeably wherever a RECIPIENT is expected (see
    `nora.platform.signal.react`'s module docstring). A sender behind phone
    number privacy therefore still gets reacted to under their ACI.

    A message with no sender id or no timestamp is the one case with no
    possible target, so no indicator is created — returning None rather than
    creating one that can only fail keeps the "no reaction" state out of the
    failure counter, where it would be noise.
    """
    if not message.sender_id or not message.timestamp:
        log.debug("no reaction target for tag=%s; skipping the indicator", tag_id)
        return None
    indicator = Indicator(
        adapter=adapter, target=message.target(), author=message.sender_id,
        timestamp=message.timestamp, tag_id=tag_id, metrics=metrics,
        message_id=message.message_id,
    )
    spawn(_place(indicator))
    return indicator


def resolve(
    *,
    indicator: Indicator | None,
    delivered: bool,
    spawn: Callable[[Coroutine[Any, Any, None]], Any],
) -> None:
    """Replace the 👀 with ✅ (a reply went out) or ⚠️ (none did).

    Called from TWO places, and idempotent because of it (DEC-83):

    - `DeliverySettler`, the instant the first reply is delivered. This is the
      one people see: the pipeline's `finally` runs only after the agent loop
      drains, and the loop ALWAYS makes one more model call after `send_reply`
      returns, so waiting for it would leave the reply on every device while
      the 👀 stays, which reads as «she answered but is still stuck».
    - the pipeline's `finally`, so every exit path a tag has — timeout, model
      error, guard veto, a model that never called `send_reply`, and the outer
      `BaseException` catch — still replaces the 👀. That placement is the whole
      guarantee: there is no way to leave the turn with the 👀 still on.

    FIRST CALL WINS, and the ordering makes that the correct rule rather than an
    arbitrary one: the only call that can precede the `finally` is a delivery
    that already happened, and `delivered=True` is not something a later exit
    path can invalidate. A turn that delivers and THEN faults is still a turn
    the group got an answer from, so ✅ — not ⚠️ — remains the honest mark.
    """
    if indicator is None or indicator.settled.is_set():
        return
    # SPAWN FIRST, LATCH SECOND. `spawn` is `asyncio.create_task` underneath,
    # which raises `RuntimeError` when there is no running loop or it is
    # closing. Latching first would leave the indicator marked settled with
    # nothing scheduled — the pipeline's `finally` would see
    # `settled.is_set()`, return, and the group would keep the 👀 FOREVER on a
    # turn that had answered. Ordered this way a failed spawn leaves the latch
    # clear and the `finally` still does its job.
    spawn(_resolve(indicator, delivered=delivered))
    indicator.settled.set()


class DeliverySettler:
    """Places the terminal ✅ as soon as a reply is actually delivered (DEC-83).

    A COLLABORATOR, not a callback: the repo's test-seam policy is that
    production classes take collaborators through `__init__` only — no `*_fn`
    hooks — so `Deliverer` receives one of these rather than a function. It also
    keeps `Deliverer` free of any reaction vocabulary; all it knows is that it
    has something to tell «a reply went out» to, and a no-op default when
    reactions are off.

    Idempotence lives in `resolve`, not here, because the `finally` path must
    share the same latch — a flag owned by this object could not stop it.
    """

    def __init__(
        self,
        *,
        indicator: Indicator | None,
        spawn: Callable[[Coroutine[Any, Any, None]], Any],
    ) -> None:
        self._indicator = indicator
        self._spawn = spawn

    def settle(self) -> None:
        """A reply reached the group. Safe to call on every message of a
        multi-message turn (DEC-40) — the second and later calls are latched
        out, so the ✅ marks the FIRST reply rather than moving down the turn.

        NEVER RAISES, and that is load-bearing rather than tidy. This runs SYNCHRONOUSLY inside
        `Deliverer.deliver`,
        after `adapter.send` has already succeeded — the message is on every
        device by now. An exception escaping here would climb `send_reply` →
        `invoke.run`'s `except Exception` and be counted
        `nora_silent_failures_total{reason="llm_error"}`: a delivered reply
        reported as a failed turn. Decoration is never allowed to become the
        turn's problem (R-5), which is exactly why `_react` swallows too.
        """
        try:
            resolve(indicator=self._indicator, delivered=True, spawn=self._spawn)
        except Exception:
            log.warning(
                "settling the indicator failed (tag=%s); the reply was DELIVERED "
                "and the pipeline's finally will still resolve it",
                self._indicator.tag_id if self._indicator else "-", exc_info=True,
            )
