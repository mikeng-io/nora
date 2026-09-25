"""The instrument for a defect that had none: OmniRoute's Chat Completions
transport DISCARDS `reasoningContent` in a multi-turn position, and until this
module nothing in the system could tell such a turn from a healthy one
(issue #325, `docs/adr/0013-the-reasoning-strip-is-counted-in-postgres.md`).

OBSERVATION ONLY. Nothing here changes a request, a retry, a `reasoning_effort`
or a transport. The drop is deterministic — a request-shape incompatibility,
not flakiness — and a retry would turn it into an apparently intermittent one.

The FIX lives beside it, not in it: `nora.agent.reasoning_carry` puts the
reasoning back on the wire, and the watch asks the model which blocks it still
drops, judged against the request it actually formats
(`dropped_reasoning_blocks`), rather than assuming all of them are.

WHY THE DETECTOR IS NOT A LOG SCRAPER
-------------------------------------
The only signal the SDK emits is one line from `strands.models.openai` at
WARNING level:

    reasoningContent is not supported in multi-turn conversations with the
    Chat Completions API.

Matching that string would bind the instrument to a third-party sentence that
can change in a patch release, and a detector that silently stops detecting is
worse than none — the failure mode would be a series that quietly goes to zero
and reads as a fix.

So this binds to the PROPERTY instead. `OpenAIModel._format_regular_messages`
drops every content block carrying a `reasoningContent` key before it builds
the request (it is the same `if any(block_type in content for block_type in
["toolResult", "toolUse", "reasoningContent"]): continue` that emits the
warning one loop above). `reasoning_blocks` below counts exactly those blocks
in the message list the pending call is about to be formatted from. Same
predicate, first-party, no string.

THE FAILURE IS LOUD, and it is a test rather than a hope.
`tests/unit/test_strands_reasoning_transport_contract.py` formats a
synthetic multi-turn exchange through the REAL `OpenAIModel` and asserts both
halves at once: the SDK drops the reasoning, and `reasoning_blocks` agrees that
it will. If a future SDK starts carrying reasoning through, that test goes RED
and says so — it does not leave this module quietly over-counting a drop that
no longer happens. That is the no-self-attestation rule applied to a detector:
it is judged against the formatter's own output, never against itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from strands.hooks import (
    AfterModelCallEvent,
    BeforeModelCallEvent,
    HookProvider,
    HookRegistry,
)

from ..domain.reasoning_strip import ReasoningStripObservation
from ..obs.metrics import Metrics
from .punctuation import undash

log = logging.getLogger("nora.reasoning_strip")


def _utc_now() -> datetime:
    """The default clock. Local rather than imported from `nora.agent.tools`,
    which pulls the whole tool surface in behind it for one function."""
    return datetime.now(UTC)


#: How long ONE row's INSERT may take before it is abandoned.
#:
#: **This is a slot-hold bound, not a tuning knob, and the rule is fifty lines
#: from the call site that needed it** (`nora.app.fire.FirePath.__init__`,
#: `standing_store_timeout_seconds`): the conversation's slot is held around
#: the whole turn, `create_sql_engine` sets no `command_timeout` and nora sets
#: no `statement_timeout`, so an unbounded store await here wedges the room —
#: not with an error, which `except Exception` would catch, but with silence,
#: which it cannot. That defect has now been found three times in this repo:
#: `debrief.run`, then re-created on the fire path in the same change that
#: fixed it, then here (audit, 2026-09-17).
#:
#: Two seconds because the work is one INSERT of ten small columns into a
#: table with one index. A value sized for a query that should take single-digit
#: milliseconds is a bound, not a budget: if it ever binds, the database is
#: wedged and the right outcome is to lose the row rather than the room.
#:
#: NOT a config key. `nora.agent` may not import `nora.config`, and a knob
#: nobody turns is a second thing to keep consistent with the timeout it has to
#: fit inside. Injectable through `__init__` for the test seam, like every
#: other collaborator here.
DEFAULT_WRITE_TIMEOUT_SECONDS = 2.0

#: The content-block key the Chat Completions formatter refuses to carry.
#: ONE spelling, read by the detector and by the contract test that pins the
#: SDK's behaviour against it, so the two cannot drift apart.
REASONING_BLOCK_KEY = "reasoningContent"

#: The request field `nora.agent.reasoning_carry` puts a prior assistant
#: message's reasoning in: the one the upstream READS (`reasoning_content` is
#: accepted and discarded, measured 2026-09-25, #325). One spelling, read by
#: the carry and by the detector that judges it.
CARRIED_REASONING_FIELD = "reasoning"


def reasoning_blocks(messages: Iterable[Any] | None) -> int:
    """How many content blocks in this message list the transport will DROP.

    Defensive at every level — `getattr`/`isinstance` throughout — for
    `factory.final_text`'s reason: this reads a third-party structure on the
    reply path, and a Strands version that reshapes a message must degrade to
    "I saw none" rather than raise into a turn that is otherwise fine. A zero
    read for the wrong reason is caught by the contract test, not by a crash
    in production.
    """
    total = 0
    for message in messages or ():
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        total += sum(
            1 for block in content
            if isinstance(block, dict) and REASONING_BLOCK_KEY in block
        )
    return total


def reasoning_text(block: Any) -> str:
    """The readable text of one `reasoningContent` block; `""` for a redacted
    or unreadable one, which cannot be carried as text."""
    content = block.get(REASONING_BLOCK_KEY) if isinstance(block, dict) else None
    inner = content.get("reasoningText") if isinstance(content, dict) else None
    text = inner.get("text") if isinstance(inner, dict) else None
    return text if isinstance(text, str) else ""


def _carried_field(format_request: Callable[..., Any], message: Any) -> str:
    """The `reasoning` field of THIS message as the model formats it, or `""`.

    The message is formatted on its own, through the same `format_request`
    the model's `stream` calls. The SDK's formatter handles each message
    independently (its loop only appends), so this is the entry this message
    produces in the full request, paired to it by construction rather than by
    matching text across entries. An assistant message the formatter drops
    whole (nothing but reasoning) produces no assistant entry and so no field.
    """
    request = format_request([message])
    fields = [
        entry.get(CARRIED_REASONING_FIELD) for entry in request.get("messages") or ()
        if isinstance(entry, dict) and entry.get("role") == "assistant"
    ]
    return fields[0] if len(fields) == 1 and isinstance(fields[0], str) else ""


def dropped_reasoning_blocks(model: Any, messages: Iterable[Any] | None) -> int:
    """How many reasoning blocks the request THIS model builds will drop,
    judged against what the model formats rather than predicted from the
    messages.

    ADR 0013 decision 1 applied to the fix: each message holding reasoning is
    FORMATTED by the model (`format_request`, the method its `stream` calls),
    and a `reasoningContent` block counts as carried only when its text is in
    the `reasoning` field of ITS OWN message's formatted entry. A Strands
    release that stops routing through `nora.agent.reasoning_carry`'s
    override, or a model that never had it, therefore reads as a strip, not as
    a fix. Pairing per message, never «found in any carried field», is what
    keeps a dropped short reasoning from being vouched for by a longer carried
    one that happens to contain it (review of #506).

    No block, no formatting: a turn at `reasoning_effort = "none"` pays one
    walk of the list and nothing else. A model with no `format_request`, or
    one that raises, counts every block as dropped. The failure is toward
    COUNTING: an over-count is visible in the table; an under-count reads as
    the fix.
    """
    messages = list(messages or ())
    blocks = reasoning_blocks(messages)
    if not blocks:
        return 0
    format_request = getattr(model, "format_request", None)
    if not callable(format_request):
        return blocks
    reached = 0
    try:
        for message in messages:
            if not reasoning_blocks([message]):
                continue
            field = _carried_field(format_request, message)
            if not field:
                continue
            for block in message["content"]:
                text = reasoning_text(block)
                # `undash`, because the dash-free model rewrites the request
                # after formatting it and the carried text is what she READS.
                if text and (text in field or undash(text) in field):
                    reached += 1
    except Exception:  # noqa: BLE001 — on the reply path, inside a hook
        log.warning("reasoning_strip: could not format the pending request")
        return blocks
    return max(blocks - reached, 0)


def _model_identity(agent: Any) -> tuple[str, str]:
    """`(model_id, reasoning_effort)` as the PROVIDER holds them right now.

    Read off the model rather than passed in from `nora.config`, and the
    difference is the whole point of recording them: config says what was
    configured, this says what the call actually carried. #325's two hours of
    log-diving were spent establishing exactly that, after the combo behind the
    alias changed upstream and the operator moved `reasoning_effort` on two
    paths in the same session.

    Unreadable degrades to `("", "")` — an absence, which the row records as an
    absence. It never guesses.
    """
    model = getattr(agent, "model", None)
    get_config = getattr(model, "get_config", None)
    if not callable(get_config):
        return "", ""
    try:
        config = get_config() or {}
        params = config.get("params") or {}
        return str(config.get("model_id") or ""), str(params.get("reasoning_effort") or "")
    except Exception:  # noqa: BLE001 — provenance is not worth losing a turn over
        log.warning("reasoning_strip: could not read the model config")
        return "", ""


class ReasoningStripWatch(HookProvider):
    """Counts, for ONE agent invocation, how many of its continuation model
    calls had their reasoning thrown away by the transport.

    Constructed fresh per turn / per Dreamer sample, like `ReplyGuard` and
    `ToolCallLog`, and a `HookProvider` of its own rather than a method on
    either of them: the guard is veto logic and `ToolCallLog` is the tool-recall
    log's write path, and this is a third responsibility with a third store.

    **`store=None` is the honest INERT default** — the offline and eval
    compositions, and any deployment that has not wired one, record the
    Prometheus counters and write no row. Never a raised error and never a
    fallback that pretends to have remembered something.

    REGISTER IT AFTER THE GUARD. `BeforeModelCallEvent` dispatches in
    REGISTRATION order (`HookEvent.should_reverse_callbacks` is False; only the
    After events reverse), so a watch registered after `ReplyGuard` sees
    `event.cancel` already set by it. That matters more than it sounds: the
    guard cancels the pass that follows a terminal verb, which on the tag path
    is most turns, and at that moment `agent.messages` holds exactly the
    reasoning-carrying assistant message that a naive watch would count as a
    strip. A request that is never formatted strips nothing. `nora.agent.
    factory` puts it last in `hooks=[...]` and
    `tests/unit/test_reasoning_strip_probe.py::
    test_a_cancelled_model_call_is_not_an_opportunity` is what holds it there.
    """

    def __init__(
        self,
        *,
        path: str,
        metrics: Metrics,
        tag_id: str | None = None,
        store: Any = None,
        clock: Callable[[], datetime] | None = None,
        write_timeout_seconds: float = DEFAULT_WRITE_TIMEOUT_SECONDS,
    ) -> None:
        self._path = path
        self._metrics = metrics
        self._tag_id = tag_id
        self._store = store
        self._clock = clock
        self._write_timeout_seconds = write_timeout_seconds
        # Calls that COMPLETED, not attempts — `ReplyGuard._after_model`'s rule
        # and for its reason: `BeforeModelCallEvent` fires inside the retry
        # loop, so an attempt that raised is the SAME call re-entered, not the
        # next one.
        self._model_calls = 0
        self._continuation_calls = 0
        self._strip_calls = 0
        self._stripped_blocks = 0
        self._model = ""
        self._reasoning_effort = ""
        # `(depth, blocks)` for a call that has been admitted and not yet
        # settled. `depth` is `len(agent.messages)`, which is strictly
        # increasing between real calls (the loop appends the answer and the
        # tool results before the next pass) and IDENTICAL across the attempts
        # of one call — so it is what separates «a retry of this call» from
        # «the next call», and what keeps a hook that re-asks a successful
        # response from counting one position twice.
        self._pending: tuple[int, int] | None = None
        self._last_depth = -1
        self._flushed = False

    # -- the two hooks ------------------------------------------------------

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self._before_model)
        registry.add_callback(AfterModelCallEvent, self._after_model)

    def _before_model(self, event: BeforeModelCallEvent) -> None:
        try:
            if event.cancel:
                # Another hook has already refused this pass — see the class
                # docstring. Nothing will be formatted, so nothing is stripped.
                self._pending = None
                return
            agent = getattr(event, "agent", None)
            messages = getattr(agent, "messages", None) or []
            model, effort = _model_identity(agent)
            # **`unavailable` never overwrites `available`** (R-27's rule,
            # applied to provenance). `_model_identity` answers `("", "")` for
            # four distinct conditions — no model, no `get_config`, a config
            # that raised, a config missing the keys — and all four land in a
            # NOT NULL TEXT column as the same empty string. Overwriting
            # unconditionally meant a transient read failure on the LAST call
            # of a turn erased a good read from the first, turning a known
            # model into an unknown one with nothing reporting the
            # substitution. Only a real reading replaces a real reading.
            if model:
                self._model = model
            if effort:
                self._reasoning_effort = effort
            self._pending = (
                len(messages),
                dropped_reasoning_blocks(getattr(agent, "model", None), messages),
            )
        except Exception:
            log.exception("ReasoningStripWatch._before_model raised; ignoring")
            self._pending = None

    def _after_model(self, event: AfterModelCallEvent) -> None:
        """Settle the pass that just answered.

        `exception is None` is the discriminator, exactly as in
        `ReplyGuard._after_model`: the shipped retry strategy only retries a
        pass that raised, so a pass that raised produced no answer and the next
        `BeforeModelCallEvent` is a re-attempt of the same position.
        """
        try:
            pending, self._pending = self._pending, None
            if pending is None:
                return
            if getattr(event, "exception", None) is not None:
                return
            depth, blocks = pending
            if depth <= self._last_depth:
                # The same position, asked twice. Counted once: the ratio this
                # instrument exists to report is strips per OPPORTUNITY in the
                # conversation, not per HTTP request.
                return
            self._last_depth = depth
            is_continuation = self._model_calls > 0
            self._model_calls += 1
            if is_continuation:
                self._continuation_calls += 1
            if blocks:
                self._strip_calls += 1
                # MAX, not a running sum, and the difference is what the number
                # means. The message list only grows, so every later request
                # re-drops the blocks the earlier ones already lost: a five-pass
                # turn would sum the first block four times and report "10
                # blocks lost" for two pieces of reasoning. The maximum is how
                # many DISTINCT reasoning blocks the transport refused to carry
                # by the end of the turn, which is the quantity a reader is
                # actually asking about. How OFTEN it happened is `strip_calls`.
                self._stripped_blocks = max(self._stripped_blocks, blocks)
                if not is_continuation:
                    # Structurally unreachable today — a `reasoningContent`
                    # block can only enter `agent.messages` from a model answer
                    # this loop received, and `nora.agent.prefill` builds plain
                    # text. Counted anyway rather than clamped away, because a
                    # clamp would make the impossible look ordinary; the row it
                    # would produce carries `strip_calls > continuation_calls`,
                    # `flush` refuses to write it, and the write counter
                    # records `impossible` rather than `failed`. THIS LINE is
                    # the channel that reaches a reader — an earlier version of
                    # this comment claimed the ROW was, which the table's own
                    # CHECK made untrue.
                    log.warning(
                        "reasoning_strip: the FIRST model call of a %s turn "
                        "already carried %d reasoning block(s) — the prefill, "
                        "not the loop, is the source", self._path, blocks,
                    )
            if is_continuation:
                self._metrics.inc(
                    "nora_reasoning_strip_total",
                    path=self._path,
                    outcome="stripped" if blocks else "clean",
                )
        except Exception:
            log.exception("ReasoningStripWatch._after_model raised; ignoring")

    # -- what the turn leaves behind ----------------------------------------

    @property
    def observation(self) -> ReasoningStripObservation | None:
        """The row this turn would write, or `None` when it had no opportunity.

        `None` means the loop never went multi-turn: zero continuation calls,
        so zero denominator and nothing to divide. Writing it would turn this
        table from a census of the calls at risk into a log of every model
        invocation in the system.
        """
        if self._continuation_calls <= 0:
            return None
        now = self._clock() if self._clock is not None else _utc_now()
        return ReasoningStripObservation(
            id=uuid4(),
            path=self._path,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            tag_id=self._tag_id,
            model_calls=self._model_calls,
            continuation_calls=self._continuation_calls,
            strip_calls=self._strip_calls,
            stripped_blocks=self._stripped_blocks,
            created_at=now,
        )

    async def flush(self) -> None:
        """Write the row, once, BOUNDED, and never at the cost of the turn.

        **The bound is the point, and it is why this method is not one line.**
        Every caller awaits this while the conversation's slot is held, and
        `release_and_dispatch` cannot run until it returns — so an unbounded
        store await here is a room that stops answering, with arriving tags
        queued or shed (`nora_tags_dropped_total{reason="in_flight"}`) and
        heartbeat reminders deferred. `except Exception` is no defence: a
        wedged Postgres does not raise, it does not ANSWER, and there is no
        `command_timeout` on the engine and no `statement_timeout` on the
        server. `asyncio.wait_for` is the only thing between the two.

        The bound lives HERE rather than at the five call sites because the
        store is reached from exactly one place, and five `wait_for`s with five
        `except TimeoutError`s around them is five chances to write four of
        them. `tests/architecture/test_slot_held_awaits_are_bounded.py` reads
        this function and fails if the `wait_for` leaves it or loses its
        timeout.

        Idempotent: a caller that flushes on both the happy path and an error
        path writes one row, and the Dreamer flushes from three exits.

        Everything is inside the `try`, including building the observation and
        recording the counter — this runs past the stage's own `except`, so an
        exception escaping here lands on the turn it was supposed to cost
        nothing.
        """
        if self._flushed:
            return
        self._flushed = True
        if self._store is None:
            return
        # `None` until there is something to report. A turn with no
        # opportunity writes no row AND moves no write counter — recording
        # a `failed` for it would put every single-call turn in the system
        # on the one series that says the record has gone blind.
        outcome: str | None = "failed"
        try:
            observation = self.observation
            if observation is None:
                outcome = None
                return
            if observation.strip_calls > observation.continuation_calls:
                # Cannot come from the loop; the table's CHECK refuses it and
                # would arrive as an `IntegrityError` indistinguishable from a
                # database blip. Refused here instead, under its own name.
                log.error(
                    "reasoning_strip: refusing an impossible row (%s): "
                    "strip_calls=%d > continuation_calls=%d",
                    self._path, observation.strip_calls,
                    observation.continuation_calls,
                )
                outcome = "impossible"
                return
            await asyncio.wait_for(
                self._store.record_observation(observation),
                timeout=self._write_timeout_seconds,
            )
            outcome = "ok"
        except Exception:
            log.exception("ReasoningStripWatch write failed; ignoring")
        finally:
            try:
                if outcome is not None:
                    self._metrics.inc(
                        "nora_reasoning_strip_writes_total", outcome=outcome,
                    )
            except Exception:
                # The counter itself is the last thing that may take a turn
                # down. `Metrics.inc` raises `KeyError` on an undeclared name,
                # and this runs past the stage's own `except`.
                log.exception("ReasoningStripWatch could not record its outcome")
