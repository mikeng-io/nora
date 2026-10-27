"""`LiveAgentRunner` — the real `nora.engagement.stages.invoke.AgentRunner`.

Faking the model for tests is a fake `AgentRunner` passed to
`EngagementPipeline`'s constructor (see `tests/integration/harness.py`'s
`FakeAgentRunner` and `tests/unit/test_engagement_pipeline.py`'s
`ScriptedRunner`), never an `_invoke_fn` hook. This class is the production
side of that seam: it wraps `nora.agent.factory`'s Strands plumbing so
`nora.engagement.stages.invoke` never has to import `strands` itself (only
`nora.agent.**` may — `tests/test_boundaries.py::test_strands_import_boundary`).

Built once at process start around a `nora.agent.factory.ModelSource`, which
builds ONE MODEL PER TAG over the process-wide `AsyncOpenAI` client (one
CLIENT per process).

One model per tag because `tool_choice` is per-turn state (`required` until she
has spoken, `auto` after) and the engagement in-flight lock
(`nora.engagement.slots`) keys on `(platform, conversation_id)`, so two
conversations run concurrently: a shared model would apply tag A's relaxation to
tag B's FIRST call. `tests/test_model_per_tag.py` is the probe.

`models` is typed `Any`, not `nora.agent.factory.ModelSource`, so that this
module does not need to import strands either — only `nora.app.composition`,
which builds the source, ever names the SDK type.

The SDK result's text is extracted by `nora.agent.factory.final_text`, not
here: it reads Strands' `AgentResult.message` content blocks, which this module
must not import, and the Dreamer needs the identical extraction while
`nora.agent` may not import `nora.app`.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.tag_trace_extract import extract_tag_trace
from nora.agent.turn_recording import TurnRecording
from nora.doctrine.loader import Doctrine
from nora.domain.models import TagContext
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_record import retain_exchange
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

# The default cap on how many characters of `agent.messages` a `TagTrace`
# captures (`nora.agent.tag_trace_extract.cap_messages`'s `max_chars`).
DEFAULT_MAX_TRACE_CHARS = 10_000

# How long the budget-exhaustion draft may take, and the budget it runs under.
# ITS OWN timeout, not the tier's: this call happens on a turn that has ALREADY
# spent its whole budget, so inheriting the tier's timeout would let one
# cut-off turn hold a conversation slot for minutes. It is a single completion
# over context already in hand — no tools, no round trips — and expiring costs
# the canned line.
DEFAULT_DRAFT_TIMEOUT_SECONDS = 45.0
# One cycle, and enough output for a real answer. `turns=1` is the load-bearing
# one: it makes the draft structurally incapable of becoming another agent loop
# even if `tool_choice="none"` is ignored by the provider. `total_tokens` only
# has to cover the context being re-read, which is bounded by the cap that just
# bound.
DEFAULT_DRAFT_LIMITS: dict[str, int] = {
    "turns": 1, "output_tokens": 4_000, "total_tokens": 1_100_000,
}

# The caps that mean "cut off mid-work", as opposed to a turn that finished or
# one that died. Duplicated from `nora.engagement.stages.deliver` rather than
# imported: `nora.app` must not import `nora.engagement` (the layering
# `lint-imports` enforces).
_BUDGET_STOP_REASONS = frozenset({
    "limit_turns", "limit_total_tokens", "limit_output_tokens",
})


class LiveAgentRunner:
    """Satisfies `nora.engagement.stages.invoke.AgentRunner`."""

    def __init__(
        self, *, models: Any, retry: factory.RetryPolicy,
        skills_dir: Path | None = None,
        agent_name: str = factory.DEFAULT_AGENT_NAME,
        max_trace_chars: int = DEFAULT_MAX_TRACE_CHARS,
        metrics: Metrics | None = None,
        draft_timeout_seconds: float = DEFAULT_DRAFT_TIMEOUT_SECONDS,
    ) -> None:
        self._models = models
        # The agent's machine name (cfg.agent.name) — its Strands agent_id and
        # Langfuse trace span. Held per-runner so a cloned agent traces under its
        # own name.
        self._agent_name = agent_name
        # Resolved once, from the tier's invocation timeout, and reused for
        # every tag — the numbers are immutable, the strategy built from them
        # is not (see `factory.RetryPolicy`).
        self._retry = retry
        # The on-demand skill layer (DEC-50). None leaves it INERT — the
        # offline/eval runners never set it. When the composition root wires
        # it, `factory.create_agent` builds one `AgentSkills` plugin per tag
        # over this operator directory. Held as the resolved path so the
        # per-tag construction stays a pure hand-off.
        self._skills_dir = skills_dir
        # `TagTrace` extraction — cap and metrics sink for
        # `nora.agent.tag_trace_extract.extract_tag_trace`.
        self._max_trace_chars = max_trace_chars
        # Falls back to a private registry when the caller has none to share.
        # Every production caller (the composition root) passes the process
        # metrics so `nora_tagtrace_truncations_total` lands where `/metrics`
        # actually reads it; a fallback instance is only ever read by tests
        # and evals that do not assert on that counter.
        self._metrics = metrics if metrics is not None else Metrics()
        # See DEFAULT_DRAFT_TIMEOUT_SECONDS. Injected through __init__ like
        # every other collaborator (no `*_fn` hooks, no `_for_test` params), so
        # a test can drive the timeout without a clock.
        self._draft_timeout_seconds = draft_timeout_seconds

    async def invoke(
        self,
        *,
        doctrine: Doctrine,
        tools: list[Any],
        pre_fill: list[dict[str, Any]],
        guard: ReplyGuard,
        tool_call_log: Any = None,
        reasoning_watch: Any = None,
        tool_hooks: tuple[Any, ...] = (),
        tag_message: str,
        limits: dict[str, Any],
        ctx: TagContext,
        trace_attributes: dict[str, Any],
        follow_up: Callable[[str], str | None] | None = None,
    ) -> tuple[str | None, str, TagTrace]:
        """Run the turn and return Strands' `stop_reason` (R-23) plus a
        `TagTrace` of what happened.

        **`follow_up`: at most ONE continuation of the same conversation.**
        Asked once, with the first pass's final prose, after a loop that
        ended `end_turn` (never after one of R-8's caps, a guardrail, a stop
        sequence or a stop reason the SDK did not give). A message back is sent as the next user
        turn to THIS `agent` — the same messages, tools, guard (so the same
        per-tool budgets: nothing is re-granted) and trace — under what is
        left of `limits` (`factory.remaining_limits`); None, or a spent cap,
        ends the turn as before. What the continuation returns replaces the
        first pass's `stop_reason` and text. It is never asked twice, and
        the budget draft below never runs after it. The caller decides what
        the prose means; this layer only continues the conversation.

        The stop reason is the SDK's only signal that a turn was cut off by one
        of R-8's caps rather than finished; it is returned as a plain
        `str | None` rather than the SDK's `StopReason` literal for the same
        reason `model` above is `Any` — this module does not import `strands`.

        `baseline` is captured BEFORE `factory.invoke` runs:
        `agent.messages` already holds the system prompt and pre-fill by the
        time `factory.create_agent` returns, and `extract_tag_trace` slices from
        `baseline` onward. `transcript` is left empty here: this class has no
        access to the assembled window, only `nora.engagement.stages.invoke.run`
        does (via `ctx`), and fills it in. `outcome` is PROVISIONAL —
        `"send_reply"` — because this layer has no visibility into delivery,
        recovery or veto; `nora.engagement.pipeline` corrects it via
        `dataclasses.replace` once `deliver.check_outcome` has run. `created_at`
        is stamped here (frozen dataclass, so `dataclasses.replace`) because the
        real timestamp is already knowable.
        """
        recording = TurnRecording()
        started_at = int(time.time() * 1000)
        agent = factory.create_agent(
            # ONE MODEL PER TAG. See this module's docstring: `tool_choice` is
            # per-turn state and two tags overlap.
            model=self._models.create(),
            system_prompt=doctrine.text,
            tools=tools,
            pre_fill=pre_fill,
            guard=guard,
            tool_call_log=tool_call_log,
            # Typed `Any` and threaded rather than built here for
            # `tool_call_log`'s reason: this module may not import `strands`,
            # and the watch is a `HookProvider`. `None` is the inert default
            # every offline/eval composition keeps.
            reasoning_watch=reasoning_watch,
            # The tool surface's own hooks (`ToolSurface.hooks` — today the
            # `ShelfLoader` when anything is deferred). Threaded, not built
            # here, for `tool_call_log`'s reason: this module may not import
            # `strands`.
            tool_hooks=(*tool_hooks, recording),
            trace_attributes=trace_attributes,
            retry=self._retry,
            skills_dir=self._skills_dir,
            agent_name=self._agent_name,
        )
        baseline = len(agent.messages)
        try:
            result = await factory.invoke(agent, tag_message, invocation_state={}, limits=limits)
            # `isinstance`, not a cast: `factory.invoke` is typed `Any` to keep
            # `strands` out of this module, and an unchecked `Any` returned from a
            # `str | None` signature is precisely what mypy's `warn_return_any`
            # exists to stop. A shape that is not a string is not a stop reason.
            stop_reason = getattr(result, "stop_reason", None)
            stop_reason = stop_reason if isinstance(stop_reason, str) else None
            text = factory.final_text(result)
            followed_up = False
            # `end_turn` ONLY: the one stop that means she finished on her own. A
            # cap, a guardrail, a stop sequence or an unreadable reason is not a
            # turn she chose to end in prose, and is never continued.
            if follow_up is not None and stop_reason == "end_turn":
                remaining = factory.remaining_limits(agent, limits)
                message = follow_up(text) if remaining is not None else None
                if message:
                    followed_up = True
                    result = await factory.invoke(
                        agent, message, invocation_state={}, limits=remaining or {},
                    )
                    stop_reason = getattr(result, "stop_reason", None)
                    stop_reason = stop_reason if isinstance(stop_reason, str) else None
                    text = factory.final_text(result)
            # THE BUDGET-EXHAUSTION DRAFT. The loop hit one of R-8's caps mid-work
            # and left no prose behind, so the only thing the pipeline could
            # deliver is a canned apology. One more completion over this agent's
            # OWN accumulated context turns that into an answer.
            #
            # HERE, not in the pipeline, because `agent` is the only thing that
            # holds the full context and it does not outlive this method. The
            # pipeline sees the result as a non-empty `final_text` and delivers it
            # through the branch that already handles DEC-93's recovered prose — no
            # new delivery path, so R-4/R-6/R-18 and the audit row are untouched.
            #
            # Guarded on `text` being empty so this NEVER costs a call on a turn
            # that already has words: a cut-off that left prose is DEC-93's case
            # and needs no model. `guard.spoke` is not consulted — a turn that
            # already sent something has `final_text == ""` too, and the pipeline
            # checks `deliverer.delivered` before delivering anything from here.
            drafted = False
            if not followed_up and stop_reason in _BUDGET_STOP_REASONS and not text.strip():
                text = await factory.draft_from_exhausted_loop(
                    agent,
                    timeout_seconds=self._draft_timeout_seconds,
                    limits=DEFAULT_DRAFT_LIMITS,
                )
                drafted = bool(text.strip())
                self._metrics.inc(
                    "nora_budget_drafts_total",
                    outcome="drafted" if drafted else "empty",
                    reason=stop_reason,
                )
        finally:
            trace = extract_tag_trace(
                agent, baseline=baseline, tag_id=ctx.tag_id,
                conversation_id=ctx.message.conversation_id, transcript=(),
                outcome="send_reply", max_chars=self._max_trace_chars,
                metrics=self._metrics,
            )
            # `TagTrace` is frozen (`nora/domain/tag_trace.py`) — `dataclasses.
            # replace` produces a corrected instance. Stamped here because this is
            # the earliest point the real clock is knowable and the trace must
            # carry it before going anywhere persistent.
            trace = dataclasses.replace(
                trace, created_at=int(time.time() * 1000), started_at=started_at,
                platform=ctx.message.platform,
                requirements=recording.to_json(),
            )
            retain_exchange(trace)
        if drafted:
            log.info(
                "drafted a reply from an exhausted loop (tag=%s, reason=%s, %d chars)",
                ctx.tag_id, stop_reason, len(text),
            )
        return (
            stop_reason,
            text,
            trace,
        )
