"""Deferred computer tools reach the REAL formatted model request.

End to end through `nora.agent.factory` and a real Strands agent: the first
OpenAI-formatted request carries only resident tools + `load_tools` and the
deferred catalogue in the prompt; scripted `load_tools` replies load the
computer verbs from the shelf onto the agent's own registry; subsequent
formatted requests contain their real schemas. A wrong name answers
`nothing_found` (negative), and `computer_write` then executes for real
against the contract-bound `FakeSandboxRuntime` on the turn's bound scope.

Zero external or paid calls: `httpx.MockTransport` answers every SDK request
locally and is the ONLY request path. The four SDK requests are SYNTHETIC —
they prove schema/request formatting, not zero model activity.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import COMPUTER_TOOLS, LOADER_TOOL
from nora.app.computer_queue import ComputerAdmission
from nora.domain.computer import ComputerToolSettings, computer_unit_scope
from nora.domain.computer_leases import ComputerLeases
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeBlobStore,
    FakeBrowserRunner,
    FakeComputerEventStore,
    FakeComputerExportStore,
    FakeComputerSessionStore,
    FakeComputerTaskStore,
    FakeRosterSource,
    FakeSandboxRuntime,
    FakeStreamRedis,
)

NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
UNIT = "unit-x"
LOAD_NAMES = ["computer_browse", "computer_write",
              "computer_capture", "computer_export"]


def _sse(delta, finish, usage=(12, 3, 15)):
    chunks = [
        {"id": "generated", "object": "chat.completion.chunk",
         "model": "generated-model",
         "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"id": "generated", "object": "chat.completion.chunk",
         "model": "generated-model",
         "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
         "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                   "total_tokens": usage[2]}},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _tool_call(call_id: str, name: str, arguments: dict | str):
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"role": "assistant", "tool_calls": [
        {"index": 0, "id": call_id, "type": "function",
         "function": {"name": name, "arguments": args}}]}


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.miles]\nfirst_name = "Miles"\n'
                    '[[persons.miles.signal]]\nphone = "+85290000101"\n')
    return load_persons(path)


def _rig(persons):
    room = Room(
        platform="signal", conversation_id="g-computer",
        roster_source=FakeRosterSource(groups={"g-computer": ()}),
        persons=persons, self_ids=frozenset({"+85290000100"}))
    runtime = FakeSandboxRuntime()
    registry = ToolRegistry(
        unit_for=lambda platform, cid: UNIT,
        blobs=FakeBlobStore(),
        computer=runtime,
        computer_events=FakeComputerEventStore(),
        computer_settings=ComputerToolSettings(),
        computer_sessions=FakeComputerSessionStore(),
        computer_leases=ComputerLeases(),
        computer_exports=FakeComputerExportStore(),
        browser=FakeBrowserRunner(),
        computer_admission=ComputerAdmission(
            tasks=FakeComputerTaskStore(), redis=FakeStreamRedis(),
            clock=lambda: NOW,
            reconcile=lambda scope: asyncio.sleep(0, "quiet")),
        browser_manager=object(),
        computer_dm_scope_for=lambda platform, cid: "dm-anchor-1",
        artifact_link_base="https://portal.test/",
    )
    surface = registry.surface(
        store=None, conversation_id="g-computer", conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=Metrics(), room=room, defer=True)
    return room, registry, runtime, surface


async def test_deferred_computer_schemas_reach_the_real_request(persons):
    """Req1: no computer schema resident, `load_tools` is, catalogue in the
    prompt. `load_tools(["computer_browse","computer_wrong"])` loads one and
    names the other `unknown`; a second call loads the remaining three — req3
    carries all four schemas. `computer_write` then runs against the runtime
    and lands bytes on the bound scope."""
    _room, registry, runtime, surface = _rig(persons)
    assert surface.shelf.names() >= COMPUTER_TOOLS
    catalogue = surface.prompt_catalogue
    assert all(n in catalogue for n in LOAD_NAMES)

    requests: list[dict] = []

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        n = len(requests)
        names = {t["function"]["name"] for t in body.get("tools", [])}
        tool_msgs = [m for m in body["messages"] if m.get("role") == "tool"]
        if n == 1:
            assert not {x for x in names if x.startswith("computer_")}
            assert LOADER_TOOL in names
            prompt = " ".join(str(m.get("content", "")) for m in body["messages"])
            for name in LOAD_NAMES:
                assert name in prompt  # deferred catalogue reached the prompt
            delta = _tool_call("call_load1", LOADER_TOOL,
                               {"names": ["computer_browse", "computer_wrong"]})
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=_sse(delta, "tool_calls"))
        if n == 2:
            assert "computer_browse" in names
            assert "computer_write" not in names  # not loaded yet
            result = json.loads(tool_msgs[-1]["content"])
            assert result["loaded"] == ["computer_browse"]
            assert result["unknown"] == ["computer_wrong"]
            delta = _tool_call("call_load2", LOADER_TOOL,
                               {"names": ["computer_write", "computer_capture",
                                          "computer_export"]})
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=_sse(delta, "tool_calls"))
        if n == 3:
            assert set(LOAD_NAMES) <= names  # every loaded schema in the request
            delta = _tool_call(
                "call_write", "computer_write",
                '{"path":"report.csv","content":"a,b\\n1,2\\n"}')
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=_sse(delta, "tool_calls"))
        assert n == 4
        result = json.loads(tool_msgs[-1]["content"])
        assert result["status"] == "ok" and result["path"] == "report.csv"
        delta = {"role": "assistant", "content": "done"}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse(delta, "stop"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client = factory.create_client(
            base_url="https://generated.invalid/v1",
            api_key="generated-private-auth-value", http_client=transport)
        model = factory.create_model_provider(
            model="generated-model", max_tokens=100, temperature=0,
            client=client, tool_choice="auto")
        agent = factory.create_agent(
            model=model,
            system_prompt="You are Nora.\n\n" + catalogue,
            tools=surface.tools, pre_fill=[],
            guard=ReplyGuard(conversation_id="g-computer",
                             budgets=registry.budgets(),
                             terminal=registry.terminal_tools(),
                             metrics=Metrics()),
            tool_hooks=surface.hooks, trace_attributes={},
            retry=factory.build_retry_policy(timeout_seconds=5))
        result = await factory.invoke(
            agent, "write the generated csv and export it",
            invocation_state={}, limits=factory.build_limits(
                turns=8, output_tokens=400, total_tokens=4000))
        await client.close()
        assert "done" in str(result)

    assert len(requests) == 4  # synthetic SDK requests — not model evidence
    scope = computer_unit_scope(UNIT)
    assert list(runtime.files_by_scope.get(scope, {}).values()) == [b"a,b\n1,2\n"]
