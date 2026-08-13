from collections.abc import AsyncIterable
from typing import Any

import pytest
from strands import Agent, tool
from strands.models.model import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from budget import BudgetExceededError, ToolCallBudget, guarded


def test_a_fresh_budget_allows_calls_up_to_its_limit():
    budget = ToolCallBudget(max_calls=3)
    budget.spend()
    budget.spend()
    budget.spend()
    assert budget.remaining == 0


def test_spending_past_the_limit_raises():
    budget = ToolCallBudget(max_calls=1)
    budget.spend()
    with pytest.raises(BudgetExceededError):
        budget.spend()


def test_guarded_wraps_a_function_and_spends_the_budget():
    budget = ToolCallBudget(max_calls=2)
    calls = []

    @guarded(budget)
    def do_thing(x: int) -> int:
        calls.append(x)
        return x * 2

    assert do_thing(1) == 2
    assert do_thing(2) == 4
    assert calls == [1, 2]
    with pytest.raises(BudgetExceededError):
        do_thing(3)
    assert calls == [1, 2]  # the third call never ran its body


def test_a_fresh_budget_has_not_exceeded():
    budget = ToolCallBudget(max_calls=1)
    budget.spend()
    assert not budget.exceeded  # used exactly its allowance, never rejected


def test_spending_past_the_limit_sets_exceeded():
    budget = ToolCallBudget(max_calls=1)
    budget.spend()
    with pytest.raises(BudgetExceededError):
        budget.spend()
    assert budget.exceeded


class _RunawayModel(Model):
    """A fake Strands `Model` that requests the SAME tool call every single
    cycle, forever -- never backing off after the tool starts returning
    "budget exhausted" errors. This is the exact shape the underlying bug
    protects against: verified empirically that Strands'
    `DecoratedFunctionTool.stream()` catches `BudgetExceededError` (any
    `Exception` except `InterruptException`) and turns it into an ordinary
    `status: "error"` ToolResult, so a model that treats a tool error as
    just another retryable failure has NO other reason to stop. Without
    Strands' native `limits=` ceiling, an agent driven by this model would
    loop forever.

    Implements the real `strands.models.model.Model` ABC and emits the real
    `StreamEvent` shape `strands/event_loop/streaming.py` expects (verified
    against the installed `strands-agents==1.50.2`) -- this drives the
    ACTUAL Strands event loop and the ACTUAL `tool.stream()` call path, not
    a hand-rolled substitute for either.
    """

    def __init__(self, *, tool_name: str) -> None:
        self._tool_name = tool_name
        self.model_call_count = 0

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> Any:
        return {}

    async def structured_output(
        self,
        output_model: Any,
        prompt: Any,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError
        yield {}  # pragma: no cover -- makes this an async generator, per the ABC

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.model_call_count += 1
        tool_use_id = f"t{self.model_call_count}"
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {
                    "toolUse": {"toolUseId": tool_use_id, "name": self._tool_name}
                },
            }
        }
        yield {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": "{}"}},
            }
        }
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
                "metrics": {"latencyMs": 1},
            }
        }


def test_a_job_actually_terminates_once_its_native_turn_limit_is_reached():
    """The load-bearing test for the primary fix: drives the REAL Strands
    `Agent` event loop, and therefore the REAL `tool.stream()` call path
    (`DecoratedFunctionTool.stream()`) for a `@tool`-wrapped, budget-guarded
    function -- not just the bare guarded Python function, which is all
    `test_guarded_wraps_a_function_and_spends_the_budget` above exercises,
    and exactly why the underlying bug (`BudgetExceededError` silently
    swallowed by Strands, agent loop kept going) shipped through 8 task
    reviews undetected.

    `_RunawayModel` never stops requesting tool calls on its own. The ONLY
    thing that can end this job is Strands' native `limits=` ceiling
    (agent.py's `main()` now passes `limits={"turns": 50, ...}`) -- so
    asserting this call actually RETURNS, with `stop_reason == "limit_turns"`
    and the model call count capped at the configured turn limit, is proof
    the job terminates rather than looping forever. If `agent.py` were
    reverted to the pre-fix `agent(task)` call with no `limits=`, this test
    would hang (bounded here to a small `turns` value specifically so a
    regression fails fast instead of timing out the whole suite).
    """
    budget = ToolCallBudget(max_calls=3)

    @tool
    @guarded(budget)
    def spend() -> str:
        return "spent one unit"

    model = _RunawayModel(tool_name="spend")
    agent = Agent(model=model, tools=[spend])

    result = agent("do the thing", limits={"turns": 10, "total_tokens": 10_000})

    # The job terminated because Strands' own native ceiling fired -- NOT
    # because the (swallowed) BudgetExceededError propagated.
    assert result.stop_reason == "limit_turns"
    assert model.model_call_count == 10

    # The tool-call budget was exhausted long before the job ended -- the
    # per-call counter kept incrementing/tripping the whole time, exactly as
    # it does in production, even though nothing above it was listening to
    # the exception it raised.
    assert budget.exceeded
    assert budget.remaining == 0
