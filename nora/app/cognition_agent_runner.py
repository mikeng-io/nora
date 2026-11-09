"""`DreamerAgentRunner` — the production `nora.app.cognition_session.
CognitionAgentRunner`, and a DELEGATE: the session's one model call goes
through `DreamerRuntime.run_agent`, the same `create_dream_agent` /
`factory.invoke` route every Dreamer adaptor's `_sample` takes (node 30).
What this class still owns is only what is genuinely session-shaped: the
`CognitionAgentRunner` signature, the guard's vocabulary (R-99's declared
argument names and the result-size caps — the session holds no per-tool call
ceiling, DEC-247), the `SESSION_TOKENS` spend handoff and the trace
annotations a session's outcome is read by.

The model is resolved by the runtime's `ModelSource` — built by the
composition root from `[dreamer]`'s model alias — never a second
model-selection path. No model id or route string is written into this file:
`CognitionSessionRunner` is handed `model_id`/`model_route` as plain
config-resolved strings for the session row's provenance column.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from nora.agent.dreamer import (
    AdaptorBudget as DreamerBudget,
    DreamerRuntime,
    as_strands_tools,
)
from nora.agent.dream_guard import DreamGuard
from nora.agent.token_reservation import TokenReservation
from nora.app.agent_runner import DEFAULT_MAX_TRACE_CHARS
from nora.app.cognition_session import SESSION_TOKENS, AdaptorBudget, CognitionRunResult
from nora.domain.stimuli import LIMIT_STOP_REASONS
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, span

log = logging.getLogger("nora.app.cognition_agent_runner")

__all__ = ["DreamerAgentRunner", "as_strands_tools"]


class DreamerAgentRunner:
    """Satisfies `nora.app.cognition_session.CognitionAgentRunner` by
    delegating to `DreamerRuntime.run_agent` — the one model-invocation
    route, so a session's model call and every other adaptor's sample have
    one owner, one deadline vocabulary and one spend handoff."""

    def __init__(
        self, *, runtime: DreamerRuntime, metrics: Metrics | None = None,
        agent_name: str = "cognition",
        param_names: Mapping[str, frozenset[str]] | None = None,
        result_caps: Mapping[str, int] | None = None,
    ) -> None:
        """`param_names` and `result_caps` are two of the ceilings the guard
        builds (R-99, the result size cap); left out, the guard holds
        neither. The per-pass memory ceiling is not built: a session has no
        per-tool, memory or pass-wide call ceiling (DEC-247), only the
        loop's rounds, tokens and clock."""
        self._runtime = runtime
        self._metrics = metrics if metrics is not None else Metrics()
        self._agent_name = agent_name
        self._param_names = param_names
        self._result_caps = dict(result_caps or {})

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        tool_names = [getattr(t, "tool_name", None) or t.__name__ for t in tools]
        guard = self._guard(tool_names)
        reservation = (
            TokenReservation(budget.total_tokens) if budget.reserve_tokens else None
        )
        # ONE root span per session (AGENTS.md, instruments): the name and
        # attributes are the ones `DreamerRuntime.run` declares for every
        # adaptor, because the session's model call is the Dreamer's work.
        with span(
            "dream:cognition",
            **{TRACE_KIND: "generation", "nora.dreamer.adaptor": "cognition",
               **{f"nora.{k}": v for k, v in trace_attributes.items()}},
        ):
            try:
                result = await self._runtime.run_agent(
                    system_prompt=system_prompt, prompt=prompt,
                    strands_tools=tools, guard=guard,
                    budget=DreamerBudget(
                        turns=budget.turns, total_tokens=budget.total_tokens,
                        output_tokens=budget.output_tokens,
                        timeout_seconds=budget.timeout_seconds,
                    ),
                    trace_attributes=trace_attributes,
                    agent_name=self._agent_name,
                    spend=SESSION_TOKENS.get(),
                    max_trace_chars=DEFAULT_MAX_TRACE_CHARS,
                    reservation=reservation,
                )
            except TimeoutError:
                annotate(**{"nora.outcome": "timeout"})
                self._metrics.inc("nora_errors_total", stage="cognition_session")
                raise
            except Exception:
                annotate(**{"nora.outcome": "failed"})
                self._metrics.inc("nora_errors_total", stage="cognition_session")
                raise
            annotate(**{"nora.outcome": (
                "failed" if result.stop_reason in LIMIT_STOP_REASONS else "ok")})
            annotate(**{"nora.cognition.stop_reason": result.stop_reason,
                        "nora.cognition.tokens": result.total_tokens,
                        "nora.cognition.usage_known": result.total_tokens is not None})
        return CognitionRunResult(
            stop_reason=result.stop_reason, total_tokens=result.total_tokens,
            messages=tuple(result.messages), requirements=dict(result.requirements),
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

