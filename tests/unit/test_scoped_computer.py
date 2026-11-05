"""The scoped computer journey ([computer.scoped], design/browser-runtime).

Generated fixtures only: every scope is a synthetic hex key, every VM a
fake, every browser target an in-memory stand-in. What is covered:

- durable FIFO: `try_claim_head` admits only the scope's head; a queued
  collision waits behind it; an independent scope is not blocked;
- the queue runner is granter+watchdog: granted rows are watched to their
  terminal row, deadline/orphan bounds close `cancelled`/`interrupted`,
  the stream entry is acked only after the terminal write;
- nested calls inherit the parent slot (`current_segment`);
- task-owned tabs: continuation binds the SAME target, a stale instance
  expires rows, popups count toward the four-target cap, idle expiry is
  swept, and a capture writes a real image into the workspace with
  provenance;
- the `<computer>` block reports a task's tab expired after the sweep.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from nora.app.computer_queue import (
    GROUP,
    ORPHAN_AFTER,
    ComputerAdmission,
    ComputerQueueRunner,
    ComputerTaskBackend,
    _stream,
    current_segment,
)
from nora.domain.computer import (
    OpResult,
    SandboxStatus,
    StateResult,
    computer_unit_scope,
)
from nora.domain.computer_context import ComputerContextCaps
from nora.domain.computer_events import ComputerEvent
from nora.domain.computer_tasks import ComputerTask
from nora.domain.computer_vms import ScopedEndpoint
from nora.engagement.computer_context import ComputerContextReader
from nora.infra.computer import manager as mgr_mod
from nora.infra.computer.browser import BrowserError
from nora.infra.computer.manager import (
    MAX_PAGE_TARGETS,
    ScopeBrowserManager,
    TabUnavailable,
)
from nora.infra.computer.scoped import ScopeBinding
from nora.language.computer_render import render_computer
from nora.testing import (
    FakeBrowserTabStore,
    FakeComputerEventStore,
    FakeComputerSessionStore,
    FakeComputerTaskStore,
    FakeScopeEndpoints,
    FakeStreamRedis,
)

NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
SCOPE = "a1b2c3d4e5f6a7b8"
OTHER = "f6e5d4c3b2a19876"
UNIT_SCOPE = computer_unit_scope("unit-x")


def clock(t: datetime = NOW):
    return lambda: t


def task(scope=SCOPE, kind="native", *, created=None, deadline=None):
    created = created or NOW
    return ComputerTask(
        id=uuid.uuid4(), scope=scope, kind=kind, status="queued",
        payload={"tool": "computer_run", "args": {"command": "true"}},
        origin={"platform": "signal", "conversation_id": "room-a",
                "conversation_type": "group", "requester_person_id": "p1"},
        created_at=created, updated_at=created, deadline_at=deadline)


# --- the durable FIFO -------------------------------------------------------


async def test_claim_head_respects_fifo_per_scope() -> None:
    store = FakeComputerTaskStore()
    first, second = task(created=NOW), task(created=NOW + timedelta(seconds=1))
    other = task(scope=OTHER)
    for t in (first, second, other):
        await store.enqueue(t)
    assert await store.try_claim_head(second.id, now=NOW) is None  # not head
    claimed = await store.try_claim_head(first.id, now=NOW)
    assert claimed is not None and claimed.status == "running"
    # A running row blocks a later one even though its created_at is later.
    assert await store.try_claim_head(second.id, now=NOW) is None
    # An independent scope is not blocked.
    assert await store.try_claim_head(other.id, now=NOW) is not None
    await store.finish(first.id, status="done", result={}, now=NOW)
    assert await store.try_claim_head(second.id, now=NOW) is not None


async def test_admission_grants_head_and_queues_the_collision() -> None:
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    one = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    assert one.granted
    two = await admission.submit(scope=SCOPE, kind="native", payload={},
                                 origin={}, wait_seconds=0.0)
    assert not two.granted and two.result is None
    # The queued collision has its stream pointer.
    assert len(redis.streams[_stream(SCOPE)]) == 1
    # A different scope still grants immediately.
    three = await admission.submit(scope=OTHER, kind="native", payload={}, origin={})
    assert three.granted


async def test_nested_call_inherits_the_slot() -> None:
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    parent = task(scope=SCOPE)
    await store.enqueue(parent)
    token = current_segment.set(parent.id)
    try:
        got = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    finally:
        current_segment.reset(token)
    assert got.inherited and not got.granted
    # No new row, no stream entry: a child never queues behind its parent.
    assert [r.id for r in store.rows.values()] == [parent.id]
    assert _stream(SCOPE) not in redis.streams


async def test_runner_grants_head_then_executes_and_delivers() -> None:
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    delivered: list[ComputerTask] = []
    ran: list[str] = []

    async def collect(row: ComputerTask) -> None:
        # The real `on_terminal` (deliver_computer_task) marks delivered
        # after the room handoff; model that here.
        delivered.append(row)
        await store.mark_delivered(row.id, now=NOW)

    async def executor(row: ComputerTask) -> dict:
        # The deferred executor rebuilds the call from the DURABLE payload.
        ran.append(str(row.payload.get("tool")))
        return {"status": "ok", "answer": "the durable answer"}

    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), watch_seconds=0.01,
        on_terminal=collect, dispatch={"native": executor})
    blocker = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    queued = await admission.submit(scope=SCOPE, kind="native",
                                    payload={"tool": "computer_run",
                                             "args": {"command": "true"}},
                                    origin={}, wait_seconds=0.0)
    assert blocker.granted and not queued.granted
    # A running row blocks the whole scope this pass.
    assert await runner.tick() == 0
    await admission.finish(blocker.task.id, status="done", result={})
    assert await runner.tick() == 1
    # `_deliver_pending` may deliver the finished blocker first; wait for
    # THIS row to reach its terminal state, not for any delivery at all.
    row = await store.get(queued.task.id)
    for _ in range(50):
        if row is not None and row.status == "done":
            break
        await asyncio.sleep(0.01)
        row = await store.get(queued.task.id)
    assert row is not None and row.status == "done"
    # The typed result is the row's record — the answer, not a generic note.
    assert row.result.get("answer") == "the durable answer"
    assert ran == ["computer_run"]
    assert delivered and delivered[-1].id == queued.task.id
    assert row.delivered_at is not None


async def test_cancelled_queued_task_never_starts() -> None:
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    runner = ComputerQueueRunner(tasks=store, redis=redis, clock=clock())
    one = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    two = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    assert one.granted
    await admission.finish(one.task.id, status="done", result={})
    await admission.cancel(two.task.id)
    await runner.tick()
    row = await store.get(two.task.id)
    assert row is not None and row.status == "cancelled"


async def test_deadline_and_orphan_close_never_replay() -> None:
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), watch_seconds=0.01,
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    stale = task(deadline=NOW - timedelta(seconds=1))
    await store.enqueue(stale)
    await redis.xadd(_stream(SCOPE), {"task": str(stale.id)})
    await runner.tick()
    row = await store.get(stale.id)
    assert row is not None and row.status == "cancelled"
    # An orphaned running row outlives its grant bound: interrupted, not re-run.
    orphan = replace(
        task(), status="running",
        started_at=NOW - ORPHAN_AFTER - timedelta(seconds=1))
    store.rows[orphan.id] = orphan
    await runner.reconcile()
    row = await store.get(orphan.id)
    assert row is not None and row.status == "interrupted"


# --- task-owned tabs ---------------------------------------------------------


class _Cdp:
    """The manager's CDP seam, in memory: a page-target pool per scope,
    with the event fan-out `subscribe`/`unsubscribe` expose."""

    def __init__(self) -> None:
        self.targets: dict[str, dict] = {}
        self.closed: list[str] = []
        self.handlers: list = []
        self._n = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def subscribe(self, handler) -> None:
        self.handlers.append(handler)

    def unsubscribe(self, handler) -> None:
        if handler in self.handlers:
            self.handlers.remove(handler)

    def fire(self, method: str, params: dict | None = None) -> None:
        for handler in list(self.handlers):
            handler(method, params or {})

    async def call(self, method, params=None):
        if method == "Target.getTargets":
            return {"targetInfos": [
                {"targetId": tid, "type": "page",
                 "url": t.get("url", ""), "title": t.get("title", "")}
                for tid, t in self.targets.items()]}
        if method == "Target.closeTarget":
            tid = (params or {})["targetId"]
            self.closed.append(tid)
            self.targets.pop(tid, None)
            return {}
        if method == "Target.setDiscoverTargets":
            return {}
        return {}


class _Page:
    def __init__(self, cdp: _Cdp, target_id: str) -> None:
        self.cdp = cdp
        self.target_id = target_id

    async def evaluate(self, expr):
        key = "title" if "title" in expr else "url"
        return self.cdp.targets.get(self.target_id, {}).get(key, "")

    async def capture_image(self, *, full_page):
        return b"\x89PNGfake", "image/png", False

    def revoke(self) -> None:
        return None


class _FakeScopedRuntime:
    """The scoped runtime's surface the manager reads: `binding`, `state`,
    `put`, `registered`, `invalidate` — the endpoint registry decides
    resolution."""

    def __init__(self, endpoints: FakeScopeEndpoints, *, instance: str) -> None:
        self.endpoints = endpoints
        self._instance = instance
        self.puts: dict[str, bytes] = {}
        self.browser_running = False
        self.invalidated: list[str] = []

    async def binding(self, scope):
        ep = await self.endpoints.resolve(scope)
        if ep is None:
            return None
        return ScopeBinding(endpoint=ep, runtime=None, instance=self._instance)

    async def instance(self, scope):
        return self._instance

    async def registered(self):
        return tuple(self.endpoints.by_scope)

    def invalidate(self, scope: str) -> None:
        self.invalidated.append(scope)

    async def put(self, *, scope, path, data):
        self.puts[path] = data
        return OpResult(status=SandboxStatus.OK)

    async def state(self, *, scope):
        return StateResult(status=SandboxStatus.OK,
                           browser={"running": self.browser_running})


@dataclass
class _Rig:
    manager: ScopeBrowserManager
    tabs: FakeBrowserTabStore
    cdp: _Cdp
    runtime: _FakeScopedRuntime


def _wired(monkeypatch, *, instance="boot-1", tasks=None, segments=None) -> _Rig:
    """A manager whose CDP/browser seams are the in-memory pair above."""
    cdp = _Cdp()
    endpoints = FakeScopeEndpoints()
    runtime = _FakeScopedRuntime(endpoints, instance=instance)
    tabs = FakeBrowserTabStore()

    @contextlib.asynccontextmanager
    async def fake_cdp(*, base_url, token, scope, instance=None):
        yield cdp

    @contextlib.asynccontextmanager
    async def fake_tab(*, base_url, token, scope, target_id, instance=None):
        if target_id is None:
            cdp._n += 1
            target_id = f"target-{cdp._n}"
            cdp.targets[target_id] = {"url": "about:blank", "title": ""}
        elif target_id not in cdp.targets:
            raise BrowserError("target gone")
        yield _Page(cdp, target_id)

    monkeypatch.setattr(mgr_mod, "connect_browser_cdp", fake_cdp)
    monkeypatch.setattr(mgr_mod, "open_browser_tab", fake_tab)
    return _Rig(
        manager=ScopeBrowserManager(
            runtime=runtime, tabs=tabs, tasks=tasks, segments=segments,
            token="t",  # noqa: S106 - a test token
            clock=clock(), idle_after=timedelta(minutes=10)),
        tabs=tabs, cdp=cdp, runtime=runtime)


def _register(runtime: _FakeScopedRuntime, scope=SCOPE, instance="boot-1"):
    runtime.endpoints.by_scope[scope] = ScopedEndpoint(
        scope=scope, base_url="http://vm", name="vm", instance=instance)


async def test_task_owns_its_tab_across_calls(monkeypatch) -> None:
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owner = uuid.uuid4()
    async with rig.manager.acquire(SCOPE, owner) as bound:
        first_target = bound.tab.target_id
    async with rig.manager.acquire(SCOPE, owner) as bound:
        assert bound.tab.target_id == first_target  # SAME tab, continuation
    other = uuid.uuid4()
    async with rig.manager.acquire(SCOPE, other) as bound:
        assert bound.tab.target_id != first_target  # another task, another tab


async def test_scope_cannot_read_another_scopes_tab(monkeypatch) -> None:
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owner = uuid.uuid4()
    async with rig.manager.acquire(SCOPE, owner):
        pass
    # The other scope has no registry record: unresolvable, fail-closed.
    with pytest.raises(TabUnavailable):
        async with rig.manager.acquire(OTHER, owner):
            pass


async def test_stale_instance_expires_the_binding(monkeypatch) -> None:
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owner = uuid.uuid4()
    async with rig.manager.acquire(SCOPE, owner) as bound:
        target = bound.tab.target_id
    # The VM is recreated: a NEW instance answers now.
    rig.runtime._instance = "boot-2"
    _register(rig.runtime, instance="boot-2")
    async with rig.manager.acquire(SCOPE, owner) as bound:
        # A fresh tab on the new instance — the old row is expired, never
        # re-attached.
        assert bound.tab.target_id != target
        assert bound.tab.instance == "boot-2"
    stale = [r for r in await rig.tabs.for_scope(SCOPE, limit=20)
             if r.task_id == owner and r.instance == "boot-1"]
    assert stale and all(r.status == "expired" for r in stale)


async def test_popup_cap_counts_toward_four(monkeypatch) -> None:
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owners = [uuid.uuid4() for _ in range(MAX_PAGE_TARGETS)]
    for owner in owners:
        async with rig.manager.acquire(SCOPE, owner):
            pass
    assert len([r for r in rig.tabs.rows.values() if r.status == "open"]
               ) == MAX_PAGE_TARGETS
    with pytest.raises(TabUnavailable):
        async with rig.manager.acquire(SCOPE, uuid.uuid4()):
            pass


async def test_idle_expiry_sweeps_and_context_reports_it(monkeypatch) -> None:
    rig = _wired(monkeypatch)
    _register(rig.runtime, scope=UNIT_SCOPE)
    async with rig.manager.acquire(UNIT_SCOPE, uuid.uuid4()):
        pass
    for row in list(rig.tabs.rows.values()):
        rig.tabs.rows[row.id] = replace(
            row, last_activity_at=NOW - timedelta(minutes=11))
    expired = await rig.manager.sweep(now=NOW)
    assert expired == 1
    assert all(r.status == "expired" for r in rig.tabs.rows.values())
    assert rig.cdp.closed  # the target itself was closed

    # The context block reports the expiry to the next turn.
    sessions = FakeComputerSessionStore()
    events = FakeComputerEventStore()
    await events.append(ComputerEvent(
        id=uuid.uuid4(), operation_id=uuid.uuid4(), phase="finished",
        created_at=NOW, started_at=NOW, finished_at=NOW,
        actor="tag", requester_person_id="p1", platform="signal",
        conversation_id="room-a", conversation_type="group", tag_id="",
        scope=UNIT_SCOPE, tool="computer_browse", path="direct",
        trace_id="", span_id="", status="ok",
        result={"note": "browse ended"}))
    tasks = FakeComputerTaskStore()
    reader = ComputerContextReader(
        sessions=sessions, events=events, runtime=rig.runtime,
        caps=ComputerContextCaps(), clock=clock(), metrics=None,
        unit_for=lambda platform, cid: "unit-x",
        tasks=tasks, tabs=rig.tabs, browser_idle_after=timedelta(minutes=10))
    view = await reader.read(platform="signal", conversation_id="room-a")
    assert view is not None and view.tabs
    block = render_computer(view)
    assert block is not None and "expired" in block.body


async def test_capture_writes_the_workspace_with_provenance(monkeypatch) -> None:
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owner = uuid.uuid4()
    async with rig.manager.acquire(SCOPE, owner):
        pass
    got = await rig.manager.capture(SCOPE, owner, full_page=True)
    assert got.path in rig.runtime.puts
    assert got.sha256 == hashlib.sha256(rig.runtime.puts[got.path]).hexdigest()
    assert got.size == len(rig.runtime.puts[got.path]) and got.media_type == "image/png"
    assert got.task_id == owner and got.target_id


async def test_backend_polls_the_terminal_row() -> None:
    store = FakeComputerTaskStore()
    backend = ComputerTaskBackend(tasks=store)
    row = task()
    await store.enqueue(row)
    from nora.domain.ports import JobStatus
    running = await backend.poll(job_id=str(row.id))
    assert running.status is JobStatus.RUNNING
    await store.claim(row.id, now=NOW)
    await store.finish(row.id, status="done", result={"note": "ran"}, now=NOW)
    done = await backend.poll(job_id=str(row.id))
    assert done.status is JobStatus.SUCCEEDED and done.report == "ran"


# --- owner, drain, watchdog ---------------------------------------------------


async def test_run_segment_cancel_reaches_the_executor() -> None:
    """A cancel on a RUNNING task reaches its real asyncio executor: the
    work is cancelled and DRAINED, and only then does the row close."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(
        tasks=store, redis=redis, clock=clock(),
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    granted = await admission.submit(scope=SCOPE, kind="native",
                                     payload={}, origin={})
    assert granted.granted
    stopped = asyncio.Event()

    async def work() -> dict:
        try:
            await asyncio.Event().wait()
            return {"status": "ok"}
        finally:
            stopped.set()

    runner = asyncio.ensure_future(
        admission.run_segment(granted.task, work()))
    await asyncio.sleep(0.02)
    row = await admission.cancel(granted.task.id)
    assert row is not None and row.status == "cancelled"
    assert stopped.is_set()  # the executor itself was stopped, not just flagged
    got = await runner
    assert got["status"] == "cancelled"


async def test_owner_lost_busy_scope_stays_blocked() -> None:
    """A running row whose executor is gone: the VM's ACTUAL state decides.
    Busy or unknown keeps the scope blocked — an idle grant is not proof the
    old command stopped — quiet lets it close interrupted."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    verdicts = iter(["busy", "quiet"])

    async def verdict(scope: str) -> str:
        return next(verdicts)

    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), reconcile=verdict)
    orphan = replace(task(), status="running", started_at=NOW)
    store.rows[orphan.id] = orphan
    # Busy: the row keeps running and nothing behind it is granted.
    assert await runner.tick() == 0
    row = await store.get(orphan.id)
    assert row is not None and row.status == "running"
    # Quiet: the VM says nothing runs — close interrupted, never replay.
    assert await runner.tick() == 0
    row = await store.get(orphan.id)
    assert row is not None and row.status == "interrupted"


async def test_orphan_bound_never_closes_a_busy_vm() -> None:
    """The 45-min orphan bound is still not permission to close on age:
    a VM that answers BUSY keeps its running row — only the guest's own
    `quiet` lets the lost owner close `interrupted`."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    verdict = ["busy"]

    async def reconcile(scope: str) -> str:
        return verdict[0]

    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), reconcile=reconcile)
    old = replace(task(), status="running",
                  started_at=NOW - timedelta(hours=1))
    store.rows[old.id] = old
    # Busy: past the orphan bound does not close it — still the owner.
    await runner.tick()
    row = await store.get(old.id)
    assert row is not None and row.status == "running"
    # Quiet: the guest itself reports no surviving work — close.
    verdict[0] = "quiet"
    await runner.tick()
    row = await store.get(old.id)
    assert row is not None and row.status == "interrupted"


async def test_worker_row_is_never_runner_dispatched() -> None:
    """A `worker` segment is claimed by its OWN submitter's wait_for_grant:
    the runner has no dispatcher for the kind and must not run it."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    ran: list[str] = []
    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(),
        dispatch={"native": lambda row: ran.append("native") or {}})
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    got = await admission.submit(scope=SCOPE, kind="worker",
                                 payload={"job": "j1"}, origin={})
    # Head grants to the submitter directly — the worker executes itself.
    assert got.granted
    assert await runner.tick() == 0
    assert ran == []


async def test_deferred_dispatch_rebuilds_the_durable_request() -> None:
    """Restart-shaped proof: a queued row plus a stream entry is EVERYTHING
    a fresh runner needs — no in-memory closure survives a restart."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    row = task()
    await store.enqueue(row)
    await redis.xadd(_stream(SCOPE), {"task": str(row.id)})
    seen: list[dict] = []

    async def executor(t: ComputerTask) -> dict:
        seen.append(dict(t.payload))
        return {"status": "ok", "note": "rebuilt"}

    # A NEW runner, a NEW admission — the in-process state is gone.
    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(),
        dispatch={"native": executor})
    assert await runner.tick() == 1
    for _ in range(50):
        fresh = await store.get(row.id)
        if fresh is not None and fresh.status == "done":
            break
        await asyncio.sleep(0.01)
    fresh = await store.get(row.id)
    assert fresh is not None and fresh.status == "done"
    assert seen == [{"tool": "computer_run", "args": {"command": "true"}}]


# --- NEW vs EXISTING handles ---------------------------------------------------


async def test_stale_instance_continuation_is_expired(monkeypatch) -> None:
    """An EXISTING handle on a dead instance answers `expired` — the VM was
    recreated, its tabs died with it, and no substitute is conjured."""
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owner = uuid.uuid4()
    async with rig.manager.acquire(SCOPE, owner):
        pass
    rig.runtime._instance = "boot-2"
    _register(rig.runtime, instance="boot-2")
    with pytest.raises(TabUnavailable) as err:
        async with rig.manager.acquire(SCOPE, owner, must_exist=True):
            pass
    assert err.value.reason == "expired"


async def test_capture_never_creates_a_tab(monkeypatch) -> None:
    """Capture is EXISTING-only: a task with no tab is `expired`, and no
    target and no row appear as a side effect."""
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    with pytest.raises(TabUnavailable) as err:
        await rig.manager.capture(SCOPE, uuid.uuid4(), full_page=False)
    assert err.value.reason == "expired"
    assert not rig.cdp.targets and not rig.tabs.rows


async def test_foreign_and_invented_handles_are_refused(monkeypatch) -> None:
    """A target naming ANOTHER task's tab is `denied`; a target no row
    names is `expired` — never adopted, never replaced."""
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    mine, other = uuid.uuid4(), uuid.uuid4()
    async with rig.manager.acquire(SCOPE, mine) as bound:
        target = bound.tab.target_id
    with pytest.raises(TabUnavailable) as err:
        async with rig.manager.acquire(SCOPE, other, target_id=target):
            pass
    assert err.value.reason == "denied"
    with pytest.raises(TabUnavailable) as err:
        async with rig.manager.acquire(SCOPE, other, target_id="target-999"):
            pass
    assert err.value.reason == "expired"


async def test_popup_adopted_to_the_running_task(monkeypatch) -> None:
    """A page target with no row — a popup — is bound to the task executing
    when it is found (`via="popup"`), counted toward the four-cap."""
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owner = uuid.uuid4()
    async with rig.manager.acquire(SCOPE, owner):
        # The segment's own click spawned a page target.
        rig.cdp.targets["popup-1"] = {"url": "https://x", "title": "pop"}
        rig.cdp.fire("Target.targetCreated",
                     {"targetInfo": {"targetId": "popup-1", "type": "page"}})
        await asyncio.sleep(0.05)
        adopted = [r for r in rig.tabs.rows.values() if r.via == "popup"]
        assert adopted and adopted[0].task_id == owner
        assert adopted[0].target_id == "popup-1"


async def test_context_renders_full_opaque_handles(monkeypatch) -> None:
    """The `<computer>` block carries the task's FULL UUID — the handle a
    later `computer_cancel`/capture parses — never a truncated prefix."""
    rig = _wired(monkeypatch)
    _register(rig.runtime, scope=UNIT_SCOPE)
    tasks = FakeComputerTaskStore()
    row = task(scope=UNIT_SCOPE)
    row = replace(row, status="running", started_at=NOW)
    await store_row(tasks, row)
    sessions = FakeComputerSessionStore()
    events = FakeComputerEventStore()
    reader = ComputerContextReader(
        sessions=sessions, events=events, runtime=rig.runtime,
        caps=ComputerContextCaps(), clock=clock(), metrics=None,
        unit_for=lambda platform, cid: "unit-x",
        tasks=tasks, tabs=rig.tabs, browser_idle_after=timedelta(minutes=10))
    view = await reader.read(platform="signal", conversation_id="room-a")
    assert view is not None and view.tasks
    assert view.tasks[0].task_id == str(row.id)  # the full UUID handle
    block = render_computer(view)
    assert block is not None and str(row.id) in block.body


async def store_row(tasks: FakeComputerTaskStore, row: ComputerTask) -> None:
    tasks.rows[row.id] = row


# --- exact scope+instance binding -------------------------------------------------


def _runtime(endpoints: FakeScopeEndpoints, identity: dict | None,
             clock_fn=None):
    """A ScopedComputerdRuntime whose /v1/identity answer is `identity`."""
    from nora.infra.computer.scoped import ScopedComputerdRuntime
    runtime = ScopedComputerdRuntime(
        endpoints=endpoints, token="t",  # noqa: S106 - a test token
        clock=clock_fn or clock())

    async def fake_identity(endpoint):
        return identity

    runtime._identity = fake_identity
    return runtime


async def test_binding_refuses_empty_and_mismatched_instance() -> None:
    """A registry record whose `instance` is empty authorizes NOTHING (the
    pre-observe phase), and a VM answering a different instance than the
    record's is a stale authority — neither binds."""
    endpoints = FakeScopeEndpoints()
    # Pre-observe record: instance empty.
    endpoints.by_scope[SCOPE] = ScopedEndpoint(
        scope=SCOPE, base_url="http://vm", name="vm", instance="")
    runtime = _runtime(endpoints, {"scope": SCOPE, "instance": "boot-9"})
    assert await runtime.binding(SCOPE) is None
    # Observed record: the VM must answer the SAME instance.
    endpoints.by_scope[SCOPE] = ScopedEndpoint(
        scope=SCOPE, base_url="http://vm", name="vm", instance="boot-1")
    runtime = _runtime(endpoints, {"scope": SCOPE, "instance": "boot-9"})
    assert await runtime.binding(SCOPE) is None  # a recreated VM, old record
    runtime = _runtime(endpoints, {"scope": SCOPE, "instance": "boot-1"})
    bound = await runtime.binding(SCOPE)
    assert bound is not None and bound.instance == "boot-1"


async def test_binding_refuses_a_foreign_scope_and_refalls() -> None:
    """A VM answering for ANOTHER scope is never adopted — and there is no
    shared fallback: an unregistered scope resolves to nothing."""
    endpoints = FakeScopeEndpoints()
    endpoints.by_scope[SCOPE] = ScopedEndpoint(
        scope=SCOPE, base_url="http://vm", name="vm", instance="boot-1")
    runtime = _runtime(endpoints, {"scope": OTHER, "instance": "boot-1"})
    assert await runtime.binding(SCOPE) is None
    runtime = _runtime(endpoints, {"scope": SCOPE, "instance": "boot-1"})
    assert await runtime.binding(OTHER) is None  # no record, no fallback
    assert await runtime.binding("") is None


async def test_binding_revalidates_after_invalidation() -> None:
    """A cached binding is dropped on a failed verb; the next call asks
    /v1/identity again — stale authority is never trusted twice."""
    endpoints = FakeScopeEndpoints()
    endpoints.by_scope[SCOPE] = ScopedEndpoint(
        scope=SCOPE, base_url="http://vm", name="vm", instance="boot-1")
    calls: list[str] = []
    from nora.infra.computer.scoped import ScopedComputerdRuntime
    runtime = ScopedComputerdRuntime(
        endpoints=endpoints, token="t",  # noqa: S106 - a test token
        clock=clock())

    async def counting(endpoint):
        calls.append("identity")
        return {"scope": SCOPE, "instance": "boot-1"}

    runtime._identity = counting
    assert await runtime.binding(SCOPE) is not None
    assert await runtime.binding(SCOPE) is not None
    assert len(calls) == 1  # cached within the revalidation window
    runtime.invalidate(SCOPE)
    assert await runtime.binding(SCOPE) is not None
    assert len(calls) == 2  # re-verified, never trusted twice


# --- round-2 blockers -----------------------------------------------------------


async def test_submit_refuses_a_scope_that_moved() -> None:
    """`current_segment` inherits only for the SAME scope: a rebuilt call
    resolving a DIFFERENT partition — the room's unit or DM anchor moved
    under the queued row — is `unavailable`, never run under the old
    grant, never retargeted, never enqueued."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    parent = task(scope=SCOPE)
    await store.enqueue(parent)
    token = current_segment.set(parent.id)
    try:
        moved = await admission.submit(scope=OTHER, kind="native",
                                       payload={}, origin={})
        same = await admission.submit(scope=SCOPE, kind="native",
                                      payload={}, origin={})
    finally:
        current_segment.reset(token)
    assert not moved.granted and not moved.inherited
    assert moved.result is not None and moved.result.status == "unavailable"
    assert "scope moved" in (moved.result.result or {}).get("reason", "")
    assert same.inherited
    # The only durable row is the parent's — nothing reached the new scope.
    assert [r.id for r in store.rows.values()] == [parent.id]
    assert _stream(OTHER) not in redis.streams


async def test_terminal_undelivered_row_is_rediscovered() -> None:
    """A terminal row the stream never carried — or whose delivery failed —
    is re-driven from the TABLE until `delivered_at` lands: a last row with
    no open siblings and no pointer is never stranded."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    delivered: list[uuid.UUID] = []
    attempts: list[uuid.UUID] = []

    async def flaky(row: ComputerTask) -> None:
        attempts.append(row.id)
        if len(attempts) == 1:
            raise RuntimeError("delivery down")  # retried, never lost
        delivered.append(row.id)
        await store.mark_delivered(row.id, now=NOW)

    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), on_terminal=flaky)
    last = replace(task(), status="done", finished_at=NOW)
    store.rows[last.id] = last  # terminal, undelivered, no stream entry
    assert await runner.tick() == 0
    row = await store.get(last.id)
    assert row is not None and row.delivered_at is None  # failed: stays
    assert await runner.tick() == 0
    row = await store.get(last.id)
    assert delivered == [last.id] and row.delivered_at is not None


async def test_settle_never_acks_a_row_that_is_not_terminal() -> None:
    """A row whose `finish` write never landed is neither delivered nor
    acked — the pending pointer drives the pass that reconciles it."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    delivered: list[uuid.UUID] = []

    async def collect(row: ComputerTask) -> None:
        delivered.append(row.id)

    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), on_terminal=collect)
    row = replace(task(), status="running", started_at=NOW)
    store.rows[row.id] = row
    mid = await redis.xadd(_stream(SCOPE), {"task": str(row.id)})
    key = (_stream(SCOPE), GROUP)
    redis.pending.setdefault(key, []).append(mid)
    await runner._settle(_stream(SCOPE), mid, row)
    fresh = await store.get(row.id)
    assert fresh.delivered_at is None and delivered == []
    assert mid in redis.pending[key]  # unacked: the pointer survives


async def test_cancel_without_executor_keeps_a_busy_vm_blocked() -> None:
    """A running row with no local executor: `cancel` asks the GUEST — busy
    keeps the row running and the scope excluded; quiet closes it
    `interrupted`, never `cancelled` on a flag alone."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    verdicts = ["busy"]

    async def probe(scope: str) -> str:
        return verdicts[-1]

    admission = ComputerAdmission(
        tasks=store, redis=redis, clock=clock(), reconcile=probe)
    running = replace(task(), status="running", started_at=NOW)
    store.rows[running.id] = running
    row = await admission.cancel(running.id)
    assert row is not None and row.status == "running"  # busy: never closed
    verdicts.append("quiet")
    row = await admission.cancel(running.id)
    assert row is not None and row.status == "interrupted"
    assert (row.result or {}).get("reason") == "the owner was lost"


async def test_caller_cancel_never_orphans_the_executor() -> None:
    """A cancelled CALLER leaves the segment registered and running: the
    executor — not the awaiter — owns the row's terminal write, and the
    registry entry ends when the executor does."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(
        tasks=store, redis=redis, clock=clock(),
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    granted = await admission.submit(scope=SCOPE, kind="native",
                                     payload={}, origin={})
    assert granted.granted
    release = asyncio.Event()

    async def work() -> dict:
        await release.wait()
        return {"status": "ok"}

    caller = asyncio.ensure_future(
        admission.run_segment(granted.task, work()))
    await asyncio.sleep(0.02)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    # The executor outlives its caller: still registered, still owning.
    assert admission.executors.executor(granted.task.id) is not None
    release.set()
    row = None
    for _ in range(50):
        row = await store.get(granted.task.id)
        if row is not None and row.status == "done":
            break
        await asyncio.sleep(0.01)
    assert row is not None and row.status == "done"
    assert admission.executors.executor(granted.task.id) is None


async def test_poll_delivers_the_typed_result() -> None:
    """`poll` hands the room the typed result the row persisted — stdout,
    the capture's path and digest, the exit code — not a bare note."""
    store = FakeComputerTaskStore()
    backend = ComputerTaskBackend(tasks=store)
    row = task()
    await store.enqueue(row)
    await store.claim(row.id, now=NOW)
    await store.finish(row.id, status="done", result={
        "status": "ok", "exit_code": 0, "stdout": "answer is 42\n",
        "path": "captures/c1.png", "sha256": "ab" * 32}, now=NOW)
    from nora.domain.ports import JobStatus
    got = await backend.poll(job_id=str(row.id))
    assert got.status is JobStatus.SUCCEEDED
    assert "answer is 42" in got.report
    assert "captures/c1.png" in got.report and ("ab" * 32) in got.report


async def test_sweep_closes_a_popup_no_client_adopted(monkeypatch) -> None:
    """A page target that appeared while NO client was attached — a timer's
    popup — is found by the sweep's live-target reconcile and closed; an
    unowned target never mints a tab row."""
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    rig.runtime.browser_running = True
    rig.cdp.targets["popup-1"] = {"url": "https://x.invalid", "title": "pop"}
    assert await rig.manager.sweep(now=NOW) == 0  # no rows expired
    assert "popup-1" in rig.cdp.closed and "popup-1" not in rig.cdp.targets
    assert not rig.tabs.rows


async def test_browser_stop_refuses_while_a_task_runs(monkeypatch) -> None:
    """The stop joins the scope's execution owner: a durable `running` row
    makes the scope busy — no stop call reaches the VM at all."""
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    store = FakeComputerTaskStore()
    store.rows[task().id] = replace(task(), status="running", started_at=NOW)
    rig.manager._tasks = store
    rig.runtime.browser_running = True
    called: list[str] = []

    class _NoHttp:
        def __init__(self, *a, **k) -> None:
            called.append("http")
            raise AssertionError("no HTTP while a task runs")

    monkeypatch.setattr(mgr_mod.aiohttp, "ClientSession", _NoHttp)
    assert await rig.manager.browser_stop(SCOPE) is False
    assert called == []


async def test_sweep_retries_a_failed_stop_until_the_browser_is_down(
        monkeypatch) -> None:
    """A browser still running with every tab row gone is still discovered
    (the registry names the scope) and its stop retried — a refused stop is
    never the end of the story."""
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    rig.runtime.browser_running = True
    stops: list[str] = []

    async def fake_stop(scope: str) -> bool:
        stops.append(scope)
        if len(stops) < 2:
            return False  # unconfirmed: stays retryable
        rig.runtime.browser_running = False
        return True

    monkeypatch.setattr(rig.manager, "browser_stop", fake_stop)
    assert await rig.manager.sweep(now=NOW) == 0
    assert stops == [SCOPE]
    assert await rig.manager.sweep(now=NOW) == 0
    assert stops == [SCOPE, SCOPE]  # retried, not leaked


async def test_context_stamps_instance_revision_and_the_reads_own_time(
        monkeypatch) -> None:
    """The `<computer>` block carries the bound instance and the
    observation's own revision and read time — a cached read shows ITS
    age, never 'checked just now' on a stale answer."""
    rig = _wired(monkeypatch)
    _register(rig.runtime, scope=UNIT_SCOPE)
    sessions = FakeComputerSessionStore()
    events = FakeComputerEventStore()
    await events.append(ComputerEvent(
        id=uuid.uuid4(), operation_id=uuid.uuid4(), phase="finished",
        created_at=NOW, started_at=NOW, finished_at=NOW,
        actor="tag", requester_person_id="p1", platform="signal",
        conversation_id="room-a", conversation_type="group", tag_id="",
        scope=UNIT_SCOPE, tool="computer_run", path="direct",
        trace_id="", span_id="", status="ok", result={"note": "ran"}))
    tick = [NOW, 0.0]

    def mono() -> float:
        return tick[1]

    reader = ComputerContextReader(
        sessions=sessions, events=events, runtime=rig.runtime,
        caps=ComputerContextCaps(), clock=lambda: tick[0], metrics=None,
        unit_for=lambda platform, cid: "unit-x", monotonic=mono,
        tasks=FakeComputerTaskStore(), tabs=rig.tabs,
        browser_idle_after=timedelta(minutes=10))
    first = await reader.read(platform="signal", conversation_id="room-a")
    assert first is not None and first.instance == "boot-1"
    assert first.revision == 1 and first.state_at == NOW
    body = render_computer(first)
    assert body is not None and "boot-1" in body.body
    # A cached read (the monotonic clock stays inside the cache window): the
    # block stamps the ORIGINAL read's time and a new revision.
    tick[0] = NOW + timedelta(minutes=2)
    tick[1] = 10.0
    second = await reader.read(platform="signal", conversation_id="room-a")
    assert second.revision == 2 and second.state_at == NOW  # the read's own
    body = render_computer(second)
    assert body is not None and "2 min ago" in body.body


async def test_context_marks_a_dead_instances_tab_expired(monkeypatch) -> None:
    """A tab row bound to an instance that is no longer the live one is
    rendered `expired` whatever the sweep has had time to write — the
    block reports the live instance's tabs only."""
    rig = _wired(monkeypatch)
    _register(rig.runtime, scope=UNIT_SCOPE)
    async with rig.manager.acquire(UNIT_SCOPE, uuid.uuid4()):
        pass
    # The VM is recreated: a new instance answers; the row is un-swept.
    rig.runtime._instance = "boot-2"
    _register(rig.runtime, scope=UNIT_SCOPE, instance="boot-2")
    sessions = FakeComputerSessionStore()
    events = FakeComputerEventStore()
    await events.append(ComputerEvent(
        id=uuid.uuid4(), operation_id=uuid.uuid4(), phase="finished",
        created_at=NOW, started_at=NOW, finished_at=NOW,
        actor="tag", requester_person_id="p1", platform="signal",
        conversation_id="room-a", conversation_type="group", tag_id="",
        scope=UNIT_SCOPE, tool="computer_browse", path="direct",
        trace_id="", span_id="", status="ok", result={"note": "ran"}))
    reader = ComputerContextReader(
        sessions=sessions, events=events, runtime=rig.runtime,
        caps=ComputerContextCaps(), clock=clock(), metrics=None,
        unit_for=lambda platform, cid: "unit-x",
        tasks=FakeComputerTaskStore(), tabs=rig.tabs,
        browser_idle_after=timedelta(minutes=10))
    view = await reader.read(platform="signal", conversation_id="room-a")
    assert view is not None and view.tabs
    assert view.instance == "boot-2"
    assert all(t.status == "expired" for t in view.tabs)


# --- cleanup joins the execution owner ----------------------------------------


async def test_cleanup_claims_the_scopes_elected_owner(monkeypatch) -> None:
    """The sweep runs INSIDE the same owner every segment holds: a
    `maintenance` row claimed by `try_claim_head`, executed through
    `run_segment`, and closed `done` — the cleanup is a segment, not a
    second admission path beside the FIFO."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    rig = _wired(monkeypatch, tasks=store, segments=admission)
    _register(rig.runtime)
    # An idle OPEN row gives the pass real work.
    async with rig.manager.acquire(SCOPE, uuid.uuid4()):
        pass
    for row in list(rig.tabs.rows.values()):
        rig.tabs.rows[row.id] = replace(
            row, last_activity_at=NOW - timedelta(minutes=11))
    expired = await rig.manager.sweep(now=NOW)
    assert expired == 1 and rig.cdp.closed
    maintenance = [r for r in store.rows.values() if r.kind == "maintenance"]
    assert len(maintenance) == 1
    assert maintenance[0].status == "done"
    assert maintenance[0].result.get("expired") == 1
    # No room was named: the row is delivered, never queued for a reply.
    assert maintenance[0].delivered_at is not None


async def test_cleanup_never_runs_beside_a_live_owner(monkeypatch) -> None:
    """A `running` row IS an owner: the sweep's claim fails and no guest
    effect runs — no tab close, no browser stop — and no `maintenance`
    row is left parked in the queue either."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    rig = _wired(monkeypatch, tasks=store, segments=admission)
    _register(rig.runtime)
    rig.runtime.browser_running = True  # cleanup-worthy state
    owner = task()
    await store.enqueue(owner)
    assert await store.try_claim_head(owner.id, now=NOW) is not None
    expired = await rig.manager.sweep(now=NOW)
    assert expired == 0
    assert rig.cdp.closed == []
    assert not [r for r in store.rows.values() if r.kind == "maintenance"]
    # The live owner is untouched.
    assert (await store.get(owner.id)).status == "running"


async def test_cleanup_waits_behind_queued_work_never_jumps_the_fifo(
        monkeypatch) -> None:
    """Queued real work outranks housekeeping: with rows ahead, the sweep
    claims nothing and writes nothing — cleanup never waits in line."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    rig = _wired(monkeypatch, tasks=store, segments=admission)
    _register(rig.runtime)
    rig.runtime.browser_running = True
    ahead = task()
    await store.enqueue(ahead)  # queued, unclaimed
    assert await rig.manager.sweep(now=NOW) == 0
    assert not [r for r in store.rows.values() if r.kind == "maintenance"]
    assert (await store.get(ahead.id)).status == "queued"


async def test_a_submit_waits_for_cleanup_then_takes_the_scope(
        monkeypatch) -> None:
    """The coordination a bare read could never give: while cleanup HOLDS
    the maintenance row a real submit stays queued; once the segment
    closes, that same row can be granted — the successor runs only after
    the cleanup owner released the scope."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    rig = _wired(monkeypatch, tasks=store, segments=admission)
    _register(rig.runtime)
    rig.runtime.browser_running = True
    # Stall the pass mid-segment: its first guest read hangs.
    stall = asyncio.Event()
    orig_call = rig.cdp.call

    async def gated_call(method, params=None):
        await stall.wait()
        return await orig_call(method, params)

    rig.cdp.call = gated_call
    stall.clear()
    sweep_task = asyncio.ensure_future(rig.manager.sweep(now=NOW))
    try:
        for _ in range(200):
            maintenance = next(
                (r for r in store.rows.values() if r.kind == "maintenance"), None)
            if maintenance is not None and maintenance.status == "running":
                break
            await asyncio.sleep(0.01)
        maintenance = next(
            r for r in store.rows.values() if r.kind == "maintenance")
        assert maintenance.status == "running"
        queued = await admission.submit(scope=SCOPE, kind="native",
                                        payload={}, origin={})
        # The maintenance owner blocks the successor — the SAME claim, not
        # a second check that could see a stale state.
        assert not queued.granted
    finally:
        stall.set()
    assert await sweep_task == 0
    maintenance = await store.get(maintenance.id)
    assert maintenance is not None and maintenance.status in (
        "done", "unavailable")
    # The successor can now be granted and run its own segment.
    assert await store.try_claim_head(queued.task.id, now=NOW) is not None


async def test_a_leaked_queued_maintenance_row_is_closed_never_started() -> None:
    """A `maintenance` row is claimed by its own submitter in the same
    call it was written: still `queued` when the runner sees it means the
    submitter died between the two — the runner closes it, never
    executes it, and the queued work behind it proceeds."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    leaked = task(kind="maintenance")
    behind = task(created=NOW + timedelta(seconds=1))
    await store.enqueue(leaked)
    await store.enqueue(behind)
    await redis.xadd(_stream(SCOPE), {"task": str(leaked.id)})
    await redis.xadd(_stream(SCOPE), {"task": str(behind.id)})
    ran: list[str] = []

    async def executor(row: ComputerTask) -> dict:
        ran.append("native")
        return {"status": "ok"}

    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), watch_seconds=0.01,
        dispatch={"native": executor})
    assert await runner.tick() >= 0
    leaked_row = await store.get(leaked.id)
    assert leaked_row is not None and leaked_row.status == "cancelled"
    for _ in range(50):
        if (await store.get(behind.id)).status == "done":
            break
        await asyncio.sleep(0.01)
    assert (await store.get(behind.id)).status == "done"
    assert ran == ["native"]


# --- uncertain exits hold their owner until the guest reports quiet ----------


async def test_an_uncertain_fast_reply_holds_the_scope_until_quiet() -> None:
    """A reply that cannot say whether the guest ran it keeps the row
    RUNNING and the owner registered — no terminal write, no successor —
    until the scoped probe says `quiet`; then the REAL reply persists.
    A certain `done` reply closes immediately as the positive control."""
    quiet = asyncio.Event()
    verdict = {"v": "busy"}

    async def reconcile(scope: str) -> str:
        await quiet.wait()
        return verdict["v"]

    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(
        tasks=store, redis=redis, clock=clock(), reconcile=reconcile)

    async def uncertain() -> dict:
        return {"status": "unavailable", "uncertain": True,
                "note": "the computer could not be reached",
                "stdout": "partial"}

    got = await admission.submit(scope=SCOPE, kind="native", payload={},
                                 origin={})
    assert got.granted
    runner = asyncio.ensure_future(
        admission.run_segment(got.task, work=uncertain()))
    await asyncio.sleep(0.05)
    verdict["v"] = "quiet"
    quiet.set()
    result = await runner
    # The caller's reply is the reply, not an interrupted rewrite.
    assert result.get("stdout") == "partial"

    row = await store.get(got.task.id)
    assert row is not None and row.status == "unavailable"
    assert row.result.get("stdout") == "partial"
    assert row.result.get("uncertain") is True
    # The owner is gone only after the guest proved quiet.
    assert admission.executors.executor(got.task.id) is None

    # A successor can now be admitted; while the owner held it could not.
    nxt = await admission.submit(scope=SCOPE, kind="native", payload={},
                                 origin={})
    assert nxt.granted

    async def certain() -> dict:
        return {"status": "ok", "answer": "clean"}

    done = await admission.run_segment(nxt.task, work=certain())
    assert done.get("answer") == "clean"
    row = await store.get(nxt.task.id)
    assert row is not None and row.status == "done"
    assert row.result.get("answer") == "clean"


async def test_an_uncertain_reply_with_a_busy_guest_never_lets_go() -> None:
    """While the scoped probe stays `busy`, reconciliation never writes a
    terminal row — the scope cannot be stolen and nothing is replayed."""
    verdict = {"v": "busy"}

    async def reconcile(scope: str) -> str:
        return verdict["v"]

    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(
        tasks=store, redis=redis, clock=clock(), reconcile=reconcile)

    async def uncertain() -> dict:
        return {"status": "unavailable", "uncertain": True}

    got = await admission.submit(scope=SCOPE, kind="native", payload={},
                                 origin={})
    assert got.granted
    runner = asyncio.ensure_future(
        admission.run_segment(got.task, work=uncertain()))
    await asyncio.sleep(0.1)
    row = await store.get(got.task.id)
    assert row is not None and row.status == "running"
    assert row.result.get("answer") in (None, "")
    # No successor while the command may still exist.
    nxt = await admission.submit(scope=SCOPE, kind="native", payload={},
                                 origin={}, wait_seconds=0.0)
    assert not nxt.granted
    verdict["v"] = "quiet"
    result = await runner
    assert result.get("status") == "unavailable"
    row = await store.get(got.task.id)
    assert row is not None and row.status == "unavailable"


async def test_an_uncertain_queued_reply_holds_the_scope() -> None:
    """The deferred half obeys the same gate: an uncertain reply keeps the
    row running and the scope blocked while the guest is busy, and only a
    quiet verdict persists the REAL outcome — never a generic note."""
    verdict = {"v": "busy"}

    async def reconcile(scope: str) -> str:
        return verdict["v"]

    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    blocker = await admission.submit(scope=SCOPE, kind="native", payload={},
                                     origin={})
    queued = await admission.submit(scope=SCOPE, kind="native",
                                    payload={"tool": "computer_run",
                                             "args": {"command": "true"}},
                                    origin={}, wait_seconds=0.0)
    assert blocker.granted and not queued.granted
    await admission.finish(blocker.task.id, status="done", result={})

    async def executor(row: ComputerTask) -> dict:
        return {"status": "unavailable", "uncertain": True,
                "stdout": "kept-bytes"}

    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), watch_seconds=0.01,
        dispatch={"native": executor}, reconcile=reconcile)
    assert await runner.tick() == 1
    await asyncio.sleep(0.1)
    row = await store.get(queued.task.id)
    assert row is not None and row.status == "running"
    verdict["v"] = "quiet"
    for _ in range(60):
        row = await store.get(queued.task.id)
        if row is not None and row.status == "unavailable":
            break
        await asyncio.sleep(0.1)
    row = await store.get(queued.task.id)
    assert row is not None and row.status == "unavailable"
    assert row.result.get("stdout") == "kept-bytes"


async def test_a_failed_finish_keeps_the_result_with_its_owner() -> None:
    """The terminal write dropping the row keeps the REAL reply on the
    executor: the row is RUNNING and undelivered while the store is down,
    and reconciliation's retry persists the original result — stdout,
    handles and all — and only then marks it delivered."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(
        tasks=store, redis=redis, clock=clock(),
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    real_finish = store.finish
    fail = {"on": True}

    async def finish(*args, **kwargs):
        if fail["on"]:
            raise OSError("computer_task_store offline")
        return await real_finish(*args, **kwargs)

    store.finish = finish  # type: ignore[method-assign]

    async def work() -> dict:
        return {"status": "ok", "answer": "kept",
                "stdout": "x" * 4500, "exit_code": 0}

    got = await admission.submit(scope=SCOPE, kind="native", payload={},
                                 origin={})
    assert got.granted
    reply = await admission.run_segment(got.task, work=work())
    # The caller saw the real reply — it is never rewritten.
    assert reply.get("answer") == "kept"
    row = await store.get(got.task.id)
    assert row is not None and row.status == "running"
    assert row.delivered_at is None
    # The retained result is reachable with the executor that ran it.
    assert admission.executors.pending_result(got.task.id) is not None

    fail["on"] = False
    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), watch_seconds=0.01,
        executors=admission.executors,
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    fail["on"] = True
    for _ in range(2):
        await runner.tick()
        row = await store.get(got.task.id)
        assert row is not None and row.status == "running"
        assert row.delivered_at is None
        assert admission.executors.pending_result(got.task.id) is not None
    fail["on"] = False
    await runner.tick()
    row = await store.get(got.task.id)
    assert row is not None and row.status == "done"
    assert row.result.get("answer") == "kept"
    assert row.result.get("stdout") == "x" * 4500
    # A reply the caller already saw is persisted delivered — never
    # handed off a second time.
    assert row.delivered_at is not None
    assert admission.executors.pending_result(got.task.id) is None


async def test_a_failed_finish_on_the_runner_retries_before_delivery() -> None:
    """The deferred path keeps the typed reply when `finish` fails: the
    stream entry stays pending (never acked, never delivered) and the
    next reconcile persists the ORIGINAL result — not `interrupted`."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    blocker = await admission.submit(scope=SCOPE, kind="native", payload={},
                                     origin={})
    queued = await admission.submit(scope=SCOPE, kind="native",
                                    payload={"tool": "computer_run",
                                             "args": {"command": "true"}},
                                    origin={}, wait_seconds=0.0)
    assert blocker.granted and not queued.granted
    await admission.finish(blocker.task.id, status="done", result={})

    delivered: list[ComputerTask] = []

    async def collect(row: ComputerTask) -> None:
        delivered.append(row)
        await store.mark_delivered(row.id, now=NOW)

    real_finish = store.finish
    fail = {"on": True}

    async def finish(*args, **kwargs):
        if fail["on"] and kwargs.get("status") == "done":
            raise OSError("computer_task_store offline")
        return await real_finish(*args, **kwargs)

    store.finish = finish  # type: ignore[method-assign]

    async def executor(row: ComputerTask) -> dict:
        return {"status": "ok", "answer": "the deferred answer",
                "stdout": "kept-bytes"}

    runner = ComputerQueueRunner(
        tasks=store, redis=redis, clock=clock(), watch_seconds=0.01,
        on_terminal=collect, dispatch={"native": executor},
        reconcile=lambda scope: asyncio.sleep(0, "quiet"))
    await runner.tick()
    await asyncio.sleep(0.05)
    row = await store.get(queued.task.id)
    # The failed finish left the row RUNNING and undelivered — no
    # `interrupted` overwrite, no ack.
    assert row is not None and row.status == "running"
    assert row.delivered_at is None
    assert [d.id for d in delivered] == [blocker.task.id]

    for _ in range(2):
        await runner.tick()
        row = await store.get(queued.task.id)
        assert row is not None and row.status == "running"
        assert row.delivered_at is None
        assert [d.id for d in delivered] == [blocker.task.id]
    fail["on"] = False
    for _ in range(60):
        await runner.tick()
        if (await store.get(queued.task.id)).status == "done":
            break
        await asyncio.sleep(0.02)
    row = await store.get(queued.task.id)
    assert row is not None and row.status == "done"
    assert row.result.get("answer") == "the deferred answer"
    assert row.result.get("stdout") == "kept-bytes"
    assert [d.id for d in delivered][-1] == queued.task.id
    assert row.delivered_at is not None


async def test_poll_reports_the_browser_handle_tab_and_full_tail() -> None:
    """`poll` projects the row's typed result: the browser handle, the tab
    it ran on, and every byte of the retained stdout — no silent 4000 cut."""
    store = FakeComputerTaskStore()
    backend = ComputerTaskBackend(tasks=store)
    row = task(kind="browse")
    await store.enqueue(row)
    await store.claim(row.id, now=NOW)
    handle = f"h-{uuid.uuid4()}"
    stdout = "x" * 4500
    await store.finish(row.id, status="done", result={
        "task": handle, "tab": "tab-7", "status": "ok",
        "stdout": stdout, "url": "https://example.invalid/"}, now=NOW)
    done = await backend.poll(job_id=str(row.id))
    from nora.domain.ports import JobStatus
    assert done.status is JobStatus.SUCCEEDED
    assert handle in done.report and "tab-7" in done.report
    assert stdout in done.report


async def test_a_stale_attach_invalidates_the_binding_before_expired(
        monkeypatch) -> None:
    """`acquire` on a bound row whose target is gone expires the row AND
    drops the endpoint binding — the next call re-resolves the instance
    rather than routing a stale handle to a replacement VM."""
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owner = uuid.uuid4()
    async with rig.manager.acquire(SCOPE, owner) as bound:
        target = bound.tab.target_id
    assert target in rig.cdp.targets
    del rig.cdp.targets[target]
    with pytest.raises(TabUnavailable, match="expired"):
        async with rig.manager.acquire(SCOPE, owner, target_id=target):
            pass
    assert rig.runtime.invalidated == [SCOPE]
    stale = [r for r in rig.tabs.rows.values() if r.target_id == target]
    assert stale and all(r.status == "expired" for r in stale)


async def test_maintenance_cannot_run_beside_a_running_task() -> None:
    """The maintenance segment is claimed through the SAME FIFO as user
    work: while a task is running, `browser_stop` cannot be admitted —
    the guest reaper is not a second lifecycle owner."""
    store, redis = FakeComputerTaskStore(), FakeStreamRedis()
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
    first = await admission.submit(scope=SCOPE, kind="native", payload={},
                                   origin={})
    assert first.granted
    maint = await admission.submit(scope=SCOPE, kind="maintenance",
                                   payload={"op": "browser_stop"},
                                   origin={}, wait_seconds=0.0)
    assert not maint.granted
    assert maint.task.kind == "maintenance"
    await admission.cancel(maint.task.id)
    await admission.finish(first.task.id, status="done", result={})
    again = await admission.submit(scope=SCOPE, kind="maintenance",
                                   payload={"op": "browser_stop"},
                                   origin={})
    assert again.granted


async def test_confirmed_browser_stop_retires_full_capacity_before_a_new_task(monkeypatch):
    rig = _wired(monkeypatch)
    _register(rig.runtime)
    owners = [uuid.uuid4() for _ in range(MAX_PAGE_TARGETS)]
    for owner in owners:
        async with rig.manager.acquire(SCOPE, owner):
            pass
    assert len(await rig.tabs.open_for_scope(SCOPE, instance="boot-1")) == MAX_PAGE_TARGETS
    stops = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class Session:
        def __init__(self, *, timeout):
            assert timeout.total == 30

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def post(self, url, *, headers, json):
            assert url == "http://vm/v1/browser_stop"
            assert json == {"scope": SCOPE}
            assert headers["X-Computerd-Instance"] == "boot-1"
            stops.append(SCOPE)
            rig.cdp.targets.clear()
            rig.runtime.browser_running = False
            return Response()

    monkeypatch.setattr(mgr_mod.aiohttp, "ClientSession", Session)
    assert await rig.manager.browser_stop(SCOPE)
    assert stops == [SCOPE]
    assert not await rig.tabs.open_for_scope(SCOPE, instance="boot-1")
    with pytest.raises(TabUnavailable, match="expired"):
        async with rig.manager.acquire(SCOPE, owners[0], must_exist=True):
            pass
    async with rig.manager.acquire(SCOPE, uuid.uuid4()) as fresh:
        assert fresh.tab.target_id in rig.cdp.targets


async def test_pending_results_survive_a_restart(tmp_path) -> None:
    """A retained outcome is spilled to disk: a fresh `SegmentRegistry` (the
    "next process") sees both the original result and its `consumed` flag —
    so `_persist_pending` writes the row terminal AND marks it delivered
    instead of losing the result or re-delivering it."""
    from nora.app.computer_queue import SegmentRegistry
    path = tmp_path / "computer-pending-results.json"
    first = SegmentRegistry(pending_path=path)
    tid = uuid.uuid4()
    first.retain(tid, "done", {"status": "ok", "note": "written"})
    first.mark_consumed(tid)

    second = SegmentRegistry(pending_path=path)  # the restart
    pending = second.pending_result(tid)
    assert pending is not None and pending.status == "done" \
        and pending.result["note"] == "written" and pending.consumed

    second.clear_pending(tid)
    third = SegmentRegistry(pending_path=path)
    assert third.pending_result(tid) is None


async def test_a_retained_pending_is_persisted_terminal_and_delivered(tmp_path) -> None:
    """Restart scenario end to end: the SQL store was down at finish time,
    the caller saw the reply (`consumed`), the process died — a new
    registry's `_persist_pending` lands the ORIGINAL result terminal and
    stamps `delivered_at`, so deferred delivery never hands it out twice."""
    from nora.app.computer_queue import _persist_pending, SegmentRegistry
    path = tmp_path / "computer-pending-results.json"
    first = SegmentRegistry(pending_path=path)
    store = FakeComputerTaskStore()
    row = task()
    await store.enqueue(row)
    await store.try_claim_head(row.id, now=NOW)
    tid = row.id
    first.retain(tid, "done", {"status": "ok", "value": 42})
    first.mark_consumed(tid)

    second = SegmentRegistry(pending_path=path)  # the restart
    assert await _persist_pending(
        executors=second, tasks=store, clock=clock(), task_id=tid)
    fresh = await store.get(tid)
    assert fresh is not None and fresh.status == "done" \
        and fresh.result == {"status": "ok", "value": 42} \
        and fresh.delivered_at is not None
    assert second.pending_result(tid) is None
