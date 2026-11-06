"""The PR-584 Astra readiness regressions — ported from the audit probes.

Each test names the property the audit measured red at `9a23e22`, plus the
positive controls: scope exclusion while the guest is busy, single
live/deferred delivery disposition, live-only nested inheritance, journal
lock release, deferred-dispatch re-admission, openerId popup attribution,
fail-closed capture, and measured-not-capped vision budgets. Generated
values only; no production fixtures.
"""
import asyncio
import base64
import contextlib
import gc
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from nora.agent.registry import ToolRegistry
from nora.agent.run_budget_transport import _BoundedTransport
from nora.app.composition import _note_unit_of
from nora.app.computer_dispatch import ComputerToolDispatch
from nora.app.computer_queue import (
    ComputerAdmission,
    ComputerQueueRunner,
    SegmentRegistry,
    current_segment,
    deliver_computer_task,
)
from nora.domain.browser_tasks import BrowserTab
from nora.domain.computer import computer_unit_scope
from nora.domain.computer_vms import ScopedEndpoint
from nora.domain.run_budget import RunBudget, RunBudgetError
from nora.infra.computer.browser import BrowserError, BrowserPage
from nora.infra.computer.manager import ScopeBrowserManager, TabUnavailable
from nora.infra.computer.scoped import ScopeBinding
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import Conversation, Policy
from nora.testing import (
    FakeBlobStore,
    FakeBrowserTabStore,
    FakeComputeJobStore,
    FakeComputerEventStore,
    FakeComputerTaskStore,
    FakeSandboxRuntime,
    FakeStreamRedis,
)

NOW = datetime(2026, 2, 1, tzinfo=UTC)
SCOPE = "a1b2c3d4e5f6a7b8"
pytestmark = pytest.mark.asyncio


async def test_answered_native_work_must_hold_scope_while_guest_is_busy():
    store = FakeComputerTaskStore()
    observations = []

    async def busy(scope):
        observations.append(scope)
        return "busy"

    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(),
                                  clock=lambda: NOW, reconcile=busy)
    first = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    assert first.granted

    async def answered():
        return {"status": "ok", "exit_code": 0, "stdout": "detached job started"}

    await admission.run_segment(first.task, answered())
    second = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    assert not second.granted, (
        f"successor granted={second.granted}; guest probes={observations}")


async def test_running_owner_blocks_same_scope_and_allows_other_scope():
    store = FakeComputerTaskStore()
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(),
                                  clock=lambda: NOW)
    first = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    second = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    other = await admission.submit(scope="f6e5d4c3b2a19876",
                                   kind="native", payload={}, origin={})
    assert first.granted and not second.granted and other.granted


async def test_live_result_and_deferred_outbox_must_not_both_win(monkeypatch):
    jobs, redis = FakeComputeJobStore(), FakeStreamRedis()
    store = FakeComputerTaskStore(outbox=jobs)
    # `quiet` reconcile: the guest confirmed stopped, so the terminal row
    # commits — the interleaving under test is AFTER that commit.
    async def quiet(scope):
        return "quiet"

    admission = ComputerAdmission(tasks=store, redis=redis, clock=lambda: NOW,
                                  reconcile=quiet)
    persisted, release = asyncio.Event(), asyncio.Event()
    original = store.finish

    async def after_commit(task_id, **kwargs):
        await original(task_id, **kwargs)
        persisted.set()
        await release.wait()  # the post-commit/pre-stamp scheduling point

    monkeypatch.setattr(store, "finish", after_commit)

    async def deliver(row):
        await deliver_computer_task(row, jobs=jobs, tasks=store, clock=lambda: NOW)

    runner = ComputerQueueRunner(tasks=store, redis=redis, clock=lambda: NOW,
                                 executors=admission.executors,
                                 on_terminal=deliver)
    first = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={
        "platform": "signal", "conversation_id": "generated-audit-room",
        "conversation_type": "group", "requester_person_id": "generated-person"})

    async def answered():
        return {"status": "ok", "answer": "original-result"}

    live = asyncio.create_task(admission.run_segment(first.task, answered()))
    try:
        await asyncio.wait_for(persisted.wait(), 2)
        await runner._deliver_pending()
    finally:
        release.set()
        answer = await asyncio.wait_for(live, 2)
    # The live half's own stamp (`run_segment`'s internal `delivered`) won
    # the election — it returned the result — so the deferred outbox,
    # run both DURING the pre-stamp window and after, files nothing.
    await runner._deliver_pending()
    outbox = await jobs.kind_since(kind="computer", since=NOW)
    assert not (answer.get("answer") == "original-result" and outbox), (
        f"live={answer}; deferred_job_count={len(outbox)}")


async def test_terminal_segment_cannot_authorize_new_nested_work():
    store = FakeComputerTaskStore()
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(),
                                  clock=lambda: NOW)
    first = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    await store.finish(first.task.id, status="done", result={}, now=NOW)
    token = current_segment.set(first.task.id)
    try:
        inherited = await admission.submit(scope=SCOPE, kind="native",
                                           payload={}, origin={})
    finally:
        current_segment.reset(token)
    assert not inherited.inherited, "terminal parent still grants nested execution"


async def test_live_parent_still_inherits_nested_work():
    """The positive control: a RUNNING parent with a live executor keeps
    its nested inheritance — the refusal is for dead authority, not for
    nesting."""
    store = FakeComputerTaskStore()
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(),
                                  clock=lambda: NOW)
    first = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    assert first.granted
    admission.executors.register(first.task.id, asyncio.current_task())
    try:
        token = current_segment.set(first.task.id)
        try:
            nested = await admission.submit(scope=SCOPE, kind="native",
                                            payload={}, origin={})
        finally:
            current_segment.reset(token)
    finally:
        admission.executors.unregister(first.task.id, asyncio.current_task())
    assert nested.inherited


async def test_a_running_but_unowned_segment_cannot_authorize_nested_work():
    """A segment id pointing at a RUNNING row this registry does not own —
    another process's live segment — authorizes nothing here: the child
    falls to its own admission, and the running row holds the scope."""
    store = FakeComputerTaskStore()
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(),
                                  clock=lambda: NOW)
    first = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    assert first.granted
    await store.claim(first.task.id, now=NOW)  # running — but no executor here
    token = current_segment.set(first.task.id)
    try:
        nested = await admission.submit(scope=SCOPE, kind="native",
                                        payload={}, origin={}, wait_seconds=0.0)
    finally:
        current_segment.reset(token)
    assert not nested.inherited and not nested.granted, (
        "an unowned running segment still granted nested execution")


async def test_abandoned_registry_releases_lifetime_lock(tmp_path):
    owner = SegmentRegistry(pending_dir=tmp_path / "journal")
    fd = owner._lock_fd
    del owner
    gc.collect()
    replacement = None
    try:
        replacement = SegmentRegistry(pending_dir=tmp_path / "journal")
    finally:
        if replacement is not None and replacement._lock_fd is not None:
            os.close(replacement._lock_fd)
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)


def _authority_harness(tmp_path, *, requester: str):
    """The deferred-dispatch rig: a generated policy admitting
    `remaining-person` in one group room, a queue that wrote the row under
    `requester`, and a dispatcher bound to the CURRENT policy and scope
    resolver — as composition wires it."""
    identity = tmp_path / f"identity-{requester}.toml"
    identity.write_text('[persons.revoked]\nfirst_name = "Generated"\n'
                        '[[persons.revoked.signal]]\nphone = "+85290000101"\n')
    persons = load_persons(identity)
    cid, unit = "generated-audit-room", "generated-unit"
    convo = Conversation(platform="signal", ids=frozenset({cid}),
                         persons=frozenset({"remaining-person"}),
                         label="Generated", unit=unit)
    policy = Policy(conversations=(convo,), _by_id={("signal", cid): convo})
    tasks, runtime = FakeComputerTaskStore(), FakeSandboxRuntime()
    admission = ComputerAdmission(tasks=tasks, redis=FakeStreamRedis(),
                                  clock=lambda: NOW)
    registry = ToolRegistry(unit_for=_note_unit_of(policy),
                            blobs=FakeBlobStore(),
                            computer=runtime,
                            computer_events=FakeComputerEventStore(),
                            computer_admission=admission, clock=lambda: NOW)
    dispatch = ComputerToolDispatch(
        registry=registry, store=None, persons=persons, honcho=None,
        metrics=Metrics(), policy=policy,
        scope_for=lambda p, c, t: computer_unit_scope(unit))
    return admission, dispatch, tasks, runtime, cid, unit


async def test_deferred_native_write_rechecks_revoked_requester(tmp_path):
    admission, dispatch, _tasks, runtime, cid, unit = _authority_harness(
        tmp_path, requester="revoked")
    row = (await admission.submit(
        scope=computer_unit_scope(unit), kind="native",
        payload={"tool": "computer_write",
                 "args": {"path": "revoked.txt", "content": "generated-bytes"}},
        origin={"platform": "signal", "conversation_id": cid,
                "conversation_type": "group",
                "requester_person_id": "revoked", "actor": "tag"})).task
    token = current_segment.set(row.id)
    try:
        result = await dispatch(row)
    finally:
        current_segment.reset(token)
    assert result["status"] in {"unavailable", "refused"} \
        and result.get("no_guest_effect") and not runtime.files_by_scope, (
            f"revoked requester ran anyway: {result}; "
            f"scopes_written={list(runtime.files_by_scope)}")


async def test_deferred_native_write_runs_for_currently_allowed_requester(tmp_path):
    """The positive control: the requester the CURRENT policy still
    admits reaches the tool — re-admission refuses stale authority, it
    does not refuse work."""
    admission, dispatch, _tasks, runtime, cid, unit = _authority_harness(
        tmp_path, requester="remaining-person")
    row = (await admission.submit(
        scope=computer_unit_scope(unit), kind="native",
        payload={"tool": "computer_write",
                 "args": {"path": "allowed.txt", "content": "generated-bytes"}},
        origin={"platform": "signal", "conversation_id": cid,
                "conversation_type": "group",
                "requester_person_id": "remaining-person",
                "actor": "tag"})).task
    # The runner's real shape: the claimed row's executor is registered
    # for the segment's length, so the spec's own submit inherits it.
    admission.executors.register(row.id, asyncio.current_task())
    token = current_segment.set(row.id)
    try:
        result = await dispatch(row)
    finally:
        current_segment.reset(token)
        admission.executors.unregister(row.id, asyncio.current_task())
    assert result["status"] == "ok", f"allowed requester refused: {result}"
    assert runtime.files_by_scope.get(computer_unit_scope(unit)), (
        "the deferred write never reached the guest")


async def test_deferred_task_refused_when_scope_moved(tmp_path):
    """A row queued under one scope executes nowhere else: the resolver's
    CURRENT answer is the authority, not the row's. The room and requester
    stay admitted — ONLY the scope moved — so the refusal is the scope
    check's, not an earlier gate's."""
    admission, _dispatch, _tasks, runtime, cid, unit = _authority_harness(
        tmp_path, requester="remaining-person")
    row = (await admission.submit(
        scope=computer_unit_scope(unit), kind="native",
        payload={"tool": "computer_write",
                 "args": {"path": "moved.txt", "content": "generated-bytes"}},
        origin={"platform": "signal", "conversation_id": cid,
                "conversation_type": "group",
                "requester_person_id": "remaining-person",
                "actor": "tag"})).task
    # Same policy, same room — the ONLY thing that moved is the scope the
    # resolver now answers, so refusal can only come from the scope check.
    convo = Conversation(platform="signal", ids=frozenset({cid}),
                         persons=frozenset({"remaining-person"}),
                         label="Generated", unit=unit)
    moved = ComputerToolDispatch(
        registry=ToolRegistry(unit_for=_note_unit_of(
            Policy(conversations=(convo,), _by_id={("signal", cid): convo})),
            blobs=FakeBlobStore(), computer=runtime,
            computer_events=FakeComputerEventStore(),
            computer_admission=admission, clock=lambda: NOW),
        store=None, persons=_dispatch._persons, honcho=None,
        metrics=Metrics(),
        policy=Policy(conversations=(convo,), _by_id={("signal", cid): convo}),
        scope_for=lambda p, c, t: "other-scope")
    admission.executors.register(row.id, asyncio.current_task())
    token = current_segment.set(row.id)
    try:
        result = await moved(row)
    finally:
        current_segment.reset(token)
        admission.executors.unregister(row.id, asyncio.current_task())
    assert result["status"] == "unavailable" and result.get("no_guest_effect")
    assert not runtime.files_by_scope


async def test_retained_tab_popup_is_not_adopted_by_another_active_task():
    scope, instance = SCOPE, "generated-boot"
    a, b = uuid.uuid4(), uuid.uuid4()
    tabs = FakeBrowserTabStore()
    for owner, target in ((a, "target-a"), (b, "target-b")):
        await tabs.bind(BrowserTab(
            id=uuid.uuid4(), scope=scope, task_id=owner,
            instance=instance, target_id=target, status="open",
            created_at=NOW, last_activity_at=NOW))
    manager = ScopeBrowserManager(runtime=None, tabs=tabs,
                                  token="generated-only",  # noqa: S106 - a generated test token
                                  clock=lambda: NOW,
                                  idle_after=timedelta(minutes=10))
    binding = ScopeBinding(
        endpoint=ScopedEndpoint(scope=scope,
                                base_url="http://generated.invalid"),
        runtime=None, instance=instance)

    class TargetCdp:
        async def call(self, method, params=None):
            if method == "Target.getTargets":
                return {"targetInfos": [
                    {"type": "page", "targetId": "target-a"},
                    {"type": "page", "targetId": "target-b"},
                    {"type": "page", "targetId": "popup-a",
                     "openerId": "target-a"},
                ]}
            if method == "Target.closeTarget":
                return {"success": True}
            raise AssertionError(method)

    await manager._reconcile(scope, binding, TargetCdp(), NOW, owner=b)
    popup = [r for r in await tabs.open_for_scope(scope, instance=instance)
             if r.target_id == "popup-a"]
    assert popup and popup[0].task_id == a, (
        "popup from retained task A was not bound to its actual opener")


async def test_unbound_popup_is_closed_not_attributed():
    """A target with no bound opener has no honest owner: closed, never
    parked on whichever task happened to be active."""
    scope, instance = SCOPE, "generated-boot"
    tabs = FakeBrowserTabStore()
    a = uuid.uuid4()
    await tabs.bind(BrowserTab(
        id=uuid.uuid4(), scope=scope, task_id=a,
        instance=instance, target_id="target-a", status="open",
        created_at=NOW, last_activity_at=NOW))
    manager = ScopeBrowserManager(runtime=None, tabs=tabs,
                                  token="generated-only",  # noqa: S106 - a generated test token
                                  clock=lambda: NOW,
                                  idle_after=timedelta(minutes=10))
    binding = ScopeBinding(
        endpoint=ScopedEndpoint(scope=scope,
                                base_url="http://generated.invalid"),
        runtime=None, instance=instance)
    closed: list[str] = []

    class TargetCdp:
        async def call(self, method, params=None):
            if method == "Target.getTargets":
                return {"targetInfos": [
                    {"type": "page", "targetId": "target-a"},
                    {"type": "page", "targetId": "popup-unknown"},
                ]}
            if method == "Target.closeTarget":
                closed.append(params["targetId"])
                return {"success": True}
            raise AssertionError(method)

    await manager._reconcile(scope, binding, TargetCdp(), NOW, owner=a)
    rows = await tabs.open_for_scope(scope, instance=instance)
    assert closed == ["popup-unknown"] and not [
        r for r in rows if r.target_id == "popup-unknown"]


async def test_reconcile_getTargets_failure_fails_closed():
    """An unreadable target list is not proof of quiet: the reconcile
    surfaces unavailable instead of silently retiring or adopting."""
    scope, instance = SCOPE, "generated-boot"
    tabs = FakeBrowserTabStore()
    manager = ScopeBrowserManager(runtime=None, tabs=tabs,
                                  token="generated-only",  # noqa: S106 - a generated test token
                                  clock=lambda: NOW,
                                  idle_after=timedelta(minutes=10))
    binding = ScopeBinding(
        endpoint=ScopedEndpoint(scope=scope,
                                base_url="http://generated.invalid"),
        runtime=None, instance=instance)

    class DeadCdp:
        async def call(self, method, params=None):
            raise BrowserError("the browser did not answer")

    with pytest.raises(TabUnavailable):
        await manager._reconcile(scope, binding, DeadCdp(), NOW, owner=None)


class _ScreenshotCdp:
    """Local protocol stub: only the requested screenshot method is accepted."""

    def __init__(self, data):
        self.data = data
        self.calls = []

    async def call(self, method, params=None, **kwargs):
        self.calls.append(method)
        if method != "Page.captureScreenshot":
            raise BrowserError(f"unexpected method: {method}")
        return {"data": self.data}


@pytest.mark.parametrize("payload", ["", " "])
async def test_empty_capture_is_unavailable_not_successful_png(payload):
    # "" fails the absent-data guard; " " base64-decodes to zero bytes and
    # must fail the empty-frame guard — both refuse, neither is a picture.
    cdp = _ScreenshotCdp(payload)
    page = BrowserPage(cdp=cdp, session_id="synthetic-session",
                       target_id="synthetic-tab")
    with pytest.raises(BrowserError):
        await page.capture_image()


async def test_undecodable_capture_is_unavailable_not_successful_png():
    cdp = _ScreenshotCdp("!!!not-base64!!!")
    page = BrowserPage(cdp=cdp, session_id="synthetic-session",
                       target_id="synthetic-tab")
    with pytest.raises(BrowserError):
        await page.capture_image()


async def test_capture_positive_png_preserves_bytes():
    # Generated 1x1 PNG; no captured or production pixels.
    encoded = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
               "+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    cdp = _ScreenshotCdp(encoded)
    page = BrowserPage(cdp=cdp, session_id="synthetic-session",
                       target_id="synthetic-tab")
    data, media, partial = await page.capture_image()
    assert data == base64.b64decode(encoded) and media == "image/png" and not partial
    assert cdp.calls == ["Page.captureScreenshot"]


async def test_budget_accepts_vision_when_input_is_measured_not_capped():
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": 17, "completion_tokens": 2}})

    budget = RunBudget(max_requests=1, max_output_tokens=20)
    async with httpx.AsyncClient(transport=_BoundedTransport(
            httpx.MockTransport(respond), budget)) as client:
        await client.post(
            "https://synthetic.invalid/v1/chat/completions", json={
                "max_tokens": 10,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "generated screenshot"},
                    {"type": "image_url",
                     "image_url": {"url": "data:image/png;base64,AA=="}},
                ]}]})
    assert len(seen) == 1 and budget.input_tokens == 17 \
        and budget.output_tokens == 2
    budget.finished()


async def test_unsupported_budget_shape_cannot_leave_run_successful():
    budget = RunBudget(max_requests=1, max_output_tokens=20)

    def forbidden(request):
        raise AssertionError("unsupported body reached transport")

    async with httpx.AsyncClient(transport=_BoundedTransport(
            httpx.MockTransport(forbidden), budget)) as client:
        with pytest.raises(RunBudgetError):
            await client.post("https://synthetic.invalid/",
                              json={"max_tokens": 10, "messages": "bad-shape"})
    with pytest.raises(RunBudgetError):
        budget.finished()


async def test_strict_input_token_cap_still_refuses():
    """A hard input-token bound has no honest pre-dispatch measure: the
    budget is spent at construction and nothing dispatches — an image or
    a text call alike."""
    budget = RunBudget(max_requests=1, max_input_tokens=100,
                       max_output_tokens=20)

    def forbidden(request):
        raise AssertionError("strict-cap body reached transport")

    async with httpx.AsyncClient(transport=_BoundedTransport(
            httpx.MockTransport(forbidden), budget)) as client:
        with pytest.raises(RunBudgetError):
            await client.post(
                "https://synthetic.invalid/v1/chat/completions", json={
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}]})
    with pytest.raises(RunBudgetError):
        budget.finished()


async def test_stream_group_retries_transient_errors():
    """NOGROUP/transient group failures must NOT mark the stream ready —
    only an actual create or BUSYGROUP proves the consumer group exists."""
    from nora.app.computer_queue import ComputerQueueRunner
    store = FakeComputerTaskStore()

    class FlakyRedis(FakeStreamRedis):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def xgroup_create(self, *a, **kw):
            if self.fail:
                raise RuntimeError("generated transient group error")
            return await super().xgroup_create(*a, **kw)

    flaky = FlakyRedis()
    runner = ComputerQueueRunner(tasks=store, redis=flaky, clock=lambda: NOW)
    stream = f"nq:computer:{SCOPE}"
    await runner._group(stream)   # transient failure: logged, NOT ready
    assert stream not in runner._group_ready
    flaky.fail = False
    await runner._group(stream)
    assert stream in runner._group_ready
    # A second call proves the group through BUSYGROUP, not another create.
    await runner._group(stream)


async def test_fake_election_rolls_back_on_outbox_failure():
    jobs = FakeComputeJobStore()
    store = FakeComputerTaskStore(outbox=jobs)
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(), clock=lambda: NOW)
    row = (await admission.submit(scope="ab" * 16, kind="native", payload={}, origin={})).task
    await store.finish(row.id, status="done", result={"task": "original"}, now=NOW)
    jobs.create = AsyncMock(side_effect=RuntimeError("generated outbox failure"))
    with pytest.raises(RuntimeError):
        await store.elect_delivery(row.id, now=NOW, job=SimpleNamespace(id="generated"))
    assert (await store.get(row.id)).delivered_at is None


async def test_malformed_nonempty_capture_is_refused():
    class CDP:
        async def call(self, method, params=None, **kwargs):
            assert method == "Page.captureScreenshot"
            return {"data": base64.b64encode(b"not a png").decode()}

    page = BrowserPage(cdp=CDP(), session_id="generated", target_id="generated")
    with pytest.raises(BrowserError):
        await page.capture_image()


async def test_journey_rejects_foreign_handle_and_unrelated_export():
    from evals.backbone import NORA_TOOL_INPUTS, NORA_TOOL_RESULTS, ToolRouteGrader

    ran = ["computer_browse", "computer_capture", "computer_export"]
    results = [
        (
            "computer_browse",
            json.dumps({"status": "ok", "task": "owned-handle", "tab": "target-a"}),
        ),
        (
            "computer_capture",
            json.dumps(
                {"status": "ok", "sha256": "garbage", "path": "captures/a.png", "tab": "target-b"}
            ),
        ),
        (
            "computer_export",
            json.dumps(
                {
                    "status": "ok",
                    "artifact_id": "unrelated",
                    "url": "https://generated.invalid/unrelated",
                }
            ),
        ),
    ]
    results = [(name, json.dumps([{"text": value}])) for name, value in results]
    inputs = [
        ("computer_capture", json.dumps({"task": "foreign-owned-handle"})),
        ("computer_export", json.dumps({"path": "unrelated.txt", "to": "link"})),
    ]
    data = SimpleNamespace(
        actual_environment_state=[
            SimpleNamespace(name=NORA_TOOL_RESULTS, state=results),
            SimpleNamespace(name=NORA_TOOL_INPUTS, state=inputs),
        ]
    )
    assert ToolRouteGrader()._journey(data, ran) is not None


@pytest.mark.parametrize(
    "changed", [None, "handle", "target", "path", "digest", "export_digest", "kept"],
)
async def test_real_journey_accepts_successful_keep_receipt(changed):
    from evals.backbone import NORA_TOOL_INPUTS, NORA_TOOL_RESULTS, ToolRouteGrader

    ran = ["computer_browse", "computer_capture", "computer_export"]
    values = [
        ("computer_browse", {"status": "ok", "task": "owned-handle", "tab": "target-a"}),
        (
            "computer_capture",
            {"status": "ok", "sha256": "a" * 64, "path": "captures/a.png", "tab": "target-a"},
        ),
        (
            "computer_export",
            {
                "status": "ok",
                "kept": True,
                "sha256": "a" * 64,
                "media_type": "image/png",
                "size": 100,
            },
        ),
    ]
    capture_input = {"task": "owned-handle"}
    export_input = {"path": "captures/a.png", "to": "keep"}
    if changed == "handle":
        capture_input["task"] = "foreign-owned-handle"
    elif changed == "target":
        values[1][1]["tab"] = "target-b"
    elif changed == "path":
        export_input["path"] = "unrelated.txt"
    elif changed == "digest":
        values[1][1]["sha256"] = "garbage"
    elif changed == "export_digest":
        values[2][1]["sha256"] = "b" * 64
    elif changed == "kept":
        values[2][1]["kept"] = False
    results = [(n, json.dumps([{"text": json.dumps(v)}])) for n, v in values]
    inputs = [
        ("computer_capture", json.dumps(capture_input)),
        ("computer_export", json.dumps(export_input)),
    ]
    data = SimpleNamespace(
        actual_environment_state=[
            SimpleNamespace(name=NORA_TOOL_RESULTS, state=results),
            SimpleNamespace(name=NORA_TOOL_INPUTS, state=inputs),
        ]
    )
    assert (ToolRouteGrader()._journey(data, ran) is None) == (changed is None)
