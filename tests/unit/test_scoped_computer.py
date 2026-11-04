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
    token = current_segment.set(uuid.uuid4())
    try:
        got = await admission.submit(scope=SCOPE, kind="native", payload={}, origin={})
    finally:
        current_segment.reset(token)
    assert got.inherited and not got.granted
    # No row, no stream entry: a child never queues behind its parent.
    assert not store.rows
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
    for _ in range(50):
        if delivered:
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
        tasks=store, redis=redis, clock=clock(), watch_seconds=0.01)
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
    `put` — the endpoint registry decides resolution."""

    def __init__(self, endpoints: FakeScopeEndpoints, *, instance: str) -> None:
        self.endpoints = endpoints
        self.instance = instance
        self.puts: dict[str, bytes] = {}

    async def binding(self, scope):
        ep = await self.endpoints.resolve(scope)
        if ep is None:
            return None
        return ScopeBinding(endpoint=ep, runtime=None, instance=self.instance)

    async def put(self, *, scope, path, data):
        self.puts[path] = data
        return OpResult(status=SandboxStatus.OK)

    async def state(self, *, scope):
        return StateResult(status=SandboxStatus.OK,
                           browser={"running": False})


@dataclass
class _Rig:
    manager: ScopeBrowserManager
    tabs: FakeBrowserTabStore
    cdp: _Cdp
    runtime: _FakeScopedRuntime


def _wired(monkeypatch, *, instance="boot-1") -> _Rig:
    """A manager whose CDP/browser seams are the in-memory pair above."""
    cdp = _Cdp()
    endpoints = FakeScopeEndpoints()
    runtime = _FakeScopedRuntime(endpoints, instance=instance)
    tabs = FakeBrowserTabStore()

    @contextlib.asynccontextmanager
    async def fake_cdp(*, base_url, token, scope):
        yield cdp

    @contextlib.asynccontextmanager
    async def fake_tab(*, base_url, token, scope, target_id):
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
            runtime=runtime, tabs=tabs,
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
    rig.runtime.instance = "boot-2"
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
    admission = ComputerAdmission(tasks=store, redis=redis, clock=clock())
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
    rig.runtime.instance = "boot-2"
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
             clock_fn=clock()):
    """A ScopedComputerdRuntime whose /v1/identity answer is `identity`."""
    from nora.infra.computer.scoped import ScopedComputerdRuntime
    runtime = ScopedComputerdRuntime(endpoints=endpoints, token="t", clock=clock_fn)

    async def fake_identity(endpoint):
        return identity

    runtime._identity = fake_identity  # noqa: SLF001 - the seam is the point
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
    runtime = ScopedComputerdRuntime(endpoints=endpoints, token="t", clock=clock())

    async def counting(endpoint):
        calls.append("identity")
        return {"scope": SCOPE, "instance": "boot-1"}

    runtime._identity = counting  # noqa: SLF001
    assert await runtime.binding(SCOPE) is not None
    assert await runtime.binding(SCOPE) is not None
    assert len(calls) == 1  # cached within the revalidation window
    runtime.invalidate(SCOPE)
    assert await runtime.binding(SCOPE) is not None
    assert len(calls) == 2  # re-verified, never trusted twice
