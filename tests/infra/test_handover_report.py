"""A handed-over call is reported once, whatever its outcome, through the real condition matcher.

The report is an intention whose trigger is an act event on the hand-over's act. These tests run
the production matcher and the production proactive claims over PostgreSQL, and count what the
proactive pass would be given: one due occurrence, claimed once.
"""

# ruff: noqa: F811 - pytest resolves imported fixture names.

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa

from nora.agent.computer_ctx import _cctx__computer_queued
from nora.agent.guards import ToolCallLog
from nora.agent.ports import ToolSpec, adapt
from nora.agent.tool_executor import BoundedToolExecutor, ToolSlots
from nora.app.compute_poller import ComputeJobPoller
from nora.app.computer_queue import SegmentRegistry
from nora.app.fire import SENT
from nora.app.handover_sweep import sweep_handovers
from nora.computer.wire import _scoped_computer_queues
from nora.domain.computer_tasks import TERMINAL, computer_admission_window
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.cognitive_graph_schema import INTENTION, NORA_ACT, NORA_ACT_OUTCOME
from nora.infra.sql.compute_jobs import SqlComputeJobStore
from nora.infra.sql.computer_tasks import SqlComputerTaskStore
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.item_registration import RegistrationScope, SqlItemRegistration
from nora.memory import act_pass, hand_over_tool, wire
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.proactive_claims import SqlProactiveClaims
from nora.memory.domain.acts import Act, ActAuthority, ActIdentity, Outcome
from nora.obs.metrics import Metrics
from nora.testing import FakeStreamRedis
from tests.infra.test_act_envelope import PASS, _Revisions
from tests.infra.test_act_envelope import _rows as _act_rows
from tests.infra.test_condition_matcher import _health, _intention, _platform_act
from tests.infra.test_item_registration import (
    _PERSON,
    _ROOM,
    _UNIT,
    _registration,
    engine,  # noqa: F401
)
from tests.infra.test_proactive_pass import ScriptedRunner, setup
from tests.infra.test_retrieval_scope import PERSON, ROOM, UNIT, scenario  # noqa: F401
from tests.infra.test_tool_handover_acts import _agent, _finish
from tests.unit.test_tool_handover import _result

LATER = datetime.now(UTC) + timedelta(days=1)


def _handover(*, intention: str | None = None) -> Act:
    return Act(
        ActIdentity("tool.handover", "pass-1", 0), "delegation", "tool-call:pass-1:t0",
        ActAuthority(_UNIT, _PERSON, intention, _ROOM, None), "unknown",
    )


def _report(act: Act) -> dict:
    return {"kind": "condition", "form": "act_event", "value": {"act_id": act.identity.id}}


async def _commit_report(engine, act: Act, id_: str = "report", **fields) -> None:
    """The report intention a pass or its debrief commits: a trigger on the act, delivered once."""
    await _intention(engine, id_, "act_event", {"own_delivery": True}, trigger=_report(act),
                     **fields)


async def _due(engine) -> list:
    return [s for s in await SqlGraphStimuli(engine).read(_UNIT) if s.kind == "intention_due"]


async def _states(engine) -> dict[str, set[str]]:
    """The row states this behaviour depends on: a live act and outcome, an open intention."""
    async with engine.connect() as conn:
        return {
            "act": set(await conn.scalars(sa.select(NORA_ACT.c.state))),
            "outcome": set(await conn.scalars(sa.select(NORA_ACT_OUTCOME.c.state))),
            "intention": set(await conn.scalars(sa.select(INTENTION.c.state))),
        }


async def _tick(engine) -> None:
    await wire.condition_matcher(engine).run_once()


@pytest.mark.parametrize("verdict", ["done", "failed", "uncertain"])
async def test_each_outcome_of_a_handed_over_call_gives_exactly_one_report(engine, verdict):
    store, act = SqlActStore(engine, _registration()), _handover()
    await _commit_report(engine, act)
    assert await store.reserve(act)
    await _tick(engine)
    assert await _due(engine) == []  # reserved, no outcome yet: nothing fires at launch

    await store.observe(act, Outcome(verdict))
    await _tick(engine)
    await _tick(engine)

    assert await _states(engine) == {
        "act": {"live"}, "outcome": {"live"}, "intention": {"open"},
    }
    [due] = await _due(engine)
    assert (due.graph_id, due.occurrence) == ("report", await _act_graph_id(engine))
    claims = SqlProactiveClaims(engine)
    [available] = await claims.due_intentions(LATER)
    assert await claims.claim(available, LATER) is not None
    assert await claims.due_intentions(LATER) == ()


async def _act_graph_id(engine) -> str:
    async with engine.connect() as conn:
        return await conn.scalar(sa.select(NORA_ACT.c.graph_id))


async def test_an_uncertain_outcome_later_settled_to_done_gives_one_report_not_two(engine):
    store, act = SqlActStore(engine, _registration()), _handover()
    await _commit_report(engine, act)
    assert await store.reserve(act)
    await store.observe(act, Outcome("uncertain"))
    await _tick(engine)
    [first] = await _due(engine)

    await store.observe(act, Outcome("done"))
    await _tick(engine)

    assert await _due(engine) == [first]


async def test_a_swept_handover_gives_exactly_one_report(engine):
    engine.update_execution_options(item_registration=_registration())
    store, act = SqlActStore(engine, _registration()), _handover()
    await _commit_report(engine, act)
    assert await store.reserve(act)
    await _tick(engine)
    assert await _due(engine) == []

    assert await sweep_handovers(engine) == 1
    await _tick(engine)
    await sweep_handovers(engine)
    await _tick(engine)

    assert len(await _due(engine)) == 1


async def test_a_report_the_debrief_commits_after_the_outcome_is_still_one_report(engine):
    store, act = SqlActStore(engine, _registration()), _handover()
    assert await store.reserve(act)
    await store.observe(act, Outcome("done"))
    await _tick(engine)
    assert await _due(engine) == []  # nobody promised a report: nothing is delivered

    await _commit_report(engine, act, asserted_at=datetime.now(UTC))
    await _tick(engine)
    await _tick(engine)

    assert len(await _due(engine)) == 1


async def test_a_proactive_pass_that_hands_over_moves_its_own_intention(engine):
    store, act = SqlActStore(engine, _registration()), _handover(intention="report")
    await _commit_report(engine, act)
    await _intention(engine, "other", "act_event", {"own_delivery": True}, trigger=_report(act))
    assert await store.reserve(act)
    await store.observe(act, Outcome("done"))
    await _tick(engine)
    assert [(s.graph_id) for s in await _due(engine)] == ["report"]


async def test_the_hand_over_itself_reserves_the_act_without_an_outcome(engine):
    envelope = wire.act_envelope(engine, _registration())
    context = replace(PASS, unit=_UNIT, room=_ROOM)
    with act_pass(ActAuthority(_UNIT, _PERSON, None, _ROOM, context), envelope):
        handed = await hand_over_tool({"toolUseId": "t0", "name": "slow", "input": {}})
    assert handed is not None and handed.act is not None
    await _commit_report(engine, handed.act)
    await _tick(engine)
    assert await _due(engine) == []  # the launch is not an outcome

    await handed.settle(Outcome("done"))
    await _tick(engine)
    assert len(await _due(engine)) == 1


async def test_no_committed_intention_means_no_report(engine):
    store, act = SqlActStore(engine, _registration()), _handover()
    assert await store.reserve(act)
    await store.observe(act, Outcome("done"))
    await _tick(engine)
    assert await _due(engine) == []
    assert (await _states(engine))["outcome"] == {"live"}  # positive control: the act ran


@pytest.mark.parametrize("verdict", ["done", "failed", "uncertain"])
async def test_an_act_event_on_a_platform_delivery_still_waits_for_a_healthy_source(
    engine, verdict,
):
    store, act = SqlActStore(engine, _registration()), _platform_act()
    act = replace(act, authority=replace(act.authority, unit=_UNIT))
    await _commit_report(engine, act)
    assert await store.reserve(act)
    await store.observe(act, Outcome(verdict))
    await _health(engine, "signal", "down")
    await _tick(engine)
    assert await _due(engine) == []

    await _health(engine, "signal", "up", at=datetime.now(UTC) - timedelta(seconds=1))
    await _tick(engine)
    assert len(await _due(engine)) == 1


@pytest.mark.parametrize("handed_over", [True, False], ids=["past-window", "inside-window"])
async def test_a_computer_call_has_one_delivery_through_the_route_its_window_selects(
    scenario, handed_over,
):
    await _assert_computer_delivery_route(scenario, handed_over=handed_over)


async def test_a_computer_call_dispatched_after_handover_gives_exactly_one_report(scenario):
    await _assert_computer_delivery_route(scenario, handed_over=True, late_dispatch=True)


class _ObservedComputerTasks(SqlComputerTaskStore):
    def __init__(self, engine):
        super().__init__(engine)
        self.admitted: asyncio.Future[UUID] = asyncio.get_running_loop().create_future()
        self.stamped = asyncio.Event()

    async def try_claim_head(self, task_id, *, now):
        claimed = await super().try_claim_head(task_id, now=now)
        if claimed is None and not self.admitted.done():
            self.admitted.set_result(task_id)
        return claimed

    async def get(self, task_id):
        row = await super().get(task_id)
        window = computer_admission_window.get()
        if (window is not None and window.report_act_id is not None
                and row is not None and row.status in TERMINAL):
            await self.stamped.wait()
            return await super().get(task_id)
        return row


def _native_queue_wrapper(admission):
    ctx = SimpleNamespace(
        computer_admission=admission, bound_computer_scope="abcd",
        computer_platform="signal", bound_conversation_id=ROOM, conversation_type="group",
        computer_actor="fixture", _computer_requester=lambda: PERSON,
        grant=None, fire_turn=False, relay_turn=False, subagent_job=None,
        _segment_deadline={"native": 10},
    )
    # The production wrapper with a zero live wait, so the test controls queue progress.
    return _cctx__computer_queued(ctx, "native", 0, dirty=False)


def _computer_tool(admission, admit, release, *, late_dispatch):
    async def computer_run(text: str = "") -> dict:
        raise AssertionError("a queued call runs in the queue runner")

    queued = _native_queue_wrapper(admission)(computer_run)

    async def call(text: str = "") -> dict:
        await admit.wait()
        if late_dispatch:
            return await queued(text=text)
        submitted = await admission.submit(
            scope="abcd", kind="native", payload={"tool": "computer_run"},
            origin={"platform": "signal", "conversation_id": ROOM, "requester_person_id": PERSON},
        )
        assert not submitted.granted
        await release.wait()
        return {"status": "started", "task": str(submitted.task.id)}

    return adapt(ToolSpec(name="computer_run", description="", schema={}, call=call, budget=10))


async def _assert_computer_delivery_route(scenario, *, handed_over, late_dispatch=False):
    engine = scenario.engine
    scope = RegistrationScope(scenario.policy, scenario.persons)
    engine.update_execution_options(item_registration=SqlItemRegistration(scope, _Revisions()))
    tasks, jobs = _ObservedComputerTasks(engine), SqlComputeJobStore(engine)
    def clock():
        return datetime.now(UTC)

    metrics = Metrics()
    segments = SegmentRegistry()
    queues = await _scoped_computer_queues(
        allowlist=scenario.policy, background=None, clock=clock, compute_jobs=jobs,
        computer=None, computer_context=None, computer_events=None,
        computer_redis=FakeStreamRedis(),
        computer_segments=segments, computer_tasks=tasks, computer_token="",
        lifecycle=None, metrics=metrics, op_clock=clock, tabs=None, engine=engine,
        scoped_cfg=SimpleNamespace(browser_idle_seconds=600),
    )
    admission = queues.computer_admission
    head = await admission.submit(scope="abcd", kind="native", payload={}, origin={})
    assert head.granted
    admit = asyncio.Event()
    release = asyncio.Event()
    admitted = tasks.admitted
    if not late_dispatch:
        admit.set()
    if not handed_over:
        release.set()

    tool = _computer_tool(admission, admit, release, late_dispatch=late_dispatch)
    executor = BoundedToolExecutor(ToolSlots(4, reserve=1), wait_seconds=0.6)
    log = ToolCallLog(conversation_id=ROOM, conversation_type="group", tag_id=PASS.pass_id,
                      cacheable=frozenset(), store=None, metrics=metrics)
    agent = _agent("computer_run", tool, executor, tool_call_log=log)
    context = replace(PASS, unit=UNIT, room=ROOM)
    envelope = wire.act_envelope(engine)
    report = "Generated computer task report"
    proactive, sender, _ = setup(
        scenario, ScriptedRunner((("send_reply", {"text": report}),)), [], now=LATER,
    )
    compute = []

    async def speak(event):
        compute.append(event)
        return SENT

    poller = ComputeJobPoller(
        store=jobs, backends={"computer": queues.computer_task_backend}, speak=speak,
        slots=ConversationSlots(maxlen=4, spawn=asyncio.create_task), metrics=metrics,
        tick_seconds=1, clock=lambda: LATER,
        policy=scenario.policy, persons=scenario.persons,
    )
    try:
        with act_pass(ActAuthority(UNIT, PERSON, None, ROOM, context), envelope):
            await asyncio.wait_for(agent.invoke_async("go"), timeout=5)
        result = json.loads(_result(agent)["content"][0]["text"])
        [row] = [r for r in await _act_rows(engine, NORA_ACT) if r["pass"] == context.pass_id]
        if late_dispatch:
            assert row["executor"] == "tool.handover"
            assert not admitted.done()
        else:
            assert row["executor"] == "computer.dispatch"
            assert admitted.result() == UUID(row["idempotency_key"])
        if handed_over:
            assert result["act"] == row["id"]
            act = await envelope.by_destination(row["executor"], row["destination"])
            assert act is not None
            await _commit_report(
                engine, act, unit=UNIT, origin_room=f"signal:{ROOM}", owed_to=PERSON,
            )
        else:
            assert result == {"status": "started", "task": str(admitted.result())}
        admit.set()
        task_id = await asyncio.wait_for(admitted, timeout=5)
        task = await tasks.get(task_id)
        assert task is not None and task.status == "queued" and task.delivered_at is None
        await _tick(engine)
        await proactive.poll()
        assert sender.texts == []
        payload = {"tool": "computer_run", "args": {"text": "hello"}} if late_dispatch else {
            "tool": "computer_run",
        }
        await _finish_queued_task(queues, segments, head.task.id, task_id, payload)
        finished = await tasks.get(task_id)
        assert finished is not None and finished.delivered_at is not None
    finally:
        admit.set()
        release.set()
        tasks.stamped.set()
        await _finish(executor)

    for _ in range(2):
        await queues.computer_queue_runner.tick()
        await poller.tick()
        await _tick(engine)
        await proactive.poll()

    assert sender.texts == ([report] if handed_over else [])
    assert len(compute) == (0 if handed_over else 1)
    assert len(sender.texts) + len(compute) == 1
    result_row = await tasks.get(task_id)
    assert result_row is not None and result_row.status == "done"
    assert result_row.result["note"] == "Generated"
    assert result_row.delivered_at is not None
    assert result_row.origin.get("deliver") == ("job" if handed_over else None)
    if late_dispatch:
        [outcome] = [r for r in await _act_rows(engine, NORA_ACT_OUTCOME)
                     if r["act_id"] == row["id"]]
        assert outcome["verdict"] == "done"
        assert json.loads(outcome["result"][0]["text"]) == result_row.result


async def _finish_queued_task(queues, segments, head_id, task_id, payload):
    runner = queues.computer_queue_runner
    release_work = asyncio.Event()

    async def execute(task):
        assert task.id == task_id and task.payload == payload
        await release_work.wait()
        return {"status": "ok", "note": "Generated", "no_guest_effect": True}

    runner.set_dispatch("native", execute)
    await queues.computer_admission.finish(
        head_id, status="done", result={"no_guest_effect": True},
    )
    try:
        assert await runner.tick() == 1
        worker = segments.executor(task_id)
        assert worker is not None
        release_work.set()
        await asyncio.wait_for(worker, timeout=5)
    finally:
        release_work.set()
