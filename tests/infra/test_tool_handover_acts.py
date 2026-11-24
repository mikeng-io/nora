"""A handed-over tool call against the production act envelope (PostgreSQL)."""

# ruff: noqa: F811 - pytest resolves imported fixture names.

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

from nora.agent import factory, tool_executor
from nora.agent.guards import ToolCallLog
from nora.app.composition_fire import build_recover
from nora.app.handover_sweep import sweep_handovers
from nora.infra.sql.cognitive_graph_schema import NORA_ACT, NORA_ACT_OUTCOME
from nora.memory import act_pass, declare_act, hand_over_tool
from nora.memory.app.acts import ActInvocation
from nora.memory.domain.acts import ActAuthority, Outcome
from nora.obs.metrics import Metrics
from nora.testing import FakeComputeJobStore
from nora.testing.reminders import FakeReminderStore
from tests.infra.test_act_envelope import PASS, _rows, acts  # noqa: F401
from tests.unit.test_tool_concurrency import RETRY, _Gauge, _guard
from tests.unit.test_tool_handover import _Model, _result, _tool

WINDOW = 0.05


def _agent(name: str, tool: Any, executor: Any, *, tool_call_log: Any = None) -> Any:
    return factory.create_agent(
        model=_Model([name], "t"), system_prompt="p", tools=[tool], pre_fill=[],
        guard=_guard([name]), trace_attributes={}, retry=RETRY, tool_executor=executor,
        tool_call_log=tool_call_log,
    )


def _executor() -> tool_executor.BoundedToolExecutor:
    slots = tool_executor.ToolSlots(4, reserve=1)
    return tool_executor.BoundedToolExecutor(slots, wait_seconds=WINDOW)


async def _run(agent: Any, envelope: Any, *, intention: str | None = None) -> None:
    authority = ActAuthority("unit-1", "person-1", intention, "room-1", PASS)
    with act_pass(authority, envelope):
        await asyncio.wait_for(agent.invoke_async("go"), timeout=10)


async def _finish(executor: tool_executor.BoundedToolExecutor) -> None:
    await asyncio.wait_for(asyncio.gather(*executor.handed_over), timeout=5)


async def test_a_handed_over_call_is_one_delegation_act_under_the_pass_authority(acts):
    envelope, engine = acts
    gauge, executor = _Gauge(), _executor()
    agent = _agent("slow", _tool("slow", gauge, None, {"status": "ok"}), executor)
    await _run(agent, envelope, intention="intention-1")

    [act] = await _rows(engine, NORA_ACT)
    assert (act["executor"], act["act_kind"], act["guarantee"]) == (
        "tool.handover", "delegation", "unknown")
    assert (act["trigger_id"], act["ordinal"], act["unit"]) == ("pass-1", 0, "unit-1")
    assert (act["authority_person"], act["authority_intention"]) == ("person-1", "intention-1")
    assert (act["pass"], act["occasion"], act["writer"]) == ("pass-1", "called", "engagement")
    assert json.loads(_result(agent)["content"][0]["text"])["act"] == act["id"]
    assert await _rows(engine, NORA_ACT_OUTCOME) == []

    gauge.gate.set()
    await _finish(executor)
    [outcome] = await _rows(engine, NORA_ACT_OUTCOME)
    assert (outcome["act_id"], outcome["verdict"]) == (act["id"], "done")
    assert outcome["result"] == [{"text": json.dumps({"status": "ok"})}]
    assert len(await _rows(engine, NORA_ACT)) == 1


async def test_a_handed_over_call_that_reports_unavailable_is_recorded_failed(acts):
    envelope, engine = acts
    gauge, executor = _Gauge(), _executor()
    agent = _agent("slow", _tool("slow", gauge, None, {"status": "unavailable"}), executor)
    await _run(agent, envelope)
    gauge.gate.set()
    await _finish(executor)
    [outcome] = await _rows(engine, NORA_ACT_OUTCOME)
    assert outcome["verdict"] == "failed"


async def test_a_handed_over_call_that_is_cancelled_is_uncertain_and_never_rerun(acts):
    envelope, engine = acts
    gauge, executor = _Gauge(), _executor()
    agent = _agent("slow", _tool("slow", gauge, None), executor)
    await _run(agent, envelope)
    [pending] = executor.handed_over
    pending.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    [outcome] = await _rows(engine, NORA_ACT_OUTCOME)
    assert outcome["verdict"] == "uncertain"
    assert gauge.peak == 1 and len(await _rows(engine, NORA_ACT)) == 1


async def test_the_sweep_records_uncertain_once_on_a_handover_a_dead_process_left(acts):
    envelope, engine = acts
    with act_pass(ActAuthority("unit-1", "person-1", None, "room-1", PASS), envelope):
        left = await hand_over_tool({"toolUseId": "t0", "name": "slow", "input": {}})
    assert left is not None and await _rows(engine, NORA_ACT_OUTCOME) == []

    assert await sweep_handovers(engine) == 1
    assert await sweep_handovers(engine) == 0
    [outcome] = await _rows(engine, NORA_ACT_OUTCOME)
    assert (outcome["act_id"], outcome["verdict"]) == (left.act_id, "uncertain")


async def test_a_computer_call_that_declared_its_dispatch_gets_no_second_act(acts):
    envelope, engine = acts
    gate = asyncio.Event()

    async def dispatched(text: str = "") -> dict[str, str]:
        act = declare_act(
            "computer.dispatch", "computer-job:job-1", event="job-1", key="job-1",
            authority=ActAuthority("unit-1"),
        )
        await envelope.invoke(act, lambda _key: _uncertain())
        await gate.wait()
        return {"status": "ok"}

    async def _uncertain() -> ActInvocation[None]:
        return ActInvocation(None, Outcome("uncertain"))

    dispatched.__name__ = "computer_run"
    from nora.agent.ports import ToolSpec, adapt
    tool = adapt(ToolSpec(name="computer_run", description="", schema={}, call=dispatched,
                          budget=10))
    log = ToolCallLog(conversation_id="room-1", conversation_type="group", tag_id="pass-1",
                      cacheable=frozenset(), store=None, metrics=Metrics())
    executor = _executor()
    agent = _agent("computer_run", tool, executor, tool_call_log=log)
    await _run(agent, envelope)

    [act] = await _rows(engine, NORA_ACT)
    assert act["executor"] == "computer.dispatch"
    assert json.loads(_result(agent)["content"][0]["text"])["act"] == act["id"]
    gate.set()
    await _finish(executor)
    assert len(await _rows(engine, NORA_ACT)) == 1


async def test_startup_recovery_records_the_outcome_of_a_lost_handover(acts):
    envelope, engine = acts
    with act_pass(ActAuthority("unit-1", "person-1", None, "room-1", PASS), envelope):
        left = await hand_over_tool({"toolUseId": "t0", "name": "slow", "input": {}})
    assert left is not None and await _rows(engine, NORA_ACT_OUTCOME) == []

    await build_recover(
        clock=lambda: datetime.now(UTC), compute_backends={}, compute_jobs=FakeComputeJobStore(),
        engine=engine, metrics=Metrics(), reminders=FakeReminderStore(),
    )

    outcomes = await _rows(engine, NORA_ACT_OUTCOME)
    assert [(row["act_id"], row["verdict"]) for row in outcomes] == [(left.act_id, "uncertain")]
