"""`DreamerAgentRunner`: the production `CognitionAgentRunner`, a DELEGATE. A
pass's one model call goes through `DreamerRuntime.run_agent`, which creates and
invokes the agent. What this class still owns is only what is genuinely
pass-shaped: the `CognitionAgentRunner` signature, the guard's vocabulary (the
declared argument names and the result-size caps; a pass holds no per-tool call
ceiling), the `SESSION_TOKENS` spend handoff and the trace annotations a
pass's outcome is read by. A `ToolSurface` hand is unwrapped here: its resident
tools are what the agent holds, its hooks (the shelf loader) register on the
agent, and a shelved set earns the loop one extra turn for the `load_tools` call
itself.

The model is resolved by the runtime's `ModelSource`, built by the composition
root from `[dreamer]`'s model alias; no model id or route string is written here.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from nora.agent.dream_guard import DreamGuard
from nora.agent.dreamer import (
    AdaptorBudget,
    DreamerRuntime,
    as_strands_tools,
)
from nora.agent.surface import ToolSurface
from nora.app.agent_runner import DEFAULT_MAX_TRACE_CHARS
from nora.domain.stimuli import LIMIT_STOP_REASONS
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, span

log = logging.getLogger("nora.app.cognition_agent_runner")

__all__ = [
    "SESSION_TOKENS", "CognitionAgentRunner", "CognitionRunResult", "DreamerAgentRunner",
    "TokenSpend", "as_strands_tools",
]


@dataclass(frozen=True)
class CognitionRunResult:
    """What one model call returned. `stop_reason` is Strands' own."""

    stop_reason: str | None = None
    #: Tokens the whole loop spent; `None` when the runner could not read it, never a zero.
    total_tokens: int | None = None
    messages: tuple[dict[str, Any], ...] = ()
    requirements: dict[str, Any] = field(default_factory=dict)


class CognitionAgentRunner(Protocol):
    """The test seam: a constructor argument, never an `_fn` hook. `DreamerAgentRunner` is
    the production implementation. It RAISES on failure: a model loop that errors or runs
    out its clock must not read as a pass that returned."""

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult: ...


@dataclass
class TokenSpend:
    """The tokens one pass's loop spent, as far as the runner could read them, left even
    when the loop failed or timed out. `None`: unread, never a zero."""

    total: int | None = None
    messages: tuple[dict[str, Any], ...] = ()
    requirements: dict[str, Any] = field(default_factory=dict)


#: The pass's `TokenSpend`, set around the model loop. A context variable and not a
#: parameter of `CognitionAgentRunner.run`, so every runner double still satisfies the
#: Protocol; the holder is shared by reference.
SESSION_TOKENS: contextvars.ContextVar[TokenSpend | None] = contextvars.ContextVar(
    "pass_tokens", default=None,
)


class DreamerAgentRunner:
    """Satisfies `CognitionAgentRunner` by delegating to `DreamerRuntime.run_agent`, the
    one model-invocation route."""

    def __init__(
        self, *, runtime: DreamerRuntime, metrics: Metrics | None = None,
        agent_name: str = "cognition",
        param_names: Mapping[str, frozenset[str]] | None = None,
        result_caps: Mapping[str, int] | None = None,
        bound_tool_arguments: bool = False,
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
        self._bound_tool_arguments = bound_tool_arguments
        self._result_caps = dict(result_caps or {})

    async def run(
        self,
        *,
        system_prompt: str,
        prompt: str,
        tools: list[Any],
        budget: AdaptorBudget,
        trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        tool_names = [getattr(t, "tool_name", None) or t.__name__ for t in tools]
        surface = tools if isinstance(tools, ToolSurface) else None
        strands_tools = (
            list(surface.tools) if surface is not None else as_strands_tools(tools)
        )
        param_names = self._param_names
        if self._bound_tool_arguments:
            param_names = {
                tool.tool_name: frozenset(
                    tool.tool_spec["inputSchema"]["json"].get("properties", {}),
                )
                for tool in strands_tools
            }
        guard = self._guard(tool_names, param_names)
        with span(
            "dream:cognition",
            **{TRACE_KIND: "generation", "nora.dreamer.adaptor": "cognition",
               **{f"nora.{k}": v for k, v in trace_attributes.items()}},
        ):
            try:
                result = await self._runtime.run_agent(
                    system_prompt=system_prompt, prompt=prompt,
                    strands_tools=strands_tools, guard=guard,
                    budget=AdaptorBudget(
                        turns=budget.turns
                        + int(surface is not None and bool(surface.shelf)),
                        total_tokens=budget.total_tokens,
                        output_tokens=budget.output_tokens,
                        timeout_seconds=budget.timeout_seconds,
                    ),
                    trace_attributes=trace_attributes,
                    agent_name=self._agent_name,
                    spend=SESSION_TOKENS.get(),
                    max_trace_chars=DEFAULT_MAX_TRACE_CHARS,
                    tool_hooks=surface.hooks if surface is not None else (),
                )
            except TimeoutError:
                annotate(**{"nora.outcome": "timeout"})
                self._metrics.inc("nora_errors_total", stage="cognition_pass")
                raise
            except Exception:
                annotate(**{"nora.outcome": "failed"})
                self._metrics.inc("nora_errors_total", stage="cognition_pass")
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

    def _guard(
        self, tool_names: list[str], param_names: Mapping[str, frozenset[str]] | None,
    ) -> DreamGuard:
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
            param_names=param_names,
            per_tool_ceilings=False,
        )
