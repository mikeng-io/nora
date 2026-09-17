"""The instrument for a defect that had none: OmniRoute's Chat Completions
transport DISCARDS `reasoningContent` in a multi-turn position, and until this
module nothing in the system could tell such a turn from a healthy one
(issue #325, `docs/adr/0013-the-reasoning-strip-is-counted-in-postgres.md`).

OBSERVATION ONLY. Nothing here changes a request, a retry, a `reasoning_effort`
or a transport. The drop is deterministic — a request-shape incompatibility,
not flakiness — and a retry would turn it into an apparently intermittent one.

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

log = logging.getLogger("nora.reasoning_strip")


def _utc_now() -> datetime:
    """The default clock. Local rather than imported from `nora.agent.tools`,
    which pulls the whole tool surface in behind it for one function."""
    return datetime.now(UTC)


#: The content-block key the Chat Completions formatter refuses to carry.
#: ONE spelling, read by the detector and by the contract test that pins the
#: SDK's behaviour against it, so the two cannot drift apart.
REASONING_BLOCK_KEY = "reasoningContent"


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
    ) -> None:
        self._path = path
        self._metrics = metrics
        self._tag_id = tag_id
        self._store = store
        self._clock = clock
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
            self._model, self._reasoning_effort = _model_identity(agent)
            self._pending = (len(messages), reasoning_blocks(messages))
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
                    # text. Said out loud rather than clamped away: the row it
                    # produces carries `strip_calls > continuation_calls`,
                    # which is a shape no correct reading can explain, and that
                    # is exactly what should reach whoever looks.
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
        """Write the row, once, and never at the cost of the turn.

        Idempotent: a caller that flushes on both the happy path and an error
        path writes one row. Exceptions are swallowed and counted — this is a
        record of something that has already happened, and a failed write must
        not become the reason a reply does not go out (`ToolCallLog`'s rule).
        """
        if self._flushed:
            return
        self._flushed = True
        observation = self.observation
        if observation is None or self._store is None:
            return
        try:
            await self._store.record_observation(observation)
            self._metrics.inc("nora_reasoning_strip_writes_total", outcome="ok")
        except Exception:
            log.exception("ReasoningStripWatch write failed; ignoring")
            self._metrics.inc("nora_reasoning_strip_writes_total", outcome="failed")
