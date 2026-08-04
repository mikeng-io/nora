"""Stage: invoke — one bounded LLM invocation attempt (spec C.7, R-5, R-8).

Preserves `Handler._invoke`'s timeout/exception behaviour exactly
(`nora/handler.py:187-203`):

- `asyncio.wait_for(..., timeout=timeout_seconds)`; on `TimeoutError` → log +
  `nora_silent_failures_total{reason="invocation_timeout"}` + return silently.
- On any other exception → log + `nora_silent_failures_total{reason="llm_error"}`
  + return silently.
- `finally` → always `metrics.observe("nora_llm_latency_seconds", elapsed)`.

Both reasons are R-5 cases with named tests in
`tests/test_acceptance_rules.py::RULE_TESTS` — the strings must not change.

Per spec §10's test-seam policy, faking the model means injecting a fake
`AgentRunner` here — never an `_invoke_fn` hook. The real `AgentRunner` (Task
8's composition root) wraps `nora.agent.factory`'s already-built AsyncOpenAI
client and `OpenAIModel` provider (built once, at process start, never per
tag) together with `factory.create_agent`/`factory.invoke`; this stage never
imports `nora.agent.factory` itself.

`trace_attributes` is computed here, from `ctx`/`persona`, via
`nora.obs.tracing.trace_attributes` — the same call `Handler._invoke` makes
immediately before `factory.create_agent` (`nora/handler.py:133`) — and
handed to the runner rather than left for it to recompute.


Returns `(ok, elapsed)` rather than just `ok`: `elapsed` is the single
measurement both recorded as `nora_llm_latency_seconds` here AND reused by
`nora.engagement.pipeline` for the `nora_overhead_seconds` computation —
one clock read, not two independent ones that could drift apart.
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
        persona: Any,
        tools: list,
        pre_fill: list[dict],
        guard: Any,
        tag_message: str,
        limits: dict,
        ctx: TagContext,
        trace_attributes: dict,
    ) -> None: ...


async def run(
    *,
    runner: AgentRunner,
    persona: Any,
    tools: list,
    pre_fill: list[dict],
    guard: Any,
    tag_message: str,
    limits: dict,
    ctx: TagContext,
    timeout_seconds: float,
    metrics: Any,
) -> tuple[bool, float]:
    """Return `(ok, elapsed)`. `ok` is False on a timeout or any other
    exception, in which case the model was never allowed to reply — the
    caller must not proceed to delivery."""
    started = time.perf_counter()
    ok = True
    try:
        await asyncio.wait_for(
            runner.invoke(
                persona=persona, tools=tools, pre_fill=pre_fill, guard=guard,
                tag_message=tag_message, limits=limits, ctx=ctx,
                trace_attributes=build_trace_attributes(ctx, persona),
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        log.error("invocation_timeout (tag=%s)", ctx.tag_id)
        metrics.inc("nora_silent_failures_total", reason="invocation_timeout")
        ok = False
    except Exception:
        log.exception("llm_error (tag=%s); staying silent", ctx.tag_id)
        metrics.inc("nora_silent_failures_total", reason="llm_error")
        ok = False
    finally:
        elapsed = time.perf_counter() - started
        metrics.observe("nora_llm_latency_seconds", elapsed)
    return ok, elapsed
