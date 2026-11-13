"""Integrated review regressions over generated local collaborators; no guest/model calls."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nora.agent.registry import ToolRegistry
from nora.app.composition_helpers import _note_unit_of
from nora.app.computer_dispatch import ComputerToolDispatch
from nora.app.computer_queue import (
    ComputerAdmission,
    ComputerQueueRunner,
    SegmentRegistry,
    current_segment,
)
from nora.app.runtime import ComputerLifecycle
from nora.domain.computer import computer_unit_scope
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import Conversation, Policy
from nora.testing import (
    FakeBlobStore,
    FakeComputerEventStore,
    FakeComputerTaskStore,
    FakeSandboxRuntime,
    FakeStreamRedis,
)

NOW = datetime(2026, 2, 1, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


async def test_real_lifecycle_closes_real_runner(tmp_path):
    registry = SegmentRegistry(pending_dir=tmp_path / "journal")
    runner = ComputerQueueRunner(
        tasks=FakeComputerTaskStore(),
        redis=FakeStreamRedis(),
        clock=lambda: NOW,
        executors=registry,
    )
    lifecycle = ComputerLifecycle(runner=runner, metrics=Metrics(), sweep_seconds=30)
    try:
        await lifecycle.close()
        assert registry._lock_fd is None
    finally:
        registry.close()


async def test_lifecycle_timeout_retains_journal_and_close_can_retry(tmp_path):
    from uuid import uuid4

    registry = SegmentRegistry(pending_dir=tmp_path / "journal")
    runner = ComputerQueueRunner(tasks=FakeComputerTaskStore(), redis=FakeStreamRedis(),
                                 clock=lambda: NOW, executors=registry)
    lifecycle = ComputerLifecycle(runner=runner, metrics=Metrics(), sweep_seconds=30)
    started, release = asyncio.Event(), asyncio.Event()

    async def draining():
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    worker = asyncio.create_task(draining())
    registry.register(uuid4(), worker)
    try:
        await started.wait()
        with pytest.raises(TimeoutError):
            await lifecycle.close(settle_seconds=0)
        assert registry._lock_fd is not None
        assert not registry.accepting
        with pytest.raises(RuntimeError, match="second owner"):
            SegmentRegistry(pending_dir=tmp_path / "journal")
    finally:
        release.set()
        await worker
        await lifecycle.close()
    assert registry._lock_fd is None


@pytest.mark.parametrize("origin_extra", [{}, {"grant": [], "requester_person_id": "remaining"}])
async def test_dispatch_anonymous_revocation_and_empty_grant(tmp_path, origin_extra):
    identity = tmp_path / "identity.toml"
    identity.write_text('[persons.remaining]\nfirst_name="Generated"\n')
    persons = load_persons(identity)
    cid, unit = "generated-room", "generated-unit"
    # An unmapped tag sender was admitted when persons="*". Current room is restricted.
    convo = Conversation(
        platform="signal",
        ids=frozenset({cid}),
        persons=frozenset({"remaining"}),
        label="Generated",
        unit=unit,
    )
    policy = Policy(conversations=(convo,), _by_id={("signal", cid): convo})
    assert not policy.is_allowed(platform="signal", conversation_id=cid, person_id=None)
    store, runtime = FakeComputerTaskStore(), FakeSandboxRuntime()
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(), clock=lambda: NOW)
    registry = ToolRegistry(
        unit_for=_note_unit_of(policy),
        blobs=FakeBlobStore(),
        computer=runtime,
        computer_events=FakeComputerEventStore(),
        computer_admission=admission,
        clock=lambda: NOW,
    )
    row = (
        await admission.submit(
            scope=computer_unit_scope(unit),
            kind="native",
            payload={
                "tool": "computer_write",
                "args": {"path": "generated.txt", "content": "generated"},
            },
            origin={
                "platform": "signal",
                "conversation_id": cid,
                "conversation_type": "group",
                "requester_person_id": None,
                "actor": "tag",
                **origin_extra,
            },
        )
    ).task
    dispatch = ComputerToolDispatch(
        registry=registry,
        store=None,
        persons=persons,
        honcho=None,
        metrics=Metrics(),
        policy=policy,
        scope_for=lambda *a: row.scope,
    )
    admission.executors.register(row.id, asyncio.current_task())
    token = current_segment.set(row.id)
    try:
        result = await dispatch(row)
    finally:
        current_segment.reset(token)
        admission.executors.unregister(row.id, asyncio.current_task())
    assert result["status"] == "unavailable" and not runtime.files_by_scope, (
        result,
        runtime.files_by_scope,
    )


async def test_cancel_browse_closes_mapped_browser_task(tmp_path):
    from nora.agent.room import Room

    identity = tmp_path / "identity.toml"
    identity.write_text('[persons.generated]\nfirst_name="Generated"\n')
    persons = load_persons(identity)
    store = FakeComputerTaskStore()
    admission = ComputerAdmission(tasks=store, redis=FakeStreamRedis(), clock=lambda: NOW)
    manager = SimpleNamespace(close_task=AsyncMock(return_value=1))
    runtime = FakeSandboxRuntime()
    registry = ToolRegistry(
        unit_for=lambda *a: "generated-unit",
        blobs=FakeBlobStore(),
        computer=runtime,
        computer_events=FakeComputerEventStore(),
        computer_admission=admission,
        browser_manager=manager,
        clock=lambda: NOW,
    )
    room = Room(
        platform="signal", conversation_id="generated-room", persons=persons, roster_source=None
    )
    specs = registry.specs(
        store=None,
        conversation_id="generated-room",
        conversation_type="group",
        persons=persons,
        honcho=None,
        deliverer=None,
        metrics=Metrics(),
        room=room,
    )
    scope = computer_unit_scope("generated-unit")
    await admission.submit(scope=scope, kind="native", payload={}, origin={})
    queued = await admission.submit(
        scope=scope,
        kind="browse",
        payload={"tool": "computer_browse", "args": {"task": "original-browser-handle"}},
        origin={},
    )
    assert not queued.granted
    result = await next(s for s in specs if s.name == "computer_cancel").call(
        task=str(queued.task.id)
    )
    assert result["status"] == "ok"
    assert manager.close_task.await_count == 1, result
