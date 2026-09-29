"""`LiveCognitionAgentRunner` — the production `nora.app.cognition_session.
CognitionAgentRunner` (migration step 3a-ii), built the same way `nora.app.
agent_runner.LiveAgentRunner` wraps `nora.agent.factory`'s Strands plumbing
for a tag turn, and the same way `nora.agent.dreamer.DreamerRuntime._sample`
wraps it for every OTHER Dreamer adaptor: one `DreamGuard` per call (a
ledger is spent state — a shared one would let an earlier session's tool
calls count against a later session's budget, `_sample`'s own reason),
`factory.create_dream_agent` (the ONLY constructor in the tree allowed to
build a Strands `Agent` for the Dreamer — spec C.7), `factory.invoke` under
`budget`'s three R-8 caps.

**The model is resolved from the existing config/alias mechanism**
(`nora.agent.factory.ModelSource`, built by the composition root from
`[cognition]`'s own model alias exactly as `[dreamer]`'s is) — never a
second model-selection path. No model id or route string is written into
this file: `nora.app.cognition_session.CognitionSessionRunner` is handed
`model_id`/`model_route` as plain config-resolved strings by the
composition root, for the session row's own provenance column
(30-cognition.md §1), and this class never reads or repeats them.

**Simplification, stated rather than hidden.** `ReasoningStripWatch`
(`nora.agent.reasoning_strip`), which every OTHER Dreamer sample attaches,
is not wired here: it exists to catch a `<think>` block leaking into a
DELIVERED reply, and nothing this session writes is ever delivered — every
write is a dry-run row (`30-cognition.md` §2, this migration step). Adding
it is a clean follow-up once step 4 makes a write live and the leak
actually reaches somewhere a person could read it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from nora.agent import factory
from nora.agent.dream_guard import DreamGuard
from nora.app.cognition_session import AdaptorBudget, CognitionRunResult
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.cognition_agent_runner")

#: One name for every tool this adaptor may hold, budgeted generously and
#: uniformly (`DreamGuard`'s per-tool ledger needs a key for anything it is
#: asked to admit; a tool absent here is refused outright). Sized as a
#: STARTING guess like every other number in this migration — a shadow
#: week's `nora_dream_guard_vetoes_total{adaptor="cognition"}` is what would
#: size it for real.
DEFAULT_TOOL_BUDGET = 20


class LiveCognitionAgentRunner:
    """Satisfies `nora.app.cognition_session.CognitionAgentRunner`."""

    def __init__(
        self, *, models: Any, retry: factory.RetryPolicy, metrics: Metrics | None = None,
        agent_name: str = "cognition", tool_budget: int = DEFAULT_TOOL_BUDGET,
    ) -> None:
        self._models = models
        self._retry = retry
        self._metrics = metrics if metrics is not None else Metrics()
        self._agent_name = agent_name
        self._tool_budget = tool_budget

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        tool_names = [getattr(t, "__name__", str(t)) for t in tools]
        guard = DreamGuard(
            adaptor="cognition",
            budgets=dict.fromkeys(tool_names, self._tool_budget),
            metrics=self._metrics,
        )
        agent = factory.create_dream_agent(
            model=self._models.create(), system_prompt=system_prompt, tools=tools,
            guard=guard, trace_attributes=dict(trace_attributes), retry=self._retry,
            agent_name=self._agent_name,
        )
        try:
            result = await asyncio.wait_for(
                factory.invoke(
                    agent, prompt, invocation_state={},
                    limits=factory.build_limits(
                        output_tokens=budget.output_tokens, turns=budget.turns,
                        total_tokens=budget.total_tokens,
                    ),
                ),
                timeout=budget.timeout_seconds,
            )
        except TimeoutError:
            log.error("cognition session timed out after %.0fs", budget.timeout_seconds)
            self._metrics.inc("nora_errors_total", stage="cognition_session")
            return CognitionRunResult(stop_reason="timeout")
        except Exception:
            log.exception("cognition session failed")
            self._metrics.inc("nora_errors_total", stage="cognition_session")
            return CognitionRunResult(stop_reason=None)
        stop_reason = getattr(result, "stop_reason", None)
        return CognitionRunResult(stop_reason=stop_reason)
