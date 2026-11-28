"""The production envelope against PostgreSQL and generated destinations."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from mcp.types import Tool
from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

from nora.agent.mcp import MCPClient, McpMount, build_mcp_tools
from nora.agent.registry import aggregate_budgeted_tools
from nora.app.computer_queue import ComputerAdmission, ComputerQueueRunner, _stream
from nora.app.computer_task_acts import ComputerTaskActs
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import (
    NORA_ACT,
    NORA_ACT_OUTCOME,
    NORA_PASS_OUTPUT,
    REGISTRY,
)
from nora.infra.sql.computer_events import SqlComputerEventStore
from nora.infra.sql.computer_tasks import SqlComputerTaskStore
from nora.infra.sql.item_registration import RegistrationScope, SqlItemRegistration
from nora.infra.sql.schema import COMPUTER_EVENTS
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.app.act_scope import act_pass
from nora.memory.app.acts import ActEnvelope, ActInvocation
from nora.memory.domain.acts import Act, ActAuthority, ActIdentity, Outcome
from nora.memory.domain.admission_proposals import PassContext
from nora.persons import Persons
from nora.platform.discord.bridge_app import DiscordBridge
from nora.platform.discord.command_acts import DiscordCommandActs
from nora.platform.discord.music_commands import register
from nora.policy import Policy
from nora.testing import FakeStreamRedis
from tests.contract.test_computer_event_store_contract import _event

PASS = PassContext(
    "unit-1", "room-1", False, "called", "engagement", "pass-1", "trace-1", "model-1",
    "code-1", "instruction-1", "skill-1", "config-1", "identity-1", "policy-1",
)


class _Revisions:
    def current(self):
        return {}


@pytest.fixture
async def acts(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    scope = RegistrationScope(Policy(()), Persons({}, {}))
    registration = SqlItemRegistration(scope, _Revisions())
    engine.update_execution_options(item_registration=registration)
    store = SqlActStore(engine, registration)
    try:
        yield ActEnvelope(store), engine
    finally:
        await engine.dispose()


def _act(guarantee="unknown", *, context=PASS):
    return Act(
        ActIdentity("fixture", context.pass_id if context else "event-1", 0),
        "delivery", "test-destination",
        ActAuthority("unit-1", "person-1", None, "room-1", context), guarantee,
        "generated-key" if guarantee == "idempotent_effect" else None,
    )


async def _rows(engine, table):
    async with engine.connect() as conn:
        return (await conn.execute(sa.select(table).order_by(table.c.id))).mappings().all()


@pytest.mark.parametrize("context", [PASS, None], ids=["pass", "deterministic"])
async def test_act_commits_before_the_call_and_refuses_its_identity_twice(acts, context):
    envelope, engine = acts
    act = _act(context=context)
    calls = []

    async def send(_key):
        rows = await _rows(engine, NORA_ACT)
        assert [row["id"] for row in rows] == [act.identity.id]
        assert await _rows(engine, NORA_ACT_OUTCOME) == []
        calls.append("fixture text")
        return ActInvocation(True, Outcome("done"))

    assert await envelope.invoke(act, send) == ActInvocation(True, Outcome("done"))
    assert await envelope.invoke(act, send) is None
    assert calls == ["fixture text"]
    row, = await _rows(engine, NORA_ACT)
    outcomes = await _rows(engine, NORA_ACT_OUTCOME)
    assert len(outcomes) == 1
    outcome, = outcomes
    registered = {r["id"]: (r["kind"], r["unit"]) for r in await _rows(engine, REGISTRY)}
    for item in (row, outcome):
        assert registered[item["graph_id"]] == ("item", "unit-1")
        assert [item[k] for k in ("writer", "pass", "occasion", "trace", "model")] == (
            ["engagement", "pass-1", "called", "trace-1", "model-1"] if context
            else ["fixture", None, None, None, None]
        )
    assert outcome["act_id"] == act.identity.id
    assert outcome["verdict"] == "done"


async def test_a_new_ordinal_sends_identical_content_to_the_same_destination(acts):
    envelope, engine = acts
    calls = []

    async def send(_key):
        calls.append("fixture text")
        return ActInvocation(True, Outcome("done"))

    act = _act()
    distinct = replace(act, identity=replace(act.identity, ordinal=1))
    await envelope.invoke(act, send)
    await envelope.invoke(distinct, send)
    assert calls == ["fixture text", "fixture text"]
    assert len(await _rows(engine, NORA_ACT_OUTCOME)) == 2


async def test_an_unscoped_act_and_outcome_are_kept_outside_the_registry(acts):
    envelope, engine = acts
    act = replace(_act(context=None), authority=ActAuthority(None))

    async def send(_key):
        return ActInvocation(True, Outcome("done"))

    assert await envelope.invoke(act, send) == ActInvocation(True, Outcome("done"))
    row, = await _rows(engine, NORA_ACT)
    outcome, = await _rows(engine, NORA_ACT_OUTCOME)
    assert outcome["act_id"] == row["id"] == act.identity.id
    for item in (row, outcome):
        assert item["unit"] is None and item["graph_id"] is None
    assert await _rows(engine, REGISTRY) == []


async def test_pass_expression_and_silence_are_unique_and_carry_no_hidden_reasoning(acts):
    envelope, engine = acts
    authority = ActAuthority(PASS.unit, room=PASS.room, pass_context=PASS)
    model_output = [
        {"role": "assistant", "content": [
            {"reasoningContent": {"reasoningText": {"text": "PRIVATE_BLOCK"}}},
            {"type": "reasoning", "text": "PRIVATE_TYPED"},
            {"text": "A draft.", "reasoning": "PRIVATE_FIELD"},
        ]},
        {"role": "user", "content": [{"text": "Fixture input"}]},
        {"role": "assistant", "content": [{"text": "A conclusion."}]},
    ]
    await asyncio.gather(*(envelope.record_expression(authority, model_output) for _ in range(2)))
    await asyncio.gather(*(envelope.record_silence(authority) for _ in range(2)))
    assert await envelope.read_pass(PASS.unit, PASS.pass_id) == {
        "expression": ["A draft.", "A conclusion."], "silence": None,
    }
    assert await envelope.read_pass("other-unit", PASS.pass_id) == {}
    rows = await _rows(engine, NORA_PASS_OUTPUT)
    assert len(rows) == 2 and all("PRIVATE_" not in repr(row) for row in rows)
    assert {row["trace"] for row in rows} == {PASS.trace}
    assert await _rows(engine, NORA_ACT) == []
    assert len(await _rows(engine, REGISTRY)) == 2


async def test_raw_redacted_thinking_never_reaches_the_pass_table(acts):
    envelope, engine = acts
    authority = ActAuthority(PASS.unit, room=PASS.room, pass_context=PASS)
    await envelope.record_expression(authority, [{"role": "assistant", "content": [
        {"type": "redacted_thinking", "text": "PRIVATE_REDACTED", "data": "opaque"},
        {"type": "text", "text": "Visible conclusion."},
    ]}])

    row, = await _rows(engine, NORA_PASS_OUTPUT)
    assert row["content"] == ["Visible conclusion."]


@pytest.mark.parametrize("kind", ["denial", "sync"])
async def test_unscoped_discord_writes_still_go_out_through_the_envelope(acts, kind, monkeypatch):
    envelope, engine = acts
    commands = DiscordCommandActs(envelope)
    if kind == "denial":
        bridge = DiscordBridge(
            api_token=str(1000000001), account_key="fixture", client=object(),
            command_acts=commands,
        )
        wire = AsyncMock()
        interaction = SimpleNamespace(
            id=100000000000000001, guild_id=100000000000000002,
            channel_id=100000000000000003, channel=SimpleNamespace(id=100000000000000003),
            user=SimpleNamespace(id=100000000000000004),
            response=SimpleNamespace(send_message=wire),
        )
        assert not await bridge._slash_admission(interaction)
        assert not await bridge._slash_admission(interaction)
        assert wire.await_args.kwargs == {"ephemeral": True}
        trigger = str(interaction.id)
    else:
        wire = AsyncMock()
        tree = SimpleNamespace(sync=wire, add_command=lambda command: None)
        monkeypatch.setattr("discord.app_commands.CommandTree", lambda client: tree)
        client = SimpleNamespace(ws=SimpleNamespace(session_id="ready-session"), application_id=1)
        await register(client, object(), command_acts=commands)
        await register(client, object(), command_acts=commands)
        trigger = "ready-session"
    wire.assert_awaited_once()
    row, = await _rows(engine, NORA_ACT)
    outcome, = await _rows(engine, NORA_ACT_OUTCOME)
    executor = f"discord.command_{kind}"
    assert row["trigger_id"] == trigger
    assert row["executor"] == row["writer"] == outcome["writer"] == executor
    assert row["guarantee"] == "unknown"
    assert outcome["verdict"] == "done"
    assert outcome["act_id"] == row["id"]
    for item in (row, outcome):
        assert item["unit"] is item["graph_id"] is item["pass"] is None
    assert await _rows(engine, REGISTRY) == []


class _LostFirstPublish(FakeStreamRedis):
    def __init__(self, accepted):
        super().__init__()
        self.accepted = accepted
        self.attempts = []

    async def xadd(self, stream, fields):
        self.attempts.append(fields["task"])
        if len(self.attempts) > 1:
            return await super().xadd(stream, fields)
        if self.accepted:
            await super().xadd(stream, fields)
        raise TimeoutError("fixture lost Redis acknowledgement")


@pytest.mark.parametrize("writes", [False, True], ids=["read", "write"])
@pytest.mark.parametrize("times_out", [False, True], ids=["success", "timeout"])
async def test_mounted_mcp_is_recorded_and_never_reinvoked(acts, writes, times_out, monkeypatch):
    envelope, engine = acts
    calls, changes = [], []
    advertised = Tool.model_validate({
        "name": "fixture_check", "description": "Check a fixture.",
        "inputSchema": {"type": "object", "properties": {}},
    })

    class Destination:
        async def call_tool_async(self, *, tool_use_id, **kwargs):
            calls.append(tool_use_id)
            rows = await _rows(engine, NORA_ACT)
            assert len(rows) == 1
            row, = rows
            assert row["executor"] == "mcp.invoke"
            if writes:
                changes.append("fixture only")
            if times_out:
                raise TimeoutError("fixture lost the tool result")
            return {"toolUseId": tool_use_id, "status": "success", "content": [{"text": "ok"}]}

    client = SimpleNamespace(
        start=lambda: None, list_tools_sync=lambda: [MCPAgentTool(advertised, Destination())],
    )
    monkeypatch.setattr(MCPClient, "load_servers", lambda *_: (client,))
    mount = McpMount("fixture", "http://localhost:1/mcp", "FIXTURE_TOKEN", 3)
    groups = build_mcp_tools(
        {mount.name: mount}, credentials={mount.token_env: "generated"},
    )
    tool, = aggregate_budgeted_tools(groups[mount.name], envelope)
    assert "annotations" not in tool.tool_spec
    use = {"name": "fixture_check", "toolUseId": "generated-use", "input": {}}
    with act_pass(ActAuthority(PASS.unit, pass_context=PASS)):
        if times_out:
            with pytest.raises(TimeoutError):
                _ = [event async for event in tool.stream(use, {})]
        else:
            result, = [event async for event in tool.stream(use, {})]
            assert result.tool_result["status"] == "success"
        duplicate, = [event async for event in tool.stream(use, {})]
        assert duplicate.tool_result["status"] == "error"
    assert calls == ["generated-use"]
    assert len(changes) == int(writes)
    act, = await _rows(engine, NORA_ACT)
    outcome, = await _rows(engine, NORA_ACT_OUTCOME)
    assert act["guarantee"] == "unknown" and act["act_kind"] == "effect"
    assert act["pass"] == outcome["pass"] == PASS.pass_id
    assert outcome["act_id"] == act["id"]
    assert outcome["verdict"] == ("uncertain" if times_out else "done")


@pytest.mark.parametrize("accepted", [True, False], ids=["lost-ack", "dropped"])
async def test_computer_dispatch_settles_with_the_same_job_key(acts, accepted, monkeypatch):
    envelope, engine = acts
    tasks = SqlComputerTaskStore(engine)
    redis = _LostFirstPublish(accepted)
    dispatch = ComputerTaskActs(redis, envelope)
    scope = "a1b2c3d4e5f6a7b8"
    now = datetime(2026, 2, 1, 12, tzinfo=UTC)
    ran = []
    release = asyncio.Event()

    async def execute(task):
        ran.append(task.id)
        await release.wait()
        return {"status": "ok", "answer": "generated"}

    def clock():
        return now

    async def quiet(_scope):
        return "quiet"

    admission = ComputerAdmission(
        tasks=tasks, redis=redis, clock=clock, acts=dispatch, reconcile=quiet,
    )
    workers = [
        ComputerQueueRunner(tasks=tasks, redis=redis, clock=clock, acts=dispatch,
                            dispatch={"native": execute}, reconcile=quiet)
        for _ in range(2)
    ]
    head = await admission.submit(scope=scope, kind="native", payload={}, origin={})
    queued = await admission.submit(scope=scope, kind="native", payload={}, origin={})
    assert head.granted and not queued.granted
    assert len(redis.streams.get(_stream(scope), [])) == int(accepted)
    now += timedelta(seconds=11)
    await workers[0].reconcile()
    assert len(redis.streams[_stream(scope)]) == 1 + int(accepted)
    assert redis.attempts == [str(queued.task.id)] * 2

    await admission.finish(head.task.id, status="done", result={})
    arrive = asyncio.Barrier(2)
    claim_head = tasks.try_claim_head

    async def contested_claim(task_id, *, now):
        assert task_id == queued.task.id
        await asyncio.wait_for(arrive.wait(), timeout=2)
        return await claim_head(task_id, now=now)

    monkeypatch.setattr(tasks, "try_claim_head", contested_claim)
    grants = await asyncio.gather(*(worker._dispatch_scope(scope) for worker in workers))
    release.set()
    await asyncio.gather(*(job for worker in workers for job in tuple(worker._watchers)))
    assert sorted(grants) == [0, 1]
    for _ in range(50):
        if (await tasks.get(queued.task.id)).status == "done":
            break
        await asyncio.sleep(0.01)
    assert ran == [queued.task.id]
    assert (await tasks.get(queued.task.id)).status == "done"
    published, = [r for r in await _rows(engine, NORA_ACT)
                  if r["idempotency_key"] == str(queued.task.id)]
    assert published["executor"] == "computer.dispatch"
    assert published["guarantee"] == "idempotent_effect"
    recorded = [r for r in await _rows(engine, NORA_ACT_OUTCOME) if r["act_id"] == published["id"]]
    assert [row["verdict"] for row in recorded] == ["uncertain", "done"]
    assert recorded[-1]["result"]["answer"] == "generated"


@pytest.mark.parametrize(("status", "verdict"), [
    ("done", "done"), ("failed", "failed"), ("cancelled", "failed"), (None, "uncertain"),
])
async def test_delegated_job_results_cite_the_dispatch_act(acts, status, verdict):
    envelope, engine = acts
    store = SqlComputerTaskStore(engine)
    redis = FakeStreamRedis()
    now = datetime(2026, 2, 1, tzinfo=UTC)
    dispatch = ComputerTaskActs(redis, envelope)

    async def quiet(_scope):
        return "quiet"

    admission = ComputerAdmission(
        tasks=store, redis=redis, clock=lambda: now, reconcile=quiet, acts=dispatch,
    )
    with act_pass(ActAuthority(PASS.unit, room=PASS.room, pass_context=PASS)):
        launched = await admission.submit(
            scope="a1b2c3d4e5f6a7b8", kind="native", payload={}, origin={},
        )
    await SqlComputerEventStore(engine).append(_event(
        operation_id=launched.task.id, conversation_id=PASS.room, tag_id=PASS.pass_id,
        result={"visible": "generated", "reasoning": "PRIVATE_EVENT"},
    ))
    if status is None:
        restarted = ComputerTaskActs(redis, envelope)
        await restarted.observe_job(str(launched.task.id), None)
    else:
        await admission.finish(
            launched.task.id, status=status,
            result={"answer": "generated", "reasoning": "PRIVATE_JOB"},
        )
    act, = await _rows(engine, NORA_ACT)
    assert act["act_kind"] == "delegation" and act["idempotency_key"] == str(launched.task.id)
    event, = await _rows(engine, COMPUTER_EVENTS)
    assert event["act_id"] == act["id"] and event["unit"] == PASS.unit
    assert event["pass"] == PASS.pass_id and event["trace"] == PASS.trace
    assert event["result"] == {"visible": "generated"}
    rows = [r for r in await _rows(engine, NORA_ACT_OUTCOME) if r["act_id"] == act["id"]]
    assert rows[-1]["verdict"] == verdict
    if status == "done":
        assert rows[-1]["result"] == {"answer": "generated"}


@pytest.mark.parametrize(("guarantee", "read_result", "expected"), [
    ("unknown", None, "uncertain"),
    ("read_back", "done", "done"),
    ("read_back", "failed", "failed"),
    ("idempotent_effect", None, "done"),
])
async def test_timeout_settles_under_the_executor_guarantee(acts, guarantee, read_result, expected):
    envelope, engine = acts
    act = _act(guarantee)
    called = []
    read = []
    effects = set()

    async def send(key):
        called.append(key)
        if read_result != "failed":
            effects.add(key)
        if len(called) == 1:
            raise TimeoutError("generated lost acknowledgement")
        return ActInvocation(True, Outcome("done"))

    async def read_back(identity):
        read.append(identity)
        return Outcome("done" if effects else "failed")

    with pytest.raises(TimeoutError):
        await envelope.invoke(act, send)
    row, = await _rows(engine, NORA_ACT_OUTCOME)
    assert row["verdict"] == "uncertain"
    assert await envelope.invoke(act, send) is None
    assert await envelope.settle(act, read_back=read_back, replay=send) == Outcome(expected)
    assert read == ([act.identity.id] if guarantee == "read_back" else [])
    assert called == [act.key] * (2 if guarantee == "idempotent_effect" else 1)
    if guarantee == "idempotent_effect":
        assert effects == {"generated-key"}
    assert [(r["act_id"], r["verdict"]) for r in await _rows(engine, NORA_ACT_OUTCOME)] == [
        (act.identity.id, verdict)
        for verdict in (["uncertain"] if expected == "uncertain" else ["uncertain", expected])
    ]
