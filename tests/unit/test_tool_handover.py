"""A tool call still running at its pass's wait window is handed over (`BoundedToolExecutor`).

Runs on the real Strands loop with a scripted model. The act envelope sits over an in-memory
store here; the same flow against the SQL envelope is `tests/infra/test_tool_handover_acts.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from nora.agent import factory, tool_executor
from nora.agent.ports import ToolSpec, adapt
from nora.engagement.slots import ConversationSlots
from nora.memory import act_pass
from nora.memory.app.acts import ActEnvelope
from nora.memory.domain.acts import Act, ActAuthority, Outcome
from nora.memory.domain.admission_proposals import PassContext
from tests.unit.test_tool_concurrency import RETRY, _BatchModel, _Gauge, _guard

WINDOW = 0.05


def _context(occasion: str = "called") -> PassContext:
    return PassContext(
        "unit-1", "room-1", False, occasion, "engagement", "pass-1", "trace-1", "model-1",
        "code-1", "instruction-1", "skill-1", "config-1", "identity-1", "policy-1",
    )


def _authority(occasion: str = "called") -> ActAuthority:
    return ActAuthority("unit-1", "person-1", None, "room-1", _context(occasion))


@dataclass
class _Acts:
    """The act store an envelope needs, in memory: reservations, outcomes and pass output."""

    reserved: list[Act] = field(default_factory=list)
    outcomes: list[tuple[Act, Outcome]] = field(default_factory=list)
    pass_output: list[tuple[str, list[str] | None]] = field(default_factory=list)
    settled: asyncio.Event = field(default_factory=asyncio.Event)

    async def reserve(self, act: Act) -> bool:
        if any(a.identity.id == act.identity.id for a in self.reserved):
            return False
        self.reserved.append(act)
        return True

    async def observe(self, act: Act, outcome: Outcome) -> None:
        self.outcomes.append((act, outcome))
        self.settled.set()

    async def latest(self, act: Act) -> Outcome | None:
        found = [o for a, o in self.outcomes if a.identity.id == act.identity.id]
        return found[-1] if found else None

    async def by_key(self, executor: str, key: str) -> Act | None:
        return None

    async def by_destination(self, executor: str, destination: str) -> Act | None:
        return None

    async def append_pass(self, authority: Any, kind: str, content: list[str] | None,
                          **_: Any) -> None:
        self.pass_output.append((kind, content))

    async def read_pass(self, unit: str, pass_id: str) -> dict[str, list[str] | None]:
        return {}


class _Model(_BatchModel):
    """The batch model with real reply text, so `send_reply` is not vetoed as empty."""

    async def stream(self, messages: Any, *args: Any, **kwargs: Any):
        async for event in super().stream(messages, *args, **kwargs):
            delta = event.get("contentBlockDelta", {}).get("delta", {}).get("toolUse")
            if delta is not None:
                delta["input"] = json.dumps({"text": "hello"})
            yield event


def _tool(name: str, gauge: _Gauge, delay: float | None, result: Any = "fine"):
    async def call(text: str = "") -> Any:
        await gauge.work(delay)
        return result
    call.__name__ = name
    return adapt(ToolSpec(name=name, description="", schema={}, call=call, budget=10))


def _agent(name: str, tool: Any, executor: Any) -> Any:
    return factory.create_agent(
        model=_Model([name], "t"), system_prompt="p", tools=[tool], pre_fill=[],
        guard=_guard([name]), trace_attributes={}, retry=RETRY, tool_executor=executor,
    )


def _result(agent: Any) -> dict[str, Any]:
    """The one tool result the step received, as the model reads it."""
    [message] = [m for m in agent.messages if m["role"] == "user"
                 and any("toolResult" in c for c in m["content"])]
    [block] = [c["toolResult"] for c in message["content"] if "toolResult" in c]
    return block


async def _pass(agent: Any, store: _Acts, occasion: str = "called") -> float:
    """Run one pass under the act envelope; the seconds it took."""
    started = time.monotonic()
    with act_pass(_authority(occasion), ActEnvelope(store)):
        await asyncio.wait_for(agent.invoke_async("go"), timeout=10)
    return time.monotonic() - started


def _executor(window: float | None = WINDOW, **kwargs: Any) -> tool_executor.BoundedToolExecutor:
    slots = tool_executor.ToolSlots(4, reserve=1)
    return tool_executor.BoundedToolExecutor(slots, wait_seconds=window, **kwargs)


async def test_a_call_that_settles_within_the_window_returns_its_own_result():
    gauge, store = _Gauge(), _Acts()
    plain = _agent("quick", _tool("quick", gauge, 0.0), _executor(None))
    windowed = _agent("quick", _tool("quick", gauge, 0.0), _executor(5.0))
    await _pass(plain, _Acts())
    await _pass(windowed, store)
    assert _result(windowed) == _result(plain)
    assert store.reserved == [] and store.outcomes == []


async def test_a_call_still_running_at_the_window_returns_started_with_its_act_id_and_runs_on():
    gauge, store = _Gauge(), _Acts()
    executor = _executor()
    agent = _agent("slow", _tool("slow", gauge, None), executor)
    elapsed = await _pass(agent, store)
    assert elapsed < WINDOW + 1
    [act] = store.reserved
    body = json.loads(_result(agent)["content"][0]["text"])
    assert body["status"] == "started" and body["act"] == act.identity.id
    assert act.identity.id in body["note"] and "act_event" in body["note"]
    assert (act.kind, act.identity.executor) == ("delegation", "tool.handover")
    assert act.authority == _authority()
    assert gauge.running == 1 and store.outcomes == []
    gauge.gate.set()
    await asyncio.wait_for(store.settled.wait(), timeout=5)
    [(settled, outcome)] = store.outcomes
    assert settled.identity.id == act.identity.id
    assert (outcome.verdict, outcome.result) == ("done", [{"text": "fine"}])


@pytest.mark.parametrize(("result", "verdict"), [
    ({"status": "ok"}, "done"), ({"status": "nothing_found"}, "done"),
    ({"status": "unavailable", "reason": "down"}, "failed"),
])
async def test_a_handed_over_call_records_done_or_failed_from_its_own_status(result, verdict):
    gauge, store = _Gauge(), _Acts()
    agent = _agent("slow", _tool("slow", gauge, None, result), _executor())
    await _pass(agent, store)
    gauge.gate.set()
    await asyncio.wait_for(store.settled.wait(), timeout=5)
    assert [o.verdict for _, o in store.outcomes] == [verdict]


async def test_a_handed_over_call_that_is_cancelled_is_recorded_uncertain_and_not_rerun():
    gauge, store = _Gauge(), _Acts()
    executor = _executor()
    agent = _agent("slow", _tool("slow", gauge, None), executor)
    await _pass(agent, store)
    [pending] = executor.handed_over
    pending.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    assert [o.verdict for _, o in store.outcomes] == ["uncertain"]
    assert gauge.running == 0 and gauge.peak == 1


async def test_the_pass_slot_and_the_reserve_slot_are_given_back_at_the_hand_over():
    gauge, store = _Gauge(), _Acts()
    slots = tool_executor.ToolSlots(3, reserve=1)
    executor = tool_executor.BoundedToolExecutor(slots, wait_seconds=WINDOW)
    agent = _agent("slow", _tool("slow", gauge, None), executor)
    turn = asyncio.create_task(_pass(agent, store))
    for _ in range(200):
        if slots.in_flight == 1:
            break
        await asyncio.sleep(0.005)
    assert slots.in_flight == 1
    await turn
    assert gauge.running == 1 and slots.in_flight == 0 and slots.waiting == 0
    background = tool_executor.BoundedToolExecutor(slots, background=True)
    assert background.slots is slots
    for _ in range(2):  # every slot a background call may take is free again
        await slots.acquire(background=True)
    assert slots.in_flight == 2
    gauge.gate.set()


async def test_the_next_message_to_the_room_starts_while_the_handed_over_work_runs():
    gauge, store = _Gauge(), _Acts()
    spawned: list[Any] = []
    room = ConversationSlots(maxlen=4, spawn=spawned.append)
    key = ("signal", "room-1")
    agent = _agent("slow", _tool("slow", gauge, None), _executor())

    async def first_message() -> None:
        room.acquire(key)
        try:
            await _pass(agent, store)
        finally:
            room.release_and_dispatch(key)

    await first_message()
    assert gauge.running == 1
    assert room.try_acquire(key) is True
    gauge.gate.set()


@pytest.mark.parametrize("name", [
    "send_reply", "reaction_send", "send_gif", "send_to", "media_send",
    "memory_propose", "graph_propose", "intention_commit",
])
async def test_a_reply_a_delivery_or_a_graph_write_is_never_handed_over(name):
    gauge, store = _Gauge(), _Acts()
    agent = _agent(name, _tool(name, gauge, None, {"status": "ok"}), _executor())
    turn = asyncio.create_task(_pass(agent, store))
    await asyncio.sleep(WINDOW * 4)
    assert not turn.done() and store.reserved == []
    gauge.gate.set()
    await turn
    assert json.loads(_result(agent)["content"][0]["text"]) == {"status": "ok"}
    assert store.reserved == []


def test_every_terminal_tool_is_among_those_never_handed_over():
    from nora.agent.catalog.entries import NATIVE_TOOLS
    terminal = {name for name, tool in NATIVE_TOOLS.items() if tool.terminal}
    deliveries = {"send_to", "media_send"}
    assert terminal | deliveries <= tool_executor.NEVER_HANDED_OVER
    proposals_and_commits = {"memory_propose", "graph_propose", "intention_commit"}
    assert proposals_and_commits <= tool_executor.NEVER_HANDED_OVER


async def test_a_background_executor_never_hands_over_whatever_window_it_is_given():
    gauge, store = _Gauge(), _Acts()
    executor = tool_executor.BoundedToolExecutor(
        tool_executor.ToolSlots(4, reserve=1), background=True, wait_seconds=WINDOW,
    )
    assert executor.wait_seconds is None
    agent = _agent("slow", _tool("slow", gauge, None), executor)
    turn = asyncio.create_task(_pass(agent, store))
    await asyncio.sleep(WINDOW * 4)
    assert not turn.done() and store.reserved == []
    gauge.gate.set()
    await turn


@pytest.mark.parametrize("occasion", ["debrief", "wake", "wake_history", "review"])
async def test_a_reflection_occasion_on_the_interactive_executor_is_not_handed_over(occasion):
    gauge, store = _Gauge(), _Acts()
    agent = _agent("slow", _tool("slow", gauge, None), _executor())
    turn = asyncio.create_task(_pass(agent, store, occasion))
    await asyncio.sleep(WINDOW * 4)
    assert not turn.done() and store.reserved == []
    gauge.gate.set()
    await turn
    assert gauge.running == 0


async def test_a_call_outside_any_pass_is_not_handed_over():
    gauge = _Gauge()
    agent = _agent("slow", _tool("slow", gauge, None), _executor())
    turn = asyncio.create_task(agent.invoke_async("go"))
    await asyncio.sleep(WINDOW * 4)
    assert not turn.done()
    gauge.gate.set()
    await asyncio.wait_for(turn, timeout=5)


@pytest.mark.parametrize("window", [0, -1, math.inf, math.nan])
def test_the_wait_window_must_be_positive_and_finite(window):
    with pytest.raises(ValueError, match="positive and finite"):
        tool_executor.BoundedToolExecutor(tool_executor.ToolSlots(2), wait_seconds=window)


def test_the_example_config_waits_five_seconds_and_hands_the_executor_that_window(example_config):
    from nora.app.composition_models import engagement_executor

    assert example_config.engagement.tool_wait_seconds == 5
    executor = engagement_executor(example_config)
    assert executor.wait_seconds == 5 and not executor.background
    assert executor.slots is tool_executor.shared_tool_slots()


@pytest.mark.parametrize("window", [0, -1, math.inf, math.nan])
def test_the_configured_wait_must_be_positive_and_finite(example_config, window):
    from pydantic import ValidationError

    from nora.config import EngagementConfig

    fields = example_config.engagement.model_dump() | {"tool_wait_seconds": window}
    with pytest.raises(ValidationError):
        EngagementConfig(**fields)
