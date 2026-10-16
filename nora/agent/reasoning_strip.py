"""Detector for reasoning blocks the Chat Completions transport discards.

OmniRoute's Chat Completions transport DISCARDS `reasoningContent` in a
multi-turn position; this module counts the turns where that happens
(`docs/adr/0013-the-reasoning-strip-is-counted-in-postgres.md`).

OBSERVATION ONLY. Nothing here changes a request, a retry, a `reasoning_effort`
or a transport. The drop is deterministic (a request-shape incompatibility), and
a retry would make it look intermittent.

The fix lives beside it: `nora.agent.reasoning_carry` puts the reasoning back on
the wire, and the watch asks the model which blocks it still drops
(`dropped_reasoning_blocks`), judged against the request it actually formats.

The detector is not a log scraper. The only signal the SDK emits is a WARNING
line from `strands.models.openai` ("reasoningContent is not supported in
multi-turn conversations with the Chat Completions API."). Matching that string
would bind the instrument to a third-party sentence, and a detector that
silently stops detecting reads as a fix.

It binds to the property instead. `OpenAIModel._format_regular_messages` drops
every content block carrying a `reasoningContent` key before it builds the
request (the same `if any(block_type in content for block_type in ["toolResult",
"toolUse", "reasoningContent"]): continue` that emits the warning one loop
above). `reasoning_blocks` counts exactly those blocks in the message list the
pending call is about to be formatted from: same predicate, first-party, no
string.

`tests/unit/test_strands_reasoning_transport_contract.py` formats a synthetic
multi-turn exchange through the REAL `OpenAIModel` and asserts both halves: the
SDK drops the reasoning, and `reasoning_blocks` agrees that it will. If an SDK
starts carrying reasoning through, that test goes red instead of leaving this
module over-counting. The detector is judged against the formatter's own output,
never against itself.
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
    """The default clock. Local because importing `nora.agent.tools` would pull
    in the whole tool surface for one function."""
    return datetime.now(UTC)


#: How long ONE row's INSERT may take before it is abandoned.
#:
#: A slot-hold bound, not a tuning knob (same rule as
#: `nora.app.fire.FirePath.__init__`, `standing_store_timeout_seconds`): the
#: conversation's slot is held around the whole turn, `create_sql_engine` sets
#: no `command_timeout` and nora sets no `statement_timeout`, so an unbounded
#: store await wedges the room silently, which `except Exception` cannot catch.
#:
#: Two seconds because the work is one INSERT of ten small columns into a table
#: with one index. If the bound binds, the database is wedged and losing the row
#: is better than losing the room.
#:
#: Not a config key: `nora.agent` may not import `nora.config`. Injectable
#: through `__init__` as a test seam, like every other collaborator here.
DEFAULT_WRITE_TIMEOUT_SECONDS = 2.0

#: The content-block key the Chat Completions formatter refuses to carry.
#: One spelling, shared by the detector and the contract test that pins the
#: SDK's behaviour against it.
REASONING_BLOCK_KEY = "reasoningContent"

#: The request field `nora.agent.reasoning_carry` puts a prior assistant
#: message's reasoning in: the one the upstream READS (`reasoning_content` is
#: accepted and discarded). One spelling, shared by the carry and the detector
#: that judges it.
CARRIED_REASONING_FIELD = "reasoning"


def reasoning_blocks(messages: Iterable[Any] | None) -> int:
    """How many content blocks in this message list the transport will DROP.

    Defensive at every level (`getattr`/`isinstance` throughout), as in
    `factory.final_text`: this reads a third-party structure on the reply path,
    and a reshaped message must degrade to "I saw none" rather than raise into
    the turn. A zero read for the wrong reason is caught by the contract test.
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

    Each message holding reasoning is FORMATTED by the model (`format_request`,
    the method its `stream` calls), and a `reasoningContent` block counts as
    carried only when its text is in the `reasoning` field of ITS OWN message's
    formatted entry (ADR 0013). A model that does not route through
    `nora.agent.reasoning_carry`'s override therefore reads as a strip. Pairing
    per message, never "found in any carried field", keeps a dropped short
    reasoning from being vouched for by a longer carried one that contains it.

    No block, no formatting: a turn at `reasoning_effort = "none"` pays one walk
    of the list. A model with no `format_request`, or one that raises, counts
    every block as dropped; failures err toward over-counting, which is visible,
    where an under-count reads as a fix.
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
                # `undash`: the dash-free model rewrites the request after
                # formatting, and the carried text is what she reads.
                if text and (text in field or undash(text) in field):
                    reached += 1
    except Exception:  # noqa: BLE001 — on the reply path, inside a hook
        log.warning("reasoning_strip: could not format the pending request")
        return blocks
    return max(blocks - reached, 0)


def _model_identity(agent: Any) -> tuple[str, str]:
    """`(model_id, reasoning_effort)` as the PROVIDER holds them right now.

    Read off the model rather than passed in from `nora.config`: config says
    what was configured, this says what the call actually carried (the combo
    behind an alias can change upstream).

    Unreadable degrades to `("", "")`, which the row records as an absence. It
    never guesses.
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
    `ToolCallLog`, and a `HookProvider` of its own: the guard is veto logic and
    `ToolCallLog` is the tool-recall log's write path; this is a third
    responsibility with a third store.

    `store=None` is the inert default (offline and eval compositions): the
    Prometheus counters are recorded and no row is written.

    Register it AFTER the guard. `BeforeModelCallEvent` dispatches in
    registration order (`HookEvent.should_reverse_callbacks` is False; only the
    After events reverse), so a watch registered after `ReplyGuard` sees
    `event.cancel` already set. The guard cancels the pass that follows a
    terminal verb, and then `agent.messages` holds the reasoning-carrying
    assistant message that a naive watch would count as a strip; a request that
    is never formatted strips nothing. `nora.agent.factory` puts it last in
    `hooks=[...]` and `tests/unit/test_reasoning_strip_probe.py::
    test_a_cancelled_model_call_is_not_an_opportunity` holds it there.
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
        # Calls that COMPLETED, not attempts (as in `ReplyGuard._after_model`):
        # `BeforeModelCallEvent` fires inside the retry loop, so an attempt that
        # raised is the same call re-entered, not the next one.
        self._model_calls = 0
        self._continuation_calls = 0
        self._strip_calls = 0
        self._stripped_blocks = 0
        self._model = ""
        self._reasoning_effort = ""
        # `(depth, blocks)` for a call admitted and not yet settled. `depth` is
        # `len(agent.messages)`: strictly increasing between real calls (the
        # loop appends the answer and tool results before the next pass) and
        # identical across the attempts of one call. It separates a retry from
        # the next call, and stops a hook that re-asks a successful response
        # from counting one position twice.
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
                # Another hook already refused this pass (see the class
                # docstring). Nothing will be formatted, so nothing is stripped.
                self._pending = None
                return
            agent = getattr(event, "agent", None)
            messages = getattr(agent, "messages", None) or []
            model, effort = _model_identity(agent)
            # `unavailable` never overwrites `available` (R-27). `_model_identity`
            # answers `("", "")` for four distinct conditions (no model, no
            # `get_config`, a config that raised, a config missing the keys),
            # and all four land in a NOT NULL TEXT column as the same empty
            # string. Only a real reading replaces a real reading.
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

        `exception is None` is the discriminator, as in
        `ReplyGuard._after_model`: the retry strategy only retries a pass that
        raised, so that pass produced no answer and the next
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
                # The same position, asked twice. Counted once: the ratio is
                # strips per opportunity in the conversation, not per HTTP
                # request.
                return
            self._last_depth = depth
            is_continuation = self._model_calls > 0
            self._model_calls += 1
            if is_continuation:
                self._continuation_calls += 1
            if blocks:
                self._strip_calls += 1
                # MAX, not a running sum: the message list only grows, so every
                # later request re-drops blocks earlier ones already lost, and a
                # sum would count the first block once per pass. The maximum is
                # the number of DISTINCT blocks refused by the end of the turn;
                # how often it happened is `strip_calls`.
                self._stripped_blocks = max(self._stripped_blocks, blocks)
                if not is_continuation:
                    # Structurally unreachable: a `reasoningContent` block can
                    # only enter `agent.messages` from a model answer this loop
                    # received, and `nora.agent.prefill` builds plain text.
                    # Counted rather than clamped, because a clamp would make
                    # the impossible look ordinary; the resulting row has
                    # `strip_calls > continuation_calls`, `flush` refuses to
                    # write it, and the write counter records `impossible`.
                    # This log line is the channel that reaches a reader.
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
        so zero denominator. Writing it would turn the table from a census of
        the calls at risk into a log of every model invocation.
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
        """Write the row, once, bounded, and never at the cost of the turn.

        Every caller awaits this while the conversation's slot is held, and
        `release_and_dispatch` cannot run until it returns, so an unbounded
        store await is a room that stops answering (arriving tags queued or
        shed, `nora_tags_dropped_total{reason="in_flight"}`; heartbeat reminders
        deferred). `except Exception` is no defence: a wedged Postgres does not
        raise, it does not answer, and there is no `command_timeout` on the
        engine or `statement_timeout` on the server. `asyncio.wait_for` is the
        only bound.

        The bound lives here, not at the call sites, because the store is
        reached from exactly one place.
        `tests/architecture/test_slot_held_awaits_are_bounded.py` fails if the
        `wait_for` leaves this function or loses its timeout.

        Idempotent: a caller that flushes on both the happy path and an error
        path writes one row.

        Everything is inside the `try`, including building the observation and
        recording the counter: this runs past the stage's own `except`, so an
        escaping exception would land on the turn.
        """
        if self._flushed:
            return
        self._flushed = True
        if self._store is None:
            return
        # `None` until there is something to report. A turn with no opportunity
        # writes no row and moves no write counter; a `failed` for it would put
        # every single-call turn on the series that says the record has gone
        # blind.
        outcome: str | None = "failed"
        try:
            observation = self.observation
            if observation is None:
                outcome = None
                return
            if observation.strip_calls > observation.continuation_calls:
                # Cannot come from the loop. The table's CHECK would refuse it as
                # an `IntegrityError` indistinguishable from a database blip, so
                # it is refused here under its own name.
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
                # The counter is the last thing that may take a turn down:
                # `Metrics.inc` raises `KeyError` on an undeclared name, and this
                # runs past the stage's own `except`.
                log.exception("ReasoningStripWatch could not record its outcome")
