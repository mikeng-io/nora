"""The shared tool executor (`nora.agent.factory.BoundedToolExecutor`), on the REAL Strands loop.

Strands runs a model's parallel tool batch with no limit, and every tag, dream and sub-agent loop
had its own. These tests pin what the bound must do across agents: at most `limit` tool calls in
flight, shared by every turn that holds the same executor; result order kept; a cancelled turn
releasing its slots; and every production agent taking the one process-wide executor by default.

The scripted model emits one assistant message holding a whole batch of tool uses, so the batch is
the thing under test, not a sequence of single calls.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

from strands.models.model import Model

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.ports import ToolSpec, adapt
from nora.obs.metrics import Metrics

RETRY = factory.build_retry_policy(timeout_seconds=30.0)
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "nora" / "skills"


class _BatchModel(Model):
    """First call: one assistant message with `batch` tool uses. Then prose, which ends the turn."""

    def __init__(self, names: list[str], prefix: str) -> None:
        self._names = names
        self._prefix = prefix
        self._calls = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, messages: Any, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        self._calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self._calls == 1:
            for i, name in enumerate(self._names):
                yield {"contentBlockStart": {"start": {"toolUse": {
                    "name": name, "toolUseId": f"{self._prefix}{i}"}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                            "metrics": {"latencyMs": 1}}}


class _Gauge:
    """Counts the calls running at once, and the most ever running together."""

    def __init__(self) -> None:
        self.running = 0
        self.peak = 0
        self.gate = asyncio.Event()

    async def work(self, delay: float | None) -> str:
        """Hold a slot for `delay` seconds, or until `gate` is set when `delay` is None."""
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if delay is None:
                await self.gate.wait()
            else:
                await asyncio.sleep(delay)
        finally:
            self.running -= 1
        return "ok"


def _tools(gauge: _Gauge, names: list[str], delays: list[float | None]) -> list[Any]:
    def make(name: str, delay: float | None):
        async def call() -> str:
            return await gauge.work(delay)
        call.__name__ = name
        return adapt(ToolSpec(name=name, description="", schema={}, call=call, budget=10))
    return [make(n, d) for n, d in zip(names, delays, strict=True)]


def _guard(names: list[str]) -> ReplyGuard:
    return ReplyGuard(conversation_id="g1", budgets=dict.fromkeys(names, 10),
                      terminal=frozenset(), metrics=Metrics())


def _agent(model: Any, tools: list[Any], names: list[str], executor: Any) -> Any:
    return factory.create_agent(
        model=model, system_prompt="p", tools=tools, pre_fill=[], guard=_guard(names),
        trace_attributes={}, retry=RETRY, tool_executor=executor,
    )


async def test_a_batch_runs_at_most_the_limit_at_once_and_keeps_its_result_order():
    names = [f"slow{i}" for i in range(5)]
    gauge = _Gauge()
    executor = factory.BoundedToolExecutor(2)
    agent = _agent(_BatchModel(names, "b"), _tools(gauge, names, [0.02, 0.01, 0.03, 0.005, 0.0]),
                   names, executor)
    await asyncio.wait_for(agent.invoke_async("go"), timeout=5)
    assert gauge.peak == 2
    results = [m for m in agent.messages if m["role"] == "user"
               and any("toolResult" in c for c in m["content"])]
    ids = [c["toolResult"]["toolUseId"] for c in results[0]["content"] if "toolResult" in c]
    assert ids == [f"b{i}" for i in range(5)]


async def test_overlapping_turns_share_one_limit():
    gauge_a, gauge_b = _Gauge(), _Gauge()
    names_a = [f"a{i}" for i in range(3)]
    names_b = [f"c{i}" for i in range(3)]
    executor = factory.BoundedToolExecutor(2)
    agent_a = _agent(_BatchModel(names_a, "x"), _tools(gauge_a, names_a, [0.02] * 3), names_a,
                     executor)
    agent_b = _agent(_BatchModel(names_b, "y"), _tools(gauge_b, names_b, [0.02] * 3), names_b,
                     executor)
    await asyncio.wait_for(
        asyncio.gather(agent_a.invoke_async("go"), agent_b.invoke_async("go")), timeout=5)
    assert max(gauge_a.peak, gauge_b.peak) <= 2
    assert executor.peak == 2 and executor.in_flight == 0 and executor.waiting == 0


async def test_cancelling_a_turn_releases_every_slot_it_held():
    """A turn cancelled while its tools hold the slots must give them back: the next turn on the
    same executor runs, and the executor reads idle."""
    names = ["hang0", "hang1"]
    gauge = _Gauge()
    executor = factory.BoundedToolExecutor(2)
    agent = _agent(_BatchModel(names, "h"), _tools(gauge, names, [None, None]), names, executor)
    turn = asyncio.create_task(agent.invoke_async("go"))
    for _ in range(200):
        if executor.in_flight == 2:
            break
        await asyncio.sleep(0.005)
    assert executor.in_flight == 2
    turn.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await turn
    assert executor.in_flight == 0 and executor.waiting == 0

    gauge.gate.set()
    names2 = ["after0"]
    follow = _agent(_BatchModel(names2, "f"), _tools(gauge, names2, [0.0]), names2, executor)
    await asyncio.wait_for(follow.invoke_async("go"), timeout=5)
    assert executor.in_flight == 0


async def test_every_production_agent_takes_the_one_shared_executor_by_default():
    """The tag, dream and sub-agent loops all default to `shared_tool_executor()`, so overlapping
    turns of different kinds draw on the same limit, not one each."""
    shared = factory.shared_tool_executor()
    assert shared is factory.shared_tool_executor()
    guard = _guard([])
    model = _BatchModel([], "n")
    tag = factory.create_agent(
        model=model, system_prompt="p", tools=[], pre_fill=[], guard=guard,
        trace_attributes={}, retry=RETRY,
    )
    dream = factory.create_dream_agent(
        model=model, system_prompt="p", tools=[], guard=guard, trace_attributes={}, retry=RETRY,
    )
    sub = factory.create_subagent(
        model=model, system_prompt="p", tools=[], guard=guard, progress=guard,
        skills_dir=SKILLS_DIR, trace_attributes={}, retry=RETRY,
    )
    assert tag.tool_executor is shared and dream.tool_executor is shared
    assert sub.tool_executor is shared


async def test_the_executor_refuses_a_limit_that_admits_nothing():
    try:
        factory.BoundedToolExecutor(0)
    except ValueError:
        return
    raise AssertionError("a limit of 0 must be refused")
