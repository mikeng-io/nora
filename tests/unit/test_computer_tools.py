"""The computer tools (DEC-243):
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

import asyncio
import hashlib
import inspect
from datetime import UTC, datetime, timedelta

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.computer import (
    ComputerToolSettings,
    RunResult,
    SandboxStatus,
    computer_scope,
)
from nora.domain.models import PersonRef
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeBlobStore,
    FakeComputerEventStore,
    FakeRosterSource,
    FakeSandboxRuntime,
)

GROUP = "g-computer"
OTHER_GROUP = "g-elsewhere"
TOOLS = ("computer_run", "computer_read", "computer_write", "computer_edit")


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.miles]\nfirst_name = "Miles"\n[[persons.miles.signal]]\n'
                    'phone = "+85290000101"\n')
    return load_persons(path)


_UNSET = object()


def _tools(persons, *, computer=None, events=None, settings=None, metrics=None,
           conversation_id=GROUP, blobs=_UNSET, tagger=None, fire_turn=False,
           dream_adaptor=""):
    """The four closures. `blobs` defaults to a fresh `FakeBlobStore` whenever
    a runtime is given (the computer runs only with its full audit wired)."""
    if blobs is _UNSET:
        blobs = FakeBlobStore() if computer is not None else None
    room = Room(
        platform="signal", conversation_id=conversation_id,
        roster_source=FakeRosterSource(groups={conversation_id: ()}),
        persons=persons, self_ids=frozenset({"+85290000100"}), tagger=tagger,
    )
    specs = build_tool_specs(
        store=None, conversation_id=conversation_id, conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=metrics if metrics is not None else Metrics(),
        room=room, transit_source=None, blob_store=blobs,
        fire_turn=fire_turn, dream_adaptor=dream_adaptor,
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


def _failures(metrics: Metrics, tool: str, stage: str) -> float:
    return metrics.get("nora_computer_audit_failures_total", tool=tool, stage=stage)


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


@pytest.mark.parametrize("wired", ["nothing", "runtime_without_row_store",
                                   "runtime_without_blob_store"])
async def test_off_is_unavailable_with_a_note_for_every_tool(persons, wired):
    runtime = None if wired == "nothing" else FakeSandboxRuntime()
    events = FakeComputerEventStore() if wired == "runtime_without_blob_store" else None
    tools = _tools(persons, computer=runtime, events=events, blobs=None)
    for call, kwargs in _calls(tools):
        out = await call(**kwargs)
        assert out["status"] == "unavailable"
        assert out["note"].strip()
    # A runtime without its full audit runs nothing: no call reached it.
    assert runtime is None or runtime.run_calls == []


async def test_off_still_records_both_rows_of_every_call(persons):
    events = FakeComputerEventStore()
    tools = _tools(persons, events=events)
    for call, kwargs in _calls(tools):
        await call(**kwargs)
    assert [(e.tool, e.phase, e.status) for e in events.events] == [
        (t, phase, status) for t in TOOLS
        for phase, status in (("started", "started"), ("finished", "unavailable"))
    ]
    assert all(e.result["reason"] == "off" for e in events.finished())


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
    [row] = events.finished()
    assert row.status == "unavailable"
    assert (row.result["exit_code"], row.result["duration_ms"]) == (-9, 5000)


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


async def test_two_rows_per_call_on_every_path_even_when_the_computer_is_down(persons):
    runtime = FakeSandboxRuntime()
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=runtime, events=events)
    for call, kwargs in _calls(tools):  # up: ok / nothing_found paths
        await call(**kwargs)
    runtime.down = True
    for call, kwargs in _calls(tools):  # down: unavailable on every tool
        out = await call(**kwargs)
        assert out["status"] == "unavailable"
    assert [(e.tool, e.phase) for e in events.events] == [
        (t, phase) for t in list(TOOLS) * 2 for phase in ("started", "finished")
    ]
    assert [e.status for e in events.finished()[4:]] == ["unavailable"] * 4
    by_operation: dict = {}
    for e in events.events:
        by_operation.setdefault(e.operation_id, []).append(e.phase)
    assert all(phases == ["started", "finished"] for phases in by_operation.values())


async def test_the_audit_row_records_who_when_how_and_what(persons):
    runtime = FakeSandboxRuntime()
    command = "python3 -c 'print(\"x\" * 50000)' # " + "long " * 400
    runtime.script(command, RunResult(SandboxStatus.OK, exit_code=0, stdout="x" * 50_000,
                                      stderr="warn", duration_ms=40))
    events = FakeComputerEventStore()
    blobs = FakeBlobStore()
    tagger = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
    tools = _tools(persons, computer=runtime, events=events, blobs=blobs, tagger=tagger,
                   settings=ComputerToolSettings(max_output_chars=1000))
    bind_tag_id("tag-42")
    try:
        shown = await tools["computer_run"](command=command)
    finally:
        clear_tag_id()
    assert len(shown["stdout"]) == 1000  # the model's view stays bounded
    start, row = events.events
    # the started row: written first, the full request, no result yet
    assert (start.phase, start.status, start.finished_at) == ("started", "started", None)
    assert start.operation_id == row.operation_id
    assert start.request["command"] == command
    assert row.request == {}
    # who
    assert (row.actor, row.requester_person_id, row.tag_id) == ("tag", "miles", "tag-42")
    assert (row.platform, row.conversation_id, row.conversation_type) == ("signal", GROUP, "group")
    assert row.scope == computer_scope("signal", GROUP)
    # when
    assert start.started_at == row.started_at <= row.finished_at <= row.created_at
    # how
    assert (row.tool, row.path) == ("computer_run", "direct")
    assert (row.trace_id, row.span_id) == ("", "")  # no span in this test
    # what: the WHOLE output behind a blob key
    assert row.result["exit_code"] == 0
    assert row.result["stdout_chars"] == 50_000
    stdout_ref = row.output_ref["stdout"]
    assert blobs.blobs[stdout_ref["key"]] == b"x" * 50_000
    assert stdout_ref["sha256"] == hashlib.sha256(b"x" * 50_000).hexdigest()
    assert blobs.blobs[row.output_ref["stderr"]["key"]] == b"warn"


async def test_the_actor_follows_the_turns_provenance(persons):
    events = FakeComputerEventStore()
    for kwargs, actor in (
        ({}, "tag"), ({"fire_turn": True}, "fire"),
        ({"dream_adaptor": "proactive"}, "dreamer:proactive"),
    ):
        tools = _tools(persons, computer=FakeSandboxRuntime(), events=events, **kwargs)
        await tools["computer_run"](command="true")
        assert {e.actor for e in events.events[-2:]} == {actor}


async def test_a_read_stores_the_content_it_received_and_says_it_was_cut(persons):
    """computerd answers at most the limit the tool asks for, so the blob holds
    exactly what the client received and `truncated` records that the file
    had more (store what was received, never pretend it is all)."""
    events = FakeComputerEventStore()
    blobs = FakeBlobStore()
    tools = _tools(persons, computer=FakeSandboxRuntime(), events=events, blobs=blobs,
                   settings=ComputerToolSettings(max_read_chars=100))
    await tools["computer_write"](path="big.txt", content="z" * 5000)
    await tools["computer_read"](path="big.txt")
    read_row = events.finished()[-1]
    assert (read_row.result["content_chars"], read_row.result["truncated"]) == (100, True)
    assert blobs.blobs[read_row.output_ref["content"]["key"]] == b"z" * 100
    assert read_row.result["sha256"] == hashlib.sha256(b"z" * 5000).hexdigest()


async def test_a_write_and_an_edit_record_the_full_input_and_before_and_after(persons):
    events = FakeComputerEventStore()
    blobs = FakeBlobStore()
    tools = _tools(persons, computer=FakeSandboxRuntime(), events=events, blobs=blobs)
    first, second = "hello " * 100, "hello " * 100 + "again"
    await tools["computer_write"](path="a.txt", content=first)
    await tools["computer_write"](path="a.txt", content=second)
    old = "hello " * 60
    await tools["computer_edit"](path="a.txt", old=old, new="bye")
    new_file, overwrite, edit_row = events.finished()
    started = [e for e in events.events if e.phase == "started"]
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731
    assert blobs.blobs[started[1].request["content"]["key"]] == second.encode()
    assert (new_file.result["before_sha256"], new_file.result["after_sha256"]) == (
        None, sha(first),
    )
    assert (overwrite.result["before_sha256"], overwrite.result["after_sha256"]) == (
        sha(first), sha(second),
    )
    after_edit = second.replace(old, "bye", 1)
    assert started[2].request == {"path": "a.txt", "old": old, "new": "bye"}
    assert (edit_row.result["before_sha256"], edit_row.result["after_sha256"]) == (
        sha(second), sha(after_edit),
    )


async def test_a_call_whose_started_row_fails_is_refused_and_never_runs(persons):
    runtime = FakeSandboxRuntime()
    events = FakeComputerEventStore()
    events.fail = {"started"}
    metrics = Metrics()
    tools = _tools(persons, computer=runtime, events=events, metrics=metrics)
    for call, kwargs in _calls(tools):
        out = await call(**kwargs)
        assert out["status"] == "unavailable"
        assert out["note"].strip()
    assert runtime.run_calls == []
    assert runtime.files_by_scope == {}
    assert _failures(metrics, "computer_run", "row") == 1


async def test_a_write_whose_input_cannot_be_kept_is_refused(persons):
    runtime = FakeSandboxRuntime()
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=runtime, events=events,
                   blobs=FakeBlobStore(fail_on_put=True))
    out = await tools["computer_write"](path="a.txt", content="important")
    assert out["status"] == "unavailable"
    assert runtime.files_by_scope == {}
    assert events.events == []


async def test_a_failed_finished_row_still_returns_the_result_and_is_counted(persons):
    runtime = FakeSandboxRuntime()
    runtime.script("echo hi", RunResult(SandboxStatus.OK, exit_code=0, stdout="hi\n"))
    events = FakeComputerEventStore()
    events.fail = {"finished"}
    metrics = Metrics()
    tools = _tools(persons, computer=runtime, events=events, metrics=metrics)
    out = await tools["computer_run"](command="echo hi")
    assert out["status"] == "ok"
    assert out["stdout"] == "hi\n"
    assert _failures(metrics, "computer_run", "row") == 1


async def test_a_failed_blob_put_still_returns_the_result_and_the_row_says_so(persons):
    runtime = FakeSandboxRuntime()
    runtime.script("echo hi", RunResult(SandboxStatus.OK, exit_code=0, stdout="hi\n"))
    events = FakeComputerEventStore()
    metrics = Metrics()
    tools = _tools(persons, computer=runtime, events=events, metrics=metrics,
                   blobs=FakeBlobStore(fail_on_put=True))
    out = await tools["computer_run"](command="echo hi")
    assert out["status"] == "ok"
    assert out["stdout"] == "hi\n"
    assert _failures(metrics, "computer_run", "blob") == 1
    [row] = events.finished()
    assert row.output_ref["stdout"]["stored"] is False
    assert row.output_ref["stdout"]["sha256"] == hashlib.sha256(b"hi\n").hexdigest()


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
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=runtime, events=events)
    await tools["computer_run"](command="echo 'a\x00b'")
    assert events.events[0].request["command"] == "echo 'a\\x00b'"



# -- cancellation and crashes ------------------------------------------------


async def _cancel_mid_run(persons, events):
    runtime = FakeSandboxRuntime()
    runtime.hold = asyncio.Event()
    tools = _tools(persons, computer=runtime, events=events)
    task = asyncio.create_task(tools["computer_run"](command="sleep 999"))
    for _ in range(100):
        if runtime.run_calls:
            break
        await asyncio.sleep(0)
    assert runtime.run_calls, "the run never reached the computer"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_call_cancelled_mid_run_leaves_a_started_and_a_cancelled_row(persons):
    """The tag loop's deadline cancels the turn mid-call. The trail still
    shows the call began and how it ended, and the cancellation propagates."""
    events = FakeComputerEventStore()
    await _cancel_mid_run(persons, events)
    start, end = events.events
    assert (start.phase, end.phase) == ("started", "finished")
    assert start.operation_id == end.operation_id
    assert start.request["command"] == "sleep 999"
    assert end.status == "cancelled"
    assert end.result["status"] == "cancelled"
    assert "cancelled" in end.result["reason"]
    assert end.finished_at is not None


async def test_a_started_row_with_no_finished_row_is_found_as_an_orphan(persons):
    """A crash between the two rows leaves a started row alone: the visible
    mark the operator's query finds. Simulated by failing the finished
    append, which is what a restart mid-call looks like in the table."""
    events = FakeComputerEventStore()
    events.fail = {"finished"}
    tools = _tools(persons, computer=FakeSandboxRuntime(), events=events)
    await tools["computer_run"](command="echo crashed")
    events.fail = set()
    await tools["computer_run"](command="echo fine")
    far = datetime.now(UTC) + timedelta(days=1)
    [orphan] = await events.orphaned_starts(older_than=far, limit=10)
    assert orphan.request["command"] == "echo crashed"
    assert await events.orphaned_starts(older_than=datetime(2020, 1, 1, tzinfo=UTC),
                                        limit=10) == []


async def test_the_row_records_the_status_the_model_was_told(persons):
    """A run has nothing to find: a `nothing_found` from the port reaches the
    model as `unavailable`, and the row says `unavailable` too."""
    runtime = FakeSandboxRuntime()
    runtime.script("x", RunResult(SandboxStatus.NOTHING_FOUND, reason="odd"))
    events = FakeComputerEventStore()
    tools = _tools(persons, computer=runtime, events=events)
    out = await tools["computer_run"](command="x")
    [row] = events.finished()
    assert out["status"] == row.status == row.result["status"] == "unavailable"


async def test_a_second_cancellation_cannot_interrupt_the_finished_row(persons):
    """The turn is cancelled mid-call, and cancelled AGAIN while the
    `finished` row is being written. The write is shielded, so it still
    lands once the store answers; only the wait for it was cut short."""
    runtime = FakeSandboxRuntime()
    runtime.hold = asyncio.Event()
    events = FakeComputerEventStore()
    events.hold_finished = asyncio.Event()
    tools = _tools(persons, computer=runtime, events=events)
    task = asyncio.create_task(tools["computer_run"](command="sleep 999"))
    for _ in range(100):
        if runtime.run_calls:
            break
        await asyncio.sleep(0)
    task.cancel()          # the deadline: into the finally, the append starts
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()          # a second cancel lands on the in-flight append
    with pytest.raises(asyncio.CancelledError):
        await task
    events.hold_finished.set()
    for _ in range(10):
        await asyncio.sleep(0)
    [end] = events.finished()
    assert end.status == "cancelled"
