"""Generated regressions for scoped browser recovery, audit and delivery."""

import asyncio
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nora.agent import browse as browse_module
from nora.agent.browse import BrowseAgentRunner, BrowseSettings
from nora.app import computer_queue as queue_module
from nora.app.computer_queue import ComputerAdmission, ComputerQueueRunner
from nora.domain.computer import BrowseOutcome, SandboxStatus, StateResult, anything_running
from nora.domain.computer_context import ComputerContextCaps
from nora.engagement.computer_context import ComputerContextReader
from nora.language.computer_render import render_computer
from nora.persons import load_persons
from nora.testing import (
    FakeComputerEventStore,
    FakeComputerSessionStore,
    FakeComputerTaskStore,
    FakeStreamRedis,
)
from tests.unit.test_browse_agent import FakePage, _named
from tests.unit.test_computer_browse_tool import _browse
from tests.unit.test_computerd_processes import UID, _proc
from tests.unit.test_scoped_computer import NOW, UNIT_SCOPE, _register, _wired, clock

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "generated"  # noqa: S105 - synthetic local guest credential


def guest():
    spec = importlib.util.spec_from_file_location(
        "r4_computerd", ROOT / "deploy/computer/computerd.py"
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


@pytest.mark.asyncio
async def test_managed_browser_tree_does_not_hide_native_work(tmp_path, monkeypatch):
    m = guest()
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("btime 1000\n")
    _proc(proc, 101, argv=["google-chrome"], comm="chrome")
    _proc(proc, 102, argv=["google-chrome", "--type=renderer"], ppid=101, comm="chrome")
    _proc(proc, 103, argv=["Xvfb"], comm="Xvfb")
    browser = m.Browser(UNIT_SCOPE, 0)
    browser.chrome = SimpleNamespace(pid=101, returncode=None)
    browser.xvfb = SimpleNamespace(pid=103)
    computer = m.Computer(
        root=tmp_path / "ws",
        token=TOKEN,
        isolate_scopes=False,
        tmux_root=tmp_path / "tm",
        proc_root=proc,
    )
    browsers = m.Browsers(computer, idle_reap=False)
    browsers.running[UNIT_SCOPE] = browser
    remaining = m.scope_processes(proc, UID, 1001, exclude=browsers.pids(UNIT_SCOPE))
    assert remaining == []
    _proc(proc, 104, argv=["generated-job"], comm="job")
    remaining = m.scope_processes(proc, UID, 1001, exclude=browsers.pids(UNIT_SCOPE))
    assert [p["pid"] for p in remaining] == [104]

    async def reconcile(scope):
        state = StateResult(
            status=SandboxStatus.OK,
            tmux=(),
            processes=tuple(remaining),
            runs=0,
            browser={"running": True},
        )
        return "busy" if anything_running(state) else "quiet"

    monkeypatch.setattr(queue_module, "DRAIN_SECONDS", 0.001)
    store = FakeComputerTaskStore()
    redis = FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock(), reconcile=reconcile)
    got = await admission.submit(scope=UNIT_SCOPE, kind="browse", payload={}, origin={})

    async def timed_out():
        return {"status": "unavailable", "uncertain": True, "reason": "ran out of time"}

    await admission.run_segment(got.task, timed_out())
    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), executors=admission.executors, reconcile=reconcile
    )
    for _ in range(3):
        await runner.tick()
    assert (await store.get(got.task.id)).status == "running"
    maintenance = await admission.submit(
        scope=UNIT_SCOPE, kind="maintenance", payload={}, origin={}
    )
    assert not maintenance.granted
    # Positive control: with only managed roots there is no job.
    (proc / "104" / "cmdline").unlink()
    assert m.scope_processes(proc, UID, 1001, exclude=browsers.pids(UNIT_SCOPE)) == []
    remaining.clear()
    for _ in range(3):
        await runner.tick()
    assert (await store.get(got.task.id)).status == "unavailable"
    assert (await store.get(maintenance.task.id)).status == "cancelled"
    cleanup = await admission.submit(scope=UNIT_SCOPE, kind="maintenance", payload={}, origin={})
    assert cleanup.granted


@pytest.mark.asyncio
async def test_context_joins_browser_handle_to_execution_segment(monkeypatch):
    store = FakeComputerTaskStore()
    redis = FakeStreamRedis()
    rig = _wired(monkeypatch, tasks=store)
    _register(rig.runtime, scope=UNIT_SCOPE)
    runner = BrowseAgentRunner(
        model_source=None,
        retry=None,
        base_url="http://generated",
        token=TOKEN,
        settings=BrowseSettings(),
        manager=rig.manager,
    )

    async def drive(self, page, objective, start_url, task_id="", tab="", record=None):
        return BrowseOutcome(status="ok", answer="generated", task_id=task_id, tab=tab)

    monkeypatch.setattr(BrowseAgentRunner, "_drive", drive)
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    segment = await admission.submit(
        scope=UNIT_SCOPE, kind="browse", payload={"tool": "computer_browse", "args": {}}, origin={}
    )

    async def work():
        got = await runner.browse(scope=UNIT_SCOPE, objective="generated")
        return {"status": got.status, "task": got.task_id, "tab": got.tab}

    reply = await admission.run_segment(segment.task, work())
    assert reply["task"] != str(segment.task.id)
    sessions = FakeComputerSessionStore()
    events = FakeComputerEventStore()
    import uuid

    from nora.domain.computer_events import ComputerEvent

    await events.append(
        ComputerEvent(
            id=uuid.uuid4(),
            operation_id=uuid.uuid4(),
            phase="finished",
            created_at=NOW,
            started_at=NOW,
            finished_at=NOW,
            actor="tag",
            requester_person_id="generated",
            platform="signal",
            conversation_id="generated-room",
            conversation_type="group",
            tag_id="",
            scope=UNIT_SCOPE,
            tool="computer_browse",
            path="direct",
            trace_id="",
            span_id="",
            status="ok",
        )
    )
    reader = ComputerContextReader(
        sessions=sessions,
        events=events,
        runtime=rig.runtime,
        caps=ComputerContextCaps(),
        clock=clock(),
        metrics=None,
        unit_for=lambda p, c: "unit-x",
        tasks=store,
        tabs=rig.tabs,
    )
    view = await reader.read(platform="signal", conversation_id="generated-room")
    assert (
        view.tabs
        and view.tabs[0].last_op == "computer_browse"
        and view.tabs[0].last_result == "done"
    )
    assert "browse" in render_computer(view).body
    handle = __import__("uuid").UUID(reply["task"])
    for tool in ("computer_browse", "computer_capture"):
        next_segment = await admission.submit(
            scope=UNIT_SCOPE, kind="browse", payload={"tool": tool, "args": {}}, origin={}
        )

        async def continued(tool=tool):
            if tool == "computer_capture":
                image = await rig.manager.capture(UNIT_SCOPE, handle, full_page=False)
                return {"status": "ok", "path": image.path}
            outcome = await runner.browse(scope=UNIT_SCOPE, objective="continue", task_id=handle)
            return {"status": outcome.status, "task": outcome.task_id}

        result = await admission.run_segment(next_segment.task, continued())
        assert result["status"] == "ok"
        view = await reader.read(platform="signal", conversation_id="generated-room")
        assert view.tabs[0].task_id == str(handle)
        assert view.tabs[0].last_op == tool
        assert view.tabs[0].last_result == "done"


@pytest.mark.asyncio
async def test_action_rows_have_distinct_keys_and_preserve_tool_finish(tmp_path, monkeypatch):
    identity = tmp_path / "generated-identity.toml"
    identity.write_text('[persons.generated]\nfirst_name="Generated"\n')
    persons = load_persons(identity)

    async def run(self, scope, objective, start_url, task_id):
        task = browse_module._Task(
            FakePage(), BrowseSettings(), record=browse_module._action_record.get()
        )
        tools = _named(task)
        await tools["page_open"](url="https://example.invalid/")
        await tools["page_read"]()
        await tools["page_snapshot"]()
        await tools["page_click"](ref="e99")
        await tools["page_open"](url="file:///generated")
        return BrowseOutcome(status="ok", answer="generated result", steps=4)

    monkeypatch.setattr(BrowseAgentRunner, "_run", run)
    runner = BrowseAgentRunner(
        model_source=None,
        retry=None,
        base_url="http://generated",
        token=TOKEN,
        settings=BrowseSettings(),
    )
    events = FakeComputerEventStore()
    reply = await _browse(persons, browser=runner, events=events)(objective="generated")
    assert reply["status"] == "ok"
    actions = [e for e in events.events if e.tool == "computer_browse.action"]
    assert [a.request["action"] for a in actions] == [
        "page_open",
        "page_read",
        "page_snapshot",
        "page_click",
        "page_open",
    ]
    assert len({a.operation_id for a in actions}) == 5
    assert [a.result["outcome"] for a in actions] == [
        "executed",
        "executed",
        "executed",
        "error",
        "rejected",
    ]
    assert actions[3].request["ref"] == "e99"
    assert actions[3].request["observation"] == 1
    whole = [e for e in events.events if e.tool == "computer_browse"]
    assert [e.phase for e in whole] == ["started", "finished"]
    assert all(a.request["parent_operation_id"] == str(whole[0].operation_id) for a in actions)


def test_manifest_preserves_guest_owners_and_verifies_restore(tmp_path):
    script = (ROOT / "deploy/computer/nora-computer").read_text()
    helpers = []
    for name in ("scope_manifest_tar", "scope_manifest_dir", "scope_manifest", "scope_verify_tar"):
        start = script.index(name + "() {")
        end = script.index("\n}\n", start) + 2
        helpers.append(script[start:end])
    helper = tmp_path / "helpers.sh"
    helper.write_text("\n".join(helpers))
    archive = tmp_path / "generated.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = b"generated bytes"
        member = tarfile.TarInfo("./file.txt")
        member.uid = 1357
        member.gid = 2468
        member.size = len(data)
        member.mode = 0o640
        tf.addfile(member, io.BytesIO(data))
    record = tmp_path / "record.json"
    record.write_text(json.dumps({"instance": "generated-boot"}))
    with helper.open("a") as out:
        out.write(
            "\nscope_valid() { return 0; }\n"
            "scope_state() { echo stopped; }\n"
            "scope_sandbox() { echo generated-box; }\n"
            'scope_record() { echo "$generated_record"; }\n'
            "record_event() { :; }\n"
        )
    manifest = Path(str(archive) + ".manifest.json")
    subprocess.run(  # noqa: S603 - fixed helper and generated temporary archives
        [
            "bash",
            "-c",
            'source "$1"; generated_record="$4"; scope_manifest "$2" "$3"',
            "generated",
            str(helper),
            UNIT_SCOPE,
            str(archive),
            str(record),
        ],
        check=True,
        capture_output=True,
    )
    spec = json.loads(manifest.read_text())["files"][0]
    # Guest ownership is read from archive metadata before any host extraction.
    assert (spec["uid"], spec["gid"], spec["mode"]) == (1357, 2468, 0o640)
    verified = subprocess.run(  # noqa: S603 - fixed helper and generated temporary archives
        [
            "bash",
            "-c",
            'source "$1"; scope_verify_tar "$2" "$3"',
            "generated",
            str(helper),
            str(manifest),
            str(archive),
        ],
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0 and json.loads(verified.stdout)["verified"]
    spec["uid"] = 9999
    manifest.write_text(json.dumps({"files": [spec]}))
    rejected = subprocess.run(  # noqa: S603 - fixed helper and generated temporary archives
        [
            "bash",
            "-c",
            'source "$1"; scope_verify_tar "$2" "$3"',
            "generated",
            str(helper),
            str(manifest),
            str(archive),
        ],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 3 and json.loads(rejected.stdout)["wrong_owner"] == ["file.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_terminal", [False, True])
async def test_retry_retains_consumed_marker_until_delivery_stamp_persists(fail_terminal):
    store = FakeComputerTaskStore()
    redis = FakeStreamRedis()
    admission = ComputerAdmission(
        tasks=store, redis=redis, clock=clock(), reconcile=lambda scope: asyncio.sleep(0, "quiet")
    )
    got = await admission.submit(
        scope=UNIT_SCOPE,
        kind="native",
        payload={},
        origin={
            "platform": "signal",
            "conversation_id": "generated-room",
            "requester_person_id": "generated",
        },
    )
    finish = store.finish

    async def fail_finish(*a, **k):
        raise OSError("generated finish outage")

    if fail_terminal:
        store.finish = fail_finish
    mark_delivered = store.mark_delivered

    async def fail_stamp(*a, **k):
        raise OSError("generated delivery-stamp outage")

    store.mark_delivered = fail_stamp

    async def work():
        return {"status": "ok", "answer": "already-consumed-generated-answer"}

    reply = await admission.run_segment(got.task, work())
    # No durable disposition exists (finish and/or delivery stamp refused):
    # the live handoff cannot be committed — the caller gets `unavailable`
    # with the task's identity, NEVER the result body, and the original
    # outcome stays journaled unconsumed for its one deferred delivery.
    assert reply["status"] == "unavailable"
    assert reply["task"] == str(got.task.id)
    assert "answer" not in reply
    pending = admission.executors.pending_result(got.task.id)
    assert pending is not None and not pending.consumed
    store.finish = finish
    from nora.app.computer_queue import deliver_computer_task
    from nora.testing import FakeComputeJobStore

    jobs = FakeComputeJobStore()

    async def deliver(row):
        await deliver_computer_task(row, jobs=jobs, tasks=store, clock=clock())

    runner = ComputerQueueRunner(
        tasks=store,
        redis=redis,
        clock=clock(),
        executors=admission.executors,
        reconcile=lambda scope: asyncio.sleep(0, "quiet"),
        on_terminal=deliver,
    )
    # The deferred path IS the single handoff for a caller told
    # `unavailable`: the job is filed once (deduped by backend_job_id) even
    # while the stamp keeps failing — it is never suppressed.
    for _ in range(3):
        await runner.tick()
    pending_jobs = await jobs.pending(limit=10)
    assert len(pending_jobs) == 1
    assert pending_jobs[0].backend_job_id == str(got.task.id)
    store.mark_delivered = mark_delivered
    await runner.tick()
    assert (await store.get(got.task.id)).delivered_at is not None
    # Still exactly one job — recovery re-stamps, never re-files.
    assert len(await jobs.pending(limit=10)) == 1
    polled = await queue_module.ComputerTaskBackend(tasks=store).poll(job_id=str(got.task.id))
    assert "already-consumed-generated-answer" in polled.report
    assert admission.executors.pending_result(got.task.id) is None

    # A genuinely unconsumed terminal result still receives exactly one outbox handoff.
    other = await admission.submit(
        scope=UNIT_SCOPE,
        kind="native",
        payload={"tool": "computer_run"},
        origin={"platform": "signal", "conversation_id": "generated-room"},
    )
    await store.finish(
        other.task.id, status="done", now=NOW, result={"status": "ok", "stdout": "generated-output"}
    )
    await runner.tick()
    await runner.tick()
    pending_jobs = await jobs.pending(limit=10)
    assert {j.backend_job_id for j in pending_jobs} == {
        str(got.task.id), str(other.task.id)}
    polled = await queue_module.ComputerTaskBackend(tasks=store).poll(job_id=str(other.task.id))
    assert "generated-output" in polled.report
