"""Stage: invoke — one bounded LLM invocation attempt (spec C.7, R-5, R-8).

Preserves `Handler._invoke`'s timeout/exception behaviour exactly
(`nora/handler.py:187-203`):

- `asyncio.wait_for(..., timeout=timeout_seconds)`; on `TimeoutError` → log +
  `nora_silent_failures_total{reason="invocation_timeout"}` + return silently.
- On any other exception → log + `nora_silent_failures_total{reason="llm_error"}`
  + return silently.
- `finally` → always `metrics.observe("nora_llm_latency_seconds", elapsed)`.

Every series this stage moves is labelled `audience="tag"` as a literal, all
four of them: both `nora_silent_failures_total` increments, the latency
histogram and `nora_llm_stop_reason_total`. This stage IS the tag path and the
fire turn never enters it — see `run`'s docstring.

Both reasons are R-5 cases with named tests in
`tests/test_acceptance_rules.py::RULE_TESTS` — the strings must not change.

Per spec §10's test-seam policy, faking the model means injecting a fake
`AgentRunner` here — never an `_invoke_fn` hook. The real `AgentRunner` (Task
8's composition root) wraps `nora.agent.factory`'s already-built AsyncOpenAI
client and `OpenAIModel` provider (built once, at process start, never per
tag) together with `factory.create_agent`/`factory.invoke`; this stage never
imports `nora.agent.factory` itself.

`trace_attributes` is computed here, from `ctx`/`doctrine`, via
`nora.obs.tracing.trace_attributes` — the same call `Handler._invoke` makes
immediately before `factory.create_agent` (`nora/handler.py:133`) — and
handed to the runner rather than left for it to recompute.


Returns `(ok, elapsed, stop_reason)` rather than just `ok`: `elapsed` is the
single measurement both recorded as `nora_llm_latency_seconds` here AND reused
by `nora.engagement.pipeline` for the `nora_overhead_seconds` computation —
one clock read, not two independent ones that could drift apart —
and `stop_reason` is how the agent LOOP ended (R-23), which no amount of
`ok` can express: a turn that Strands cut off on `limit_total_tokens` returns
normally with `ok=True` and no reply, which is exactly what a model declining
to call `send_reply` also looks like from here. The distinction is made at
`nora.engagement.stages.deliver.check_outcome`, which needs this value.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from nora.domain.models import TagContext
from nora.obs.tracing import trace_attributes as build_trace_attributes

log = logging.getLogger("nora.engagement.invoke")


class AgentRunner(Protocol):
    """The narrow shape this stage needs: build one agent and run one turn."""

    async def invoke(
        self,
        *,
        doctrine: Any,
        tools: list,
        pre_fill: list[dict],
        guard: Any,
        tag_message: str,
        limits: dict,
        ctx: TagContext,
        trace_attributes: dict,
    ) -> str | None: ...


async def run(
    *,
    runner: AgentRunner,
    doctrine: Any,
    tools: list,
    pre_fill: list[dict],
    guard: Any,
    tag_message: str,
    limits: dict,
    ctx: TagContext,
    timeout_seconds: float,
    metrics: Any,
) -> tuple[bool, float, str | None]:
    """Return `(ok, elapsed, stop_reason)`. `ok` is False on a timeout or any
    other exception, in which case the model was never allowed to reply — the
    caller must not proceed to delivery.

    `stop_reason` is how the agent LOOP ended and is `None` exactly when the
    invocation did not complete (both `except` branches below), because there
    is no loop outcome to report when the loop was abandoned — those two cases
    already name their own cause. When it is not None it is counted here as
    `nora_llm_stop_reason_total{reason}` and handed back for
    `nora.engagement.stages.deliver.check_outcome` to label a silent turn
    with (R-23): `limit_total_tokens` and `no_send_call` are different
    failures and used to be the same metric.

    **Every series below is labelled `audience="tag"` as a literal, because
    this stage is the TAG path's, exclusively** (design spec §6.2, §13). That is
    both `nora_silent_failures_total` increments, `nora_llm_latency_seconds` and
    `nora_llm_stop_reason_total` — the last two because a fire turn is bounded
    by a DIFFERENT timeout and a mixed histogram describes neither population.
    The heartbeat's fire path deliberately
    does NOT call it: it needs `[heartbeat] timeout_seconds` rather than the
    tier's, and it needs the failure mapped to one of §8.1's outcome strings so
    the reminder row records what actually happened. It calls `runner.invoke`
    inside its own `asyncio.wait_for` and counts its own two failures there.
    """
    started = time.perf_counter()
    ok = True
    stop_reason: str | None = None
    try:
        stop_reason = await asyncio.wait_for(
            runner.invoke(
                doctrine=doctrine, tools=tools, pre_fill=pre_fill, guard=guard,
                tag_message=tag_message, limits=limits, ctx=ctx,
                trace_attributes=build_trace_attributes(ctx, doctrine),
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        log.error("invocation_timeout (tag=%s)", ctx.tag_id)
        metrics.inc(
            "nora_silent_failures_total", reason="invocation_timeout",
            platform=ctx.message.platform, audience="tag",
        )
        ok = False
    except Exception:
        log.exception("llm_error (tag=%s); staying silent", ctx.tag_id)
        metrics.inc(
            "nora_silent_failures_total", reason="llm_error",
            platform=ctx.message.platform, audience="tag",
        )
        ok = False
    finally:
        elapsed = time.perf_counter() - started
        metrics.observe("nora_llm_latency_seconds", elapsed, audience="tag")
    if stop_reason is not None:
        # Every completed invocation, not just the ones that ran out of
        # budget: a series that only ever carries `limit_*` cannot say what
        # fraction of turns are being cut off, and `end_turn` being the
        # overwhelming majority is the thing that makes a `limit_*` legible.
        metrics.inc("nora_llm_stop_reason_total", reason=stop_reason, audience="tag")
    return ok, elapsed, stop_reason
