"""`LiveCognitionAgentRunner` (review of #627): a failure or a timeout is
RAISED, never swallowed into a normal `CognitionRunResult`.

`CognitionSessionRunner` records a session `failed`/`timeout` only when the
model loop raises; a runner that returned `stop_reason=None` on an exception
made a dead session read as one that ran, closed its items `waiting` with a
strike, and moved the watermark past stimuli nobody looked at. Strands is not
run: `factory.create_dream_agent` and `factory.invoke` are replaced, which is
the seam the runner reaches the SDK through.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nora.agent import factory
from nora.app.cognition_agent_runner import LiveCognitionAgentRunner, as_strands_tools
from nora.app.cognition_session import AdaptorBudget
from nora.obs.metrics import Metrics


class _Models:
    def create(self) -> object:
        return object()


def _runner(metrics: Metrics) -> LiveCognitionAgentRunner:
    return LiveCognitionAgentRunner(
        models=_Models(), retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=metrics,
    )


async def _tool(x: str) -> dict[str, object]:
    """Do a thing.

    Args:
        x: The thing.
    """
    return {"status": "ok"}


async def _run(runner: LiveCognitionAgentRunner, *, budget_seconds: float = 5.0) -> Any:
    return await runner.run(
        system_prompt="s", prompt="p", tools=[_tool],
        budget=AdaptorBudget(
            turns=1, total_tokens=1, output_tokens=1, timeout_seconds=budget_seconds,
        ),
        trace_attributes={},
    )


def _patch(monkeypatch: pytest.MonkeyPatch, invoke: Any) -> list[list[Any]]:
    handed: list[list[Any]] = []

    def create_dream_agent(**kwargs: Any) -> object:
        handed.append(kwargs["tools"])
        return object()

    monkeypatch.setattr(factory, "create_dream_agent", create_dream_agent)
    monkeypatch.setattr(factory, "invoke", invoke)
    return handed


async def test_a_model_failure_is_raised_and_counted_not_returned(monkeypatch):
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the provider fell over")

    _patch(monkeypatch, invoke)
    metrics = Metrics()
    with pytest.raises(RuntimeError, match="fell over"):
        await _run(_runner(metrics))
    assert metrics.get("nora_errors_total", stage="cognition_session") == 1


async def test_a_timeout_is_raised_not_returned_as_a_stop_reason(monkeypatch):
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(30)

    _patch(monkeypatch, invoke)
    with pytest.raises(TimeoutError):
        await _run(_runner(Metrics()), budget_seconds=0.05)


async def test_a_loop_that_returns_hands_its_stop_reason_back_and_gets_strands_tools(monkeypatch):
    class Result:
        stop_reason = "end_turn"

    async def invoke(*args: Any, **kwargs: Any) -> Any:
        return Result()

    handed = _patch(monkeypatch, invoke)
    result = await _run(_runner(Metrics()))
    assert result.stop_reason == "end_turn"
    # A plain closure is not a Strands tool; the runner adapts it (ADR 0015).
    (tool,) = handed[0]
    assert tool.tool_name == "_tool"
    assert tool.tool_spec["inputSchema"]["json"]["properties"]["x"]["type"] == "string"


def test_a_tool_that_is_already_a_strands_tool_passes_through_untouched():
    (adapted,) = as_strands_tools([_tool])
    assert as_strands_tools([adapted]) == [adapted]
