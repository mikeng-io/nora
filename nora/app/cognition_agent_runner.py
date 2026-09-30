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
from nora.agent.ports import ToolSpec, adapt
from nora.app.cognition_session import AdaptorBudget, CognitionRunResult
from nora.domain.turn_deadline import turn_deadline
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, span

log = logging.getLogger("nora.app.cognition_agent_runner")

#: One name for every tool this adaptor may hold, budgeted generously and
#: uniformly (`DreamGuard`'s per-tool ledger needs a key for anything it is
#: asked to admit; a tool absent here is refused outright). Sized as a
#: STARTING guess like every other number in this migration — a shadow
#: week's `nora_dream_guard_vetoes_total{adaptor="cognition"}` is what would
#: size it for real.
DEFAULT_TOOL_BUDGET = 20


def as_strands_tools(tools: list[Any]) -> list[Any]:
    """The session's tool list as Strands tool objects.

    The session's own verbs are plain typed closures (`nora.app.
    cognition_session`); `Agent(tools=...)` accepts only `AgentTool` objects,
    so each closure goes through `nora.agent.ports.adapt`, the one place a
    `ToolSpec` becomes a Strands tool, exactly as every registry tool does.
    `adapt` derives the model-facing schema from the closure's own signature,
    annotations and docstring (ADR 0015), which is why those must be real.
    A tool that is already a Strands tool object (a read tool from the
    registry) passes through untouched."""
    out: list[Any] = []
    for tool in tools:
        if hasattr(tool, "tool_spec") and hasattr(tool, "tool_name"):
            out.append(tool)
            continue
        out.append(adapt(ToolSpec(
            name=tool.__name__, description="", schema={}, call=tool,
            budget=DEFAULT_TOOL_BUDGET,
        )))
    return out


class LiveCognitionAgentRunner:
    """Satisfies `nora.app.cognition_session.CognitionAgentRunner`."""

    def __init__(
        self, *, models: Any, retry: factory.RetryPolicy, metrics: Metrics | None = None,
        agent_name: str = "cognition", tool_budget: int = DEFAULT_TOOL_BUDGET,
        read_tool_budgets: Mapping[str, int] | None = None,
        param_names: Mapping[str, frozenset[str]] | None = None,
        result_caps: Mapping[str, int] | None = None,
        memory_tools: frozenset[str] = frozenset(),
    ) -> None:
        """`read_tool_budgets`: the per-tool call ceiling of each READ tool
        (the registry's own table with the deployment's and this adaptor's
        overrides applied, `nora.app.cognition_wiring`); a tool absent from it
        (the session's own verbs) runs on `tool_budget`. `param_names`,
        `result_caps` and `memory_tools` are the three ceilings every other
        Dreamer sample builds its guard with (R-99, the result size cap, the
        per-pass memory ceiling); left out, the guard holds none of them."""
        self._models = models
        self._retry = retry
        self._metrics = metrics if metrics is not None else Metrics()
        self._agent_name = agent_name
        self._tool_budget = tool_budget
        self._read_tool_budgets = dict(read_tool_budgets or {})
        self._param_names = param_names
        self._result_caps = dict(result_caps or {})
        self._memory_tools = memory_tools

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        tool_names = [getattr(t, "tool_name", None) or t.__name__ for t in tools]
        tools = as_strands_tools(tools)
        guard = DreamGuard(
            adaptor="cognition",
            budgets={n: self._read_tool_budgets.get(n, self._tool_budget) for n in tool_names},
            metrics=self._metrics,
            result_caps=self._result_caps,
            memory_tools=self._memory_tools,
            memory_budget=budget.max_memory_calls,
            pass_budget=budget.max_tool_calls,
            param_names=self._param_names,
        )
        # ONE root span per session, its stages its children (AGENTS.md,
        # instruments): `nora.trace_kind = generation` is what keeps the trace
        # in Langfuse, exactly as `DreamerRuntime.run` declares it for every
        # other adaptor, and `nora.outcome` is the reading a session's fate
        # needs without opening the model call.
        with span(
            "dream:cognition",
            **{TRACE_KIND: "generation", "nora.dreamer.adaptor": "cognition",
               **{f"nora.{k}": v for k, v in trace_attributes.items()}},
        ):
            try:
                result = await self._invoke(
                    system_prompt=system_prompt, prompt=prompt, tools=tools, guard=guard,
                    budget=budget, trace_attributes=trace_attributes,
                )
            except TimeoutError:
                annotate(**{"nora.outcome": "timeout"})
                raise
            except Exception:
                annotate(**{"nora.outcome": "failed"})
                raise
            annotate(**{"nora.outcome": "ok"})
        return CognitionRunResult(stop_reason=getattr(result, "stop_reason", None))

    async def _invoke(
        self, *, system_prompt: str, prompt: str, tools: list[Any], guard: DreamGuard,
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> Any:
        agent = factory.create_dream_agent(
            model=self._models.create(), system_prompt=system_prompt, tools=tools,
            guard=guard, trace_attributes=dict(trace_attributes), retry=self._retry,
            agent_name=self._agent_name,
        )
        # A failure or a timeout is RAISED, never returned as a normal result:
        # `CognitionSessionRunner` records the session `failed`/`timeout`
        # (which leaves its stimuli pending), and only a loop that RETURNED
        # closes its items. Swallowing either here made a dead session read as
        # one that ran: items closed `waiting` with a strike, the watermark
        # moved past stimuli nobody looked at.
        try:
            with turn_deadline(budget.timeout_seconds):
                return await asyncio.wait_for(
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
            raise
        except Exception:
            log.exception("cognition session failed")
            self._metrics.inc("nora_errors_total", stage="cognition_session")
            raise
