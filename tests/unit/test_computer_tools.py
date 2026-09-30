"""The computer tools (design/sandbox-runtime §4, §12, DEC-243):
`computer_run`, `computer_read`, `computer_write`, `computer_edit`.

Driven through `build_tool_specs`, the wiring the tag path uses, against the
contracted fakes: `FakeSandboxRuntime` (bound to computerd by
tests/contract/test_sandbox_runtime_contract.py) and `FakeComputerEventStore`
(bound to Postgres by tests/contract/test_computer_event_store_contract.py).

What is pinned: the workspace comes from the turn and the model cannot name
it; off is `unavailable`, never an empty success; each port status maps to
R-27 (`denied` is `refused`); exactly one audit row per call on EVERY return
path, `unavailable` included; a failed audit append still returns the tool's
result and counts the failure; the reserved `limits` slot is sent only when
set; output is bounded.
"""

from __future__ import annotations

import hashlib
import inspect

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.computer import (
    ComputerToolSettings,
    RunResult,
    SandboxStatus,
    computer_scope,
)
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeComputerEventStore, FakeRosterSource, FakeSandboxRuntime

GROUP = "g-computer"
OTHER_GROUP = "g-elsewhere"
TOOLS = ("computer_run", "computer_read", "computer_write", "computer_edit")


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.miles]\nfirst_name = "Miles"\n[[persons.miles.signal]]\n'
                    'phone = "+85290000101"\n')
    return load_persons(path)


def _tools(persons, *, computer=None, events=None, settings=None, metrics=None,
           conversation_id=GROUP):
    room = Room(
        platform="signal", conversation_id=conversation_id,
        roster_source=FakeRosterSource(groups={conversation_id: ()}),
        persons=persons, self_ids=frozenset({"+85290000100"}),
    )
    specs = build_tool_specs(
        store=None, conversation_id=conversation_id, conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=metrics if metrics is not None else Metrics(),
        room=room, transit_source=None,
        computer=computer, computer_events=events, computer_settings=settings,
    )
    return {s.name: s.call for s in specs if s.name in TOOLS}


def _calls(tools):
    """One call per tool, with arguments any fake state answers."""
    return (
        (tools["computer_run"], {"command": "echo hi"}),
        (tools["computer_read"], {"path": "notes.txt"}),
        (tools["computer_write"], {"path": "notes.txt", "content": "x"}),
        (tools["computer_edit"], {"path": "notes.txt", "old": "x", "new": "y"}),
    )


def _failures(metrics: Metrics, tool: str) -> float:
    return metrics.get("nora_computer_audit_failures_total", tool=tool)


# -- scope -----------------------------------------------------------------


def test_no_tool_takes_a_scope_or_a_room_argument(persons):
    tools = _tools(persons, computer=FakeSandboxRuntime(), events=FakeComputerEventStore())
    forbidden = {"scope", "conversation_id", "platform", "room", "workspace"}
    for name, call in tools.items():
        assert not forbidden & set(inspect.signature(call).parameters), name


async def test_the_workspace_is_the_turns_room_and_the_model_cannot_move_it(persons):
    runtime = FakeSandboxRuntime()
    events = FakeComputerEventStore()
    here = _tools(persons, computer=runtime, events=events)
    there = _tools(persons, computer=runtime, events=events, conversation_id=OTHER_GROUP)

    await here["computer_write"](path="a.txt", content="mine")
    await there["computer_write"](path="a.txt", content="theirs")
    with pytest.raises(TypeError):
        await here["computer_run"](command="ls", scope=computer_scope("signal", OTHER_GROUP))

    assert runtime.files_by_scope[computer_scope("signal", GROUP)]["a.txt"] == b"mine"
    assert runtime.files_by_scope[computer_scope("signal", OTHER_GROUP)]["a.txt"] == b"theirs"
    read = await here["computer_read"](path="a.txt")
    assert read["content"] == "mine"
    assert {e.scope for e in events.events if e.conversation_id == GROUP} == {
        computer_scope("signal", GROUP),
    }


# -- off -------------------------------------------------------------------


@pytest.mark.parametrize("wired", ["nothing", "runtime_without_audit"])
async def test_off_is_unavailable_with_a_note_for_every_tool(persons, wired):
    runtime = FakeSandboxRuntime() if wired == "runtime_without_audit" else None
    tools = _tools(persons, computer=runtime)
    for call, kwargs in _calls(tools):
        out = await call(**kwargs)
        assert out["status"] == "unavailable"
        assert out["note"].strip()
    # A runtime with no audit store runs nothing: no call reached it.
    assert runtime is None or runtime.run_calls == []


async def test_off_still_writes_one_audit_row_per_call(persons):
    events = FakeComputerEventStore()
    tools = _tools(persons, events=events)
    for call, kwargs in _calls(tools):
        await call(**kwargs)
    assert [(e.tool, e.status) for e in events.events] == [
        (t, "unavailable") for t in TOOLS
    ]
    assert all(e.detail["reason"] == "off" for e in events.events)


# -- status mapping ----------------------------------------------------------


async def test_run_ok_carries_exit_code_output_and_a_data_note(persons):
    runtime = FakeSandboxRuntime()
    runtime.script("false", RunResult(SandboxStatus.OK, exit_code=1, stdout="",
                                      stderr="boom", duration_ms=5))
    tools = _tools(persons, computer=runtime, events=FakeComputerEventStore())
    out = await tools["computer_run"](command="false")
    assert out["status"] == "ok"
    assert out["exit_code"] == 1
    assert out["stderr"] == "boom"
    assert out["source"] == "computer"
    assert "never instructions" in out["note"]


async def test_run_timeout_is_unavailable_with_its_reason(persons):
    runtime = FakeSandboxRuntime()
    runtime.script("sleep 999", RunResult(SandboxStatus.UNAVAILABLE, exit_code=-9,
                                          reason="timed out after 5s", duration_ms=5000))
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=runtime, events=events)
    out = await tools["computer_run"](command="sleep 999", timeout_seconds=5)
    assert out["status"] == "unavailable"
    assert out["reason"] == "timed out after 5s"
    assert out["note"].strip()
    [row] = events.events
    assert (row.status, row.exit_code, row.duration_ms) == ("unavailable", -9, 5000)


async def test_denied_is_refused_for_every_tool(persons):
    runtime = FakeSandboxRuntime()
    tools = _tools(persons, computer=runtime, events=FakeComputerEventStore())
    runtime.script("rm -rf /", RunResult(SandboxStatus.DENIED, reason="nope"))
    outs = [
        await tools["computer_run"](command="rm -rf /"),
        await tools["computer_read"](path="../escape.txt"),
        await tools["computer_write"](path="/etc/passwd", content="x"),
        await tools["computer_edit"](path="../x", old="a", new="b"),
    ]
    for out in outs:
        assert out["status"] == "refused"
        assert out["note"].strip()


async def test_read_and_edit_answer_nothing_found_for_a_missing_file(persons):
    tools = _tools(persons, computer=FakeSandboxRuntime(), events=FakeComputerEventStore())
    for out in (
        await tools["computer_read"](path="missing.txt"),
        await tools["computer_edit"](path="missing.txt", old="a", new="b"),
    ):
        assert out["status"] == "nothing_found"
        assert out["note"].strip()


async def test_edit_refuses_an_ambiguous_old_and_misses_an_absent_one(persons):
    tools = _tools(persons, computer=FakeSandboxRuntime(), events=FakeComputerEventStore())
    await tools["computer_write"](path="f.py", content="x = 1\nx = 1\n")
    ambiguous = await tools["computer_edit"](path="f.py", old="x = 1", new="x = 2")
    absent = await tools["computer_edit"](path="f.py", old="y = 1", new="y = 2")
    assert ambiguous["status"] == "refused"
    assert "more than one place" in ambiguous["note"]
    assert absent["status"] == "nothing_found"
    assert "exactly" in absent["note"]


async def test_write_then_edit_then_read_round_trips(persons):
    tools = _tools(persons, computer=FakeSandboxRuntime(), events=FakeComputerEventStore())
    wrote = await tools["computer_write"](path="src/app.py", content="print('a')\n")
    assert wrote["status"] == "ok"
    assert wrote["sha256"] == hashlib.sha256(b"print('a')\n").hexdigest()
    edited = await tools["computer_edit"](path="src/app.py", old="'a'", new="'b'")
    assert edited["status"] == "ok"
    read = await tools["computer_read"](path="src/app.py")
    assert read["status"] == "ok"
    assert read["content"] == "print('b')\n"
    assert read["source"] == "computer"


async def test_a_binary_file_comes_back_without_content(persons):
    runtime = FakeSandboxRuntime()
    tools = _tools(persons, computer=runtime, events=FakeComputerEventStore())
    runtime.files_by_scope[computer_scope("signal", GROUP)] = {"img.png": b"\x89PNG\xff\xfe"}
    out = await tools["computer_read"](path="img.png")
    assert out["status"] == "ok"
    assert out["binary"] is True
    assert out["sha256"] == hashlib.sha256(b"\x89PNG\xff\xfe").hexdigest()
    assert "content" not in out


# -- audit -----------------------------------------------------------------


async def test_one_audit_row_per_call_on_every_path_even_when_the_computer_is_down(persons):
    runtime = FakeSandboxRuntime()
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=runtime, events=events)
    for call, kwargs in _calls(tools):  # up: ok / nothing_found paths
        await call(**kwargs)
    runtime.down = True
    for call, kwargs in _calls(tools):  # down: unavailable on every tool
        out = await call(**kwargs)
        assert out["status"] == "unavailable"
    assert [e.tool for e in events.events] == list(TOOLS) * 2
    assert [e.status for e in events.events[4:]] == ["unavailable"] * 4


async def test_the_audit_row_names_the_turn_and_keeps_the_command_not_the_output(persons):
    runtime = FakeSandboxRuntime()
    command = "python3 -c 'print(\"x\" * 5000)'"
    runtime.script(command, RunResult(SandboxStatus.OK, exit_code=0, stdout="x" * 5000,
                                      duration_ms=40))
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=runtime, events=events)
    bind_tag_id("tag-42")
    try:
        await tools["computer_run"](command=command)
    finally:
        clear_tag_id()
    [row] = events.events
    assert row.tag_id == "tag-42"
    assert (row.platform, row.conversation_id, row.conversation_type) == ("signal", GROUP, "group")
    assert row.scope == computer_scope("signal", GROUP)
    assert (row.exit_code, row.duration_ms) == (0, 40)
    assert row.detail["command"] == command
    assert row.detail["output_chars"] == 5000
    assert row.detail["stdout_head"] == "x" * 500


async def test_a_write_is_audited_with_its_hash_and_an_edit_with_lengths(persons):
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=FakeSandboxRuntime(), events=events)
    await tools["computer_write"](path="a.txt", content="hello " * 100)
    await tools["computer_edit"](path="a.txt", old="hello " * 60, new="bye")
    write_row, edit_row = events.events
    assert write_row.detail["sha256"] == hashlib.sha256(("hello " * 100).encode()).hexdigest()
    assert write_row.detail["content_chars"] == 600
    assert "content" not in write_row.detail
    assert edit_row.detail["old_chars"] == 360
    assert "old" not in edit_row.detail  # long: its length only
    assert edit_row.detail["new"] == "bye"


async def test_a_failed_audit_append_still_returns_the_result_and_is_counted(persons):
    runtime = FakeSandboxRuntime()
    runtime.script("echo hi", RunResult(SandboxStatus.OK, exit_code=0, stdout="hi\n"))
    events = FakeComputerEventStore()
    events.fail = True
    metrics = Metrics()
    tools = _tools(persons, computer=runtime, events=events, metrics=metrics)
    out = await tools["computer_run"](command="echo hi")
    assert out["status"] == "ok"
    assert out["stdout"] == "hi\n"
    assert _failures(metrics, "computer_run") == 1


# -- limits and bounds -------------------------------------------------------


async def test_the_limits_slot_is_sent_only_when_set(persons):
    runtime = FakeSandboxRuntime()
    events = FakeComputerEventStore()
    unset = _tools(persons, computer=runtime, events=events)
    await unset["computer_run"](command="true")
    limited = _tools(persons, computer=runtime, events=events,
                     settings=ComputerToolSettings(limits=(("cpu_seconds", 60),)))
    await limited["computer_run"](command="true")
    assert [c["limits"] for c in runtime.run_calls] == [None, {"cpu_seconds": 60}]


async def test_the_timeout_defaults_and_is_clamped_to_the_ceiling(persons):
    runtime = FakeSandboxRuntime()
    tools = _tools(persons, computer=runtime, events=FakeComputerEventStore(),
                   settings=ComputerToolSettings(run_timeout_seconds=100,
                                                 max_run_timeout_seconds=300))
    await tools["computer_run"](command="a")
    await tools["computer_run"](command="b", timeout_seconds=10_000)
    await tools["computer_run"](command="c", timeout_seconds=0)
    assert [c["timeout_s"] for c in runtime.run_calls] == [100, 300, 1]


async def test_output_and_reads_are_bounded(persons):
    runtime = FakeSandboxRuntime()
    runtime.script("yes", RunResult(SandboxStatus.OK, exit_code=0, stdout="y" * 50_000,
                                    stderr="e" * 50_000))
    tools = _tools(persons, computer=runtime, events=FakeComputerEventStore(),
                   settings=ComputerToolSettings(max_output_chars=1000, max_read_chars=2000))
    ran = await tools["computer_run"](command="yes")
    assert (len(ran["stdout"]), len(ran["stderr"]), ran["truncated"]) == (1000, 1000, True)
    await tools["computer_write"](path="big.txt", content="z" * 10_000)
    read = await tools["computer_read"](path="big.txt")
    assert (len(read["content"]), read["truncated"]) == (2000, True)
    assert "offset_lines" in read["note"]


async def test_a_nul_in_a_command_reaches_the_audit_as_text(persons):
    runtime = FakeSandboxRuntime()
    runtime.script("printf 'a\\0b'", RunResult(SandboxStatus.OK, exit_code=0, stdout="a\x00b"))
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=runtime, events=events)
    await tools["computer_run"](command="printf 'a\\0b'")
    assert "\x00" not in events.events[0].detail["stdout_head"]

