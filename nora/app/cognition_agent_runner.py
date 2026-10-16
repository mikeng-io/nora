"""`LiveCognitionAgentRunner` — the production `nora.app.cognition_session.
CognitionAgentRunner`, built the same way `nora.app.agent_runner.LiveAgentRunner`
wraps `nora.agent.factory`'s Strands plumbing for a tag turn, and the same way
`nora.agent.dreamer.DreamerRuntime._sample` wraps it for every OTHER Dreamer
adaptor: one `DreamGuard` per call (a ledger is spent state — a shared one would
let an earlier session's tool calls count against a later session's budget),
`factory.create_dream_agent` (the ONLY constructor in the tree allowed to build a
Strands `Agent` for the Dreamer — spec C.7), `factory.invoke` under `budget`'s
three R-8 caps.

**The model is resolved from the existing config/alias mechanism**
(`nora.agent.factory.ModelSource`, built by the composition root from
`[cognition]`'s own model alias exactly as `[dreamer]`'s is) — never a second
model-selection path. No model id or route string is written into this file:
`nora.app.cognition_session.CognitionSessionRunner` is handed
`model_id`/`model_route` as plain config-resolved strings by the composition
root, for the session row's own provenance column, and this class never reads or
repeats them.

**`ReasoningStripWatch`** (`nora.agent.reasoning_strip`), which every OTHER
Dreamer sample attaches, is not wired here: it exists to catch a `<think>` block
leaking into a DELIVERED reply, and nothing this session writes is delivered as
she wrote it: every write passes its tier's validator, and a reminder's text is
read back by a fresh turn that writes the words. Ceiling: a `<think>` fragment
that passed a validator would land in a record.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from nora.agent import factory
from nora.agent.dream_guard import DreamGuard
from nora.agent.ports import ToolSpec, adapt
from nora.app.cognition_session import SESSION_TOKENS, AdaptorBudget, CognitionRunResult
from nora.domain.turn_deadline import turn_deadline
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, span

log = logging.getLogger("nora.app.cognition_agent_runner")

#: The `ToolSpec.budget` a session's own verbs are adapted with. Metadata
#: only: a session's guard holds no per-tool ceiling (DEC-247), so this number
#: vetoes nothing.
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


def _spent(usage: Any) -> int | None:
    """`usage["totalTokens"]` of a Strands accounting mapping, or None when it is
    absent, not a number, or ZERO: a loop that made a model call spent something,
    so a zero is a provider that reported no usage (Strands then accumulates a
    dict of zeros), not a free session. An unread count is never a zero."""
    total = usage.get("totalTokens") if isinstance(usage, Mapping) else None
    ok = isinstance(total, int) and not isinstance(total, bool) and total > 0
    return total if ok else None


def _total_tokens(result: Any) -> int | None:
    """The loop's total tokens as Strands accumulated them over every cycle
    (`AgentResult.metrics.accumulated_usage`), or None (see `_spent`)."""
    return _spent(getattr(getattr(result, "metrics", None), "accumulated_usage", None))


def _leave_spend(agent: Any) -> None:
    """Leave what the agent has spent where the session can read it, whatever
    happened to the loop (it returned, raised, timed out or was cancelled): a
    failed session is the costliest and has no result to read it from. Best
    effort and never raising, since it runs in a `finally`."""
    spend = SESSION_TOKENS.get()
    if spend is None:
        return
    try:
        usage = getattr(getattr(agent, "event_loop_metrics", None), "accumulated_usage", None)
        spend.total = _spent(usage)
    except Exception:
        log.exception("could not read the tokens a cognition session spent")


class LiveCognitionAgentRunner:
    """Satisfies `nora.app.cognition_session.CognitionAgentRunner`."""

    def __init__(
        self, *, models: Any, retry: factory.RetryPolicy, metrics: Metrics | None = None,
        agent_name: str = "cognition",
        param_names: Mapping[str, frozenset[str]] | None = None,
        result_caps: Mapping[str, int] | None = None,
    ) -> None:
        """`param_names` and `result_caps` are two of the ceilings every other
        Dreamer sample builds its guard with (R-99, the result size cap); left
        out, the guard holds neither. The third, the per-pass memory ceiling,
        is not built: a session has no per-tool, memory or pass-wide call
        ceiling (DEC-247), only the loop's rounds, tokens and clock."""
        self._models = models
        self._retry = retry
        self._metrics = metrics if metrics is not None else Metrics()
        self._agent_name = agent_name
        self._param_names = param_names
        self._result_caps = dict(result_caps or {})

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        tool_names = [getattr(t, "tool_name", None) or t.__name__ for t in tools]
        tools = as_strands_tools(tools)
        guard = self._guard(tool_names)
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
        return CognitionRunResult(
            stop_reason=getattr(result, "stop_reason", None),
            total_tokens=_total_tokens(result),
        )

    def _guard(self, tool_names: list[str]) -> DreamGuard:
        """One `DreamGuard` for a session, with NO call ceiling of any kind
        (DEC-247): not per tool, not over the memory cluster, not over every
        verb. A session is bounded by `AdaptorBudget`'s rounds, tokens and wall
        clock alone. What the guard still does is the safety half: R-7 (a tool
        the session does not hold), R-99 (an argument a tool does not declare)
        and the result size cap."""
        return DreamGuard(
            adaptor="cognition",
            budgets=dict.fromkeys(tool_names, 0),
            metrics=self._metrics,
            result_caps=self._result_caps,
            param_names=self._param_names,
            per_tool_ceilings=False,
        )

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
        # closes its items. Swallowing either would make a dead session read as
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
        finally:
            _leave_spend(agent)
