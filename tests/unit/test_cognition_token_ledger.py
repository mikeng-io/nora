"""What a Cognition session spent is a ROW, not only a counter (the Cognition
cut-over's PR-7).

`nora_cognition_unit_tokens_total` is a Prometheus counter and nora restarts 2 to 24
times a day, so every restart zeroes it and a unit-day's spend is unreadable (the
cut-over's kill line is 4M tokens per unit-day). The outcome row is the one row a
session leaves at its end, so its input, output and cache-read tokens and its root
trace id are written there, by EVERY path that ends a wake: a session that closed, one
that failed or timed out (the costliest kind, with no result to read), and one a
restart cancelled. NULL is unread, never zero.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from nora.agent import factory
from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
from nora.app.cognition_session import (
    SESSION_TOKENS,
    AdaptorBudget,
    CognitionRunResult,
    TokenSpend,
)
from nora.obs.metrics import Metrics
from tests.unit.test_cognition_session_lifecycle import ScriptedAgent, _rig, blocked


def _spend(holder: TokenSpend | None) -> None:
    assert holder is not None, "the session offers a holder around the loop"
    holder.input, holder.output, holder.cache_read = 82_000, 1_500, 67_000
    holder.trace_id = "a" * 32


async def _closes_after_spending(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    _spend(SESSION_TOKENS.get())
    return CognitionRunResult(stop_reason="end_turn", total_tokens=83_500)


async def _fails_after_spending(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    _spend(SESSION_TOKENS.get())
    raise RuntimeError("the provider fell over")


async def _runs_past_its_clock(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    _spend(SESSION_TOKENS.get())
    await asyncio.sleep(30)
    return CognitionRunResult(stop_reason="end_turn")


async def _spends_then_blocks(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    _spend(SESSION_TOKENS.get())
    return await agent.block(tools)


async def _says_nothing_of_usage(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    return CognitionRunResult(stop_reason="end_turn")


async def _one_session(behaviour: Any, *, clock_seconds: float = 30.0) -> Any:
    agent = ScriptedAgent(script=[behaviour])
    rig = _rig(agent=agent, timeout=clock_seconds)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    return rig, agent


def _only_outcome(rig: Any) -> Any:
    (outcome,) = rig.stimuli.outcomes.values()
    return outcome


def _read(outcome: Any) -> tuple[Any, ...]:
    return (outcome.input_tokens, outcome.output_tokens, outcome.cache_read_tokens,
            outcome.trace_id)


SPENT = (82_000, 1_500, 67_000, "a" * 32)


async def test_a_closed_session_records_what_it_spent_on_its_outcome():
    rig, _ = await _one_session(_closes_after_spending)
    await rig.settle()
    outcome = _only_outcome(rig)
    assert outcome.outcome == "closed" and _read(outcome) == SPENT


async def test_a_failed_session_records_what_it_spent_the_costliest_kind():
    rig, _ = await _one_session(_fails_after_spending)
    await rig.settle()
    outcome = _only_outcome(rig)
    assert (outcome.outcome, outcome.cause) == ("failed", "infra")
    assert _read(outcome) == SPENT


async def test_a_session_that_ran_past_its_clock_records_what_it_spent():
    rig, _ = await _one_session(_runs_past_its_clock, clock_seconds=0.05)
    await rig.settle()
    outcome = _only_outcome(rig)
    assert outcome.outcome == "timeout" and _read(outcome) == SPENT


async def test_a_session_a_restart_cancelled_records_what_it_spent_and_says_restart():
    from nora.domain.stimuli import is_restart_detail

    agent = ScriptedAgent(script=[_spends_then_blocks])
    rig = _rig(agent=agent)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await agent.started.wait()
    await rig.runner.shutdown()
    await rig.settle()
    outcome = _only_outcome(rig)
    assert (outcome.outcome, outcome.cause) == ("cancelled", "none")
    assert is_restart_detail(outcome.detail)
    assert _read(outcome) == SPENT


async def test_a_session_whose_usage_was_never_read_records_null_not_zero():
    rig, _ = await _one_session(_says_nothing_of_usage)
    await rig.settle()
    assert _read(_only_outcome(rig)) == (None, None, None, None)


async def test_a_session_that_never_reached_a_model_call_records_null():
    """Failed before the loop (here: no home room): nothing was spent, and nothing
    was read. A zero would read as «a free session»."""
    agent = ScriptedAgent(script=[blocked])
    rig = _rig(agent=agent, units=("u1",))
    rig.runner._home_rooms.pop("u1")                    # a unit with no home room
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    outcome = _only_outcome(rig)
    assert outcome.outcome == "failed" and _read(outcome) == (None, None, None, None)
    assert agent.calls == 0


# -- the live runner leaves the split, whatever happened to the loop --------------------


class _Agent:
    def __init__(self, usage: Any) -> None:
        self.event_loop_metrics = type("M", (), {"accumulated_usage": usage})()


class _Models:
    def create(self) -> object:
        return object()


async def _live_spend(monkeypatch: pytest.MonkeyPatch, usage: Any, *, raises: bool) -> TokenSpend:
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        if raises:
            raise RuntimeError("down")

        class Result:
            stop_reason = "end_turn"
            metrics = type("M", (), {"accumulated_usage": usage})()
        return Result()

    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: _Agent(usage))
    monkeypatch.setattr(factory, "invoke", invoke)
    runner = LiveCognitionAgentRunner(
        models=_Models(), retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=Metrics(),
    )
    spend = TokenSpend()
    token = SESSION_TOKENS.set(spend)
    try:
        with contextlib.suppress(RuntimeError):
            await runner.run(
                system_prompt="s", prompt="p", tools=[],
                budget=AdaptorBudget(turns=1, total_tokens=1, output_tokens=1, timeout_seconds=5),
                trace_attributes={},
            )
    finally:
        SESSION_TOKENS.reset(token)
    return spend


@pytest.mark.parametrize("raises", [False, True])
async def test_the_live_runner_leaves_the_providers_split_whether_or_not_the_loop_returned(
    monkeypatch, raises,
):
    usage = {"inputTokens": 900, "outputTokens": 100, "cacheReadInputTokens": 700,
             "totalTokens": 1000}
    spend = await _live_spend(monkeypatch, usage, raises=raises)
    assert (spend.input, spend.output, spend.cache_read, spend.total) == (900, 100, 700, 1000)


async def test_the_live_runner_leaves_none_for_a_counter_the_provider_did_not_report(monkeypatch):
    spend = await _live_spend(
        monkeypatch, {"inputTokens": 900, "totalTokens": 900, "outputTokens": 0}, raises=False,
    )
    assert (spend.input, spend.output, spend.cache_read) == (900, None, None)


@pytest.mark.parametrize("raises", [False, True])
async def test_the_live_runner_leaves_the_id_of_the_trace_the_loop_ran_in(monkeypatch, raises):
    """Read inside the `dream:cognition` span, which is open around `_invoke`'s
    `finally`, so the id is the root's whether the loop returned or raised. With no
    recording tracer `current_trace_id` is None, and so is the column."""
    import nora.app.cognition_agent_runner as runner_module

    monkeypatch.setattr(runner_module, "current_trace_id", lambda: "b" * 32)
    assert (await _live_spend(monkeypatch, {"totalTokens": 5}, raises=raises)).trace_id == "b" * 32
    monkeypatch.setattr(runner_module, "current_trace_id", lambda: None)
    assert (await _live_spend(monkeypatch, {"totalTokens": 5}, raises=raises)).trace_id is None
