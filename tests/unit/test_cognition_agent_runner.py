"""`LiveCognitionAgentRunner`: a failure or a timeout is
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


# -- tokens: the loop's own accounting, or nothing -----------------------------------------

class _Result:
    def __init__(self, usage: Any, stop_reason: str = "end_turn") -> None:
        self.stop_reason = stop_reason
        self.metrics = type("M", (), {"accumulated_usage": usage})()


async def test_a_run_hands_up_the_tokens_the_loop_spent(monkeypatch):
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        return _Result({"inputTokens": 900, "outputTokens": 100, "totalTokens": 1000})

    _patch(monkeypatch, invoke)
    result = await _run(_runner(Metrics()))
    assert (result.stop_reason, result.total_tokens) == ("end_turn", 1000)


@pytest.mark.parametrize("usage", [None, {}, {"totalTokens": None}, {"totalTokens": "9"},
                                   {"totalTokens": True},
                                   # a provider that reports no usage: Strands accumulates zeros,
                                   # and a loop that made a call did not spend nothing
                                   {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}])
async def test_a_result_with_no_readable_usage_is_none_never_zero(monkeypatch, usage):
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        return _Result(usage)

    _patch(monkeypatch, invoke)
    assert (await _run(_runner(Metrics()))).total_tokens is None


# -- the tokens a loop spent when it did NOT return --------------------------------------

class _Agent:
    def __init__(self, usage: Any) -> None:
        self.event_loop_metrics = type("M", (), {"accumulated_usage": usage})()


def _spend_patch(monkeypatch: pytest.MonkeyPatch, usage: Any, invoke: Any) -> None:
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: _Agent(usage))
    monkeypatch.setattr(factory, "invoke", invoke)


def _raises(exc: BaseException) -> Any:
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        raise exc
    return invoke


async def _spent_by(runner: LiveCognitionAgentRunner) -> tuple[Any, Any]:
    from nora.app.cognition_session import SESSION_TOKENS, TokenSpend
    spend = TokenSpend()
    token = SESSION_TOKENS.set(spend)
    try:
        try:
            result = await _run(runner)
        except BaseException as exc:  # noqa: BLE001 - the point is what was left behind
            return spend.total, exc
        return spend.total, result
    finally:
        SESSION_TOKENS.reset(token)


@pytest.mark.parametrize("exc", [
    RuntimeError("the provider fell over"), TimeoutError(),
    factory.PartialReplyError("half a reply"),
])
async def test_a_loop_that_failed_timed_out_or_was_cut_off_leaves_what_it_spent(
    monkeypatch, exc,
):
    usage = {"inputTokens": 7000, "outputTokens": 500, "totalTokens": 7500}
    _spend_patch(monkeypatch, usage, _raises(exc))
    total, outcome = await _spent_by(_runner(Metrics()))
    assert total == 7500 and isinstance(outcome, type(exc))      # raised, not swallowed


async def test_a_loop_that_failed_with_no_usage_leaves_none_and_a_broken_reading_never_raises(
    monkeypatch,
):
    _spend_patch(monkeypatch, {"totalTokens": 0}, _raises(RuntimeError("down")))
    total, outcome = await _spent_by(_runner(Metrics()))
    assert total is None and isinstance(outcome, RuntimeError)

    class Broken:
        @property
        def event_loop_metrics(self) -> Any:
            raise RuntimeError("no metrics")

    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: Broken())
    monkeypatch.setattr(factory, "invoke", _raises(RuntimeError("down")))
    total, outcome = await _spent_by(_runner(Metrics()))
    assert total is None and str(outcome) == "down"


async def test_a_run_outside_a_session_leaves_nothing_anywhere(monkeypatch):
    _spend_patch(monkeypatch, {"totalTokens": 5}, _raises(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        await _run(_runner(Metrics()))
