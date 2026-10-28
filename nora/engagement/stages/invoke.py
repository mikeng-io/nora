"""Stage: invoke — one bounded LLM invocation attempt (R-5, R-8).

Timeout and exception behaviour:

- `asyncio.wait_for(..., timeout=timeout_seconds)`; on `TimeoutError` → log +
  `nora_silent_failures_total{reason="invocation_timeout"}` + return silently.
- On `PartialReplyError` (a `max_tokens` cut-off carrying prose) → NOT silent:
  `stop_reason="max_tokens"`, `final_text=partial_text`, caller delivers.
- On any other exception → log + `nora_silent_failures_total{reason="llm_error"}`
  + return silently.
- `finally` → always `metrics.observe("nora_llm_latency_seconds", elapsed)`.

Every series this stage moves is labelled with the `audience` keyword `run`
takes — required, no default, all four of them: both
`nora_silent_failures_total` increments, the latency histogram and
`nora_llm_stop_reason_total`. The tag path passes "tag"; the participation
occasion passes "participation". See `run`'s
docstring.

Both reasons are R-5 cases with named tests in
`tests/test_acceptance_rules.py::RULE_TESTS` — the strings must not change.

Faking the model means injecting a fake `AgentRunner` here, never an
`_invoke_fn` hook. The real `AgentRunner` wraps `nora.agent.factory`'s already-built AsyncOpenAI
client and `OpenAIModel` provider (built once, at process start, never per
tag) together with `factory.create_agent`/`factory.invoke`; this stage never
imports `nora.agent.factory` itself.

`trace_attributes` is computed here, from `ctx`/`doctrine`, via
`nora.obs.tracing.trace_attributes` and handed to the runner rather than left for it to recompute.


Returns `(ok, elapsed, stop_reason, final_text, trace)` rather than just `ok`:
`elapsed` is the single measurement both recorded as `nora_llm_latency_seconds`
here AND reused by `nora.engagement.pipeline` for the `nora_overhead_seconds`
computation — one clock read, not two independent ones that could drift apart —
and `stop_reason` is how the agent LOOP ended (R-23), which no amount of
`ok` can express: a turn that Strands cut off on `limit_total_tokens` returns
normally with `ok=True` and no reply, which is exactly what a model declining
to call `send_reply` also looks like from here. The distinction is made at
`nora.engagement.stages.deliver.check_outcome`, which needs this value.

`trace` is a `TagTrace`: the reasoning-continuity record of
this invocation, `agent.messages` sliced/sanitised/capped by the `AgentRunner`
itself (`nora.agent.tag_trace_extract.extract_tag_trace`) with `transcript`
filled in HERE from `ctx.transcript` — the runner has no access to the
assembled window, only to `agent`. On the `ok=False` paths (timeout/llm_error)
`runner.invoke` never returned, so there is no `agent` to extract from: this
function builds a blank trace instead (`messages=()`, `outcome` mapped from
which exception fired) with `transcript` filled in the same way, so those
turns debrief from `transcript` alone with `messages` empty.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from nora.domain.errors import PartialReplyError
from nora.domain.models import TagContext
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_deadline import turn_deadline
from nora.domain.turn_record import TurnCapture, capture_turn
from nora.obs.tracing import trace_attributes as build_trace_attributes

log = logging.getLogger("nora.engagement.invoke")


class AgentRunner(Protocol):
    """The narrow shape this stage needs: build one agent and run one turn."""

    async def invoke(
        self,
        *,
        doctrine: Any,
        tools: list[Any],
        pre_fill: list[dict[str, Any]],
        guard: Any,
        tool_call_log: Any = None,
        reasoning_watch: Any = None,
        tool_hooks: tuple[Any, ...] = (),
        tag_message: str,
        limits: dict[str, Any],
        ctx: TagContext,
        trace_attributes: dict[str, Any],
        follow_up: Callable[[str], str | None] | None = None,
    ) -> tuple[str | None, str, TagTrace]: ...


def _blank_trace(*, ctx: TagContext, outcome: str) -> TagTrace:
    """A trace for a tag whose invocation never returned — no `agent` survived
    to extract from. `transcript` is still real: it comes from `ctx`,
    not from the dead invocation."""
    return TagTrace(
        tag_id=ctx.tag_id, conversation_id=ctx.message.conversation_id,
        messages=(), transcript=ctx.transcript, outcome=outcome,
        created_at=int(time.time() * 1000),
    )


async def run(
    *,
    runner: AgentRunner,
    doctrine: Any,
    tools: list[Any],
    pre_fill: list[dict[str, Any]],
    guard: Any,
    tool_call_log: Any = None,
    reasoning_watch: Any = None,
    tool_hooks: tuple[Any, ...] = (),
    tag_message: str,
    limits: dict[str, Any],
    ctx: TagContext,
    timeout_seconds: float,
    metrics: Any,
    audience: str,
    follow_up: Callable[[str], str | None] | None = None,
) -> tuple[bool, float, str | None, str, TagTrace]:
    """Return `(ok, elapsed, stop_reason, final_text, trace)`. `ok` is False on a timeout or any
    other exception, in which case the model was never allowed to reply — the
    caller must not proceed to delivery.

    `stop_reason` is how the agent LOOP ended and is `None` exactly when the
    invocation did not complete (both `except` branches below), because there
    is no loop outcome to report when the loop was abandoned — those two cases
    already name their own cause. When it is not None it is counted here as
    `nora_llm_stop_reason_total{reason}` and handed back for
    `nora.engagement.stages.deliver.check_outcome` to label a silent turn
    with (R-23): `limit_total_tokens` and `no_send_call` are different
    failures and are counted apart.

    **Every series below is labelled with the `audience` keyword, a REQUIRED
    argument with no default**. A default would compile
    fine and an omitted one would record `audience=""` (`Metrics._full_labels`),
    which the A8 runbook filter `audience="tag"` then excludes — silently. The
    tag path passes "tag"; the participation occasion passes "participation".
    Both `nora_silent_failures_total` increments,
    `nora_llm_latency_seconds` and `nora_llm_stop_reason_total` —
    the last two because a fire turn is bounded by a DIFFERENT timeout and a
    mixed histogram describes neither population. The heartbeat's fire path
    deliberately does NOT call it: it needs `[heartbeat] timeout_seconds`
    rather than the tier's, and it needs the failure mapped to one of the
    reminder outcome strings so
    the reminder row records what actually happened. It calls `runner.invoke`
    inside its own `asyncio.wait_for` and counts its own two failures there.

    `follow_up` is handed to the runner untouched (`LiveAgentRunner.invoke`
    says what it does): one continuation of the SAME conversation, inside the
    SAME `wait_for` below, so it can never outlive this turn's timeout. Only
    the public summons passes one; every other caller leaves it None.
    """
    started = time.perf_counter()
    ok = True
    stop_reason: str | None = None
    # The model's FINAL prose, when it ended a turn without calling the tool.
    # Empty on every healthy turn, because a turn that called `send_reply` has
    # said what it had to say through the tool. See `pipeline`'s recovery.
    final_text = ""
    trace: TagTrace
    captured = TurnCapture()
    try:
        # The SAME number the `wait_for` enforces, published to every tool
        # the turn runs, so a tool that spends wall clock on its own model
        # call (the vision describer) can see how much of it is left.
        with turn_deadline(timeout_seconds), capture_turn() as captured:
            stop_reason, final_text, trace = await asyncio.wait_for(
                runner.invoke(
                    doctrine=doctrine, tools=tools, pre_fill=pre_fill, guard=guard,
                    tool_call_log=tool_call_log, reasoning_watch=reasoning_watch,
                    tool_hooks=tool_hooks,
                    tag_message=tag_message, limits=limits, ctx=ctx,
                    trace_attributes=build_trace_attributes(ctx, doctrine),
                    follow_up=follow_up,
                ),
                timeout=timeout_seconds,
            )
        # The runner has no access to the assembled transcript window (only
        # to `agent`), so it always hands back `transcript=()` — filled in
        # here, the one place that holds both the trace and `ctx`.
        trace = dataclasses.replace(trace, transcript=ctx.transcript)
    except TimeoutError:
        log.error("invocation_timeout (tag=%s)", ctx.tag_id)
        metrics.inc(
            "nora_silent_failures_total", reason="invocation_timeout",
            platform=ctx.message.platform, audience=audience,
        )
        ok = False
        trace = _blank_trace(ctx=ctx, outcome="timeout")
    except PartialReplyError as exc:
        # The model was cut off at `max_tokens` AFTER writing real prose
        # (`nora.agent.factory.invoke` catches Strands' exception and carries
        # the words up as this). NOT a silent failure: the words are
        # recoverable and the pipeline delivers them. `stop_reason` is set to
        # "max_tokens" so the pipeline can distinguish this from a generic
        # `llm_error` — the same reason R-23 threads stop reasons at all.
        log.warning(
            "max_tokens with partial reply (tag=%s, %d chars)",
            ctx.tag_id, len(exc.partial_text),
        )
        ok = False
        stop_reason = "max_tokens"
        final_text = exc.partial_text
        trace = _blank_trace(ctx=ctx, outcome="llm_error")
    except Exception:
        log.exception("llm_error (tag=%s); staying silent", ctx.tag_id)
        metrics.inc(
            "nora_silent_failures_total", reason="llm_error",
            platform=ctx.message.platform, audience=audience,
        )
        ok = False
        trace = _blank_trace(ctx=ctx, outcome="llm_error")
    finally:
        elapsed = time.perf_counter() - started
        metrics.observe("nora_llm_latency_seconds", elapsed, audience=audience)
    # AFTER the try/finally, so a turn that timed out or raised still writes
    # what its loop had already done — those are exactly the turns whose model
    # calls are least visible from anywhere else. NOT inside the `finally`:
    # awaiting there on a cancelled task re-raises `CancelledError` out of the
    # cleanup path, and a record of something that has already happened must
    # never be the reason a turn ends differently. A turn cancelled outright
    # therefore writes nothing, which is stated rather than hidden.
    #
    # BOUNDED INSIDE `flush` (`ReasoningStripWatch.DEFAULT_WRITE_TIMEOUT_SECONDS`),
    # and that is not optional here: this runs with the conversation's slot
    # held, `release_and_dispatch` cannot run until it returns, and a wedged
    # Postgres does not raise — it does not answer, which no `except` can see.
    # `fire.py`'s `standing_store_timeout_seconds` states the rule and
    # `tests/architecture/test_slot_held_awaits_are_bounded.py` is what keeps
    # the `wait_for` inside `flush`.
    #
    # `flush` is idempotent, swallows its own exceptions and no-ops when the
    # loop never went multi-turn (`nora.agent.reasoning_strip`), so this line
    # is safe on every path through the stage.
    if reasoning_watch is not None:
        await reasoning_watch.flush()
    if stop_reason is not None:
        # Every completed invocation, not just the ones that ran out of
        # budget: a series that only ever carries `limit_*` cannot say what
        # fraction of turns are being cut off, and `end_turn` being the
        # overwhelming majority is the thing that makes a `limit_*` legible.
        metrics.inc("nora_llm_stop_reason_total", reason=stop_reason, audience=audience)
    if captured.trace is not None:
        trace = dataclasses.replace(
            captured.trace, outcome=trace.outcome, transcript=ctx.transcript,
        )
    return ok, elapsed, stop_reason, final_text, trace
