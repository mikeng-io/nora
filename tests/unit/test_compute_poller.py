"""`ComputeJobPoller` — a finished compute job, delivered through
`FirePath.speak`, with no real model call. Mirrors `test_version_announcer.py`'s
own shape for exercising a `speak`-consuming poller: a stub `speak` that
answers one of §8.1's four outcome strings, a real `ConversationSlots` (its
`spawn` is a no-op — nothing here ever queues), and a real `Metrics()`.

Neither `tests/unit/test_version_announcer.py` nor `tests/unit/
test_heartbeat_loop.py` defines a SHARED `fake_slots`/`fake_metrics`
fixture — both build `ConversationSlots(maxlen=..., spawn=lambda c: None)`
and `Metrics()` inline, per test. `fake_slots`/`fake_metrics` below are that
same construction, lifted into local fixtures once rather than repeated in
every test in this module — not a third invented double.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.app.compute_poller import ComputeJobPoller
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.ports import JobResult, JobStatus
from nora.engagement.slots import ConversationSlots
from nora.obs.metrics import Metrics
from nora.testing import FakeComputeBackend, FakeComputeJobStore


@pytest.fixture
def fake_slots() -> ConversationSlots:
    return ConversationSlots(maxlen=16, spawn=lambda coro: None)


@pytest.fixture
def fake_metrics() -> Metrics:
    return Metrics()


def _pending_job(**overrides):
    defaults = {
        "id": uuid4(), "backend_job_id": "job_1", "task": "write a haiku",
        "artifact_id": None, "platform": "signal", "conversation_id": "conv-1",
        "conversation_type": "group", "requester_person_id": "person-1",
        "created_at": datetime.now(UTC), "state": ComputeJobState.PENDING,
        "completed_at": None, "result_url": None, "result_summary": None, "error": None,
    }   # a dict LITERAL: ruff's C4 ruleset is enabled and C408 rejects dict()
    defaults.update(overrides)
    return ComputeJob(**defaults)


async def test_a_still_running_job_is_left_pending(fake_slots, fake_metrics):
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(
        job_id="job_1", result=JobResult(status=JobStatus.SUCCEEDED), polls_before_done=99,
    )
    spoken = []
    poller = ComputeJobPoller(
        store=store, backend=backend, speak=lambda occ: spoken.append(occ) or "sent",
        slots=fake_slots, metrics=fake_metrics, tick_seconds=1.0,
        clock=lambda: datetime.now(UTC),
    )
    await poller.tick()
    assert not spoken
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.PENDING


async def test_a_finished_job_is_delivered_and_marked_done(fake_slots, fake_metrics):
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(
        job_id="job_1",
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="a haiku"),
        polls_before_done=0,
    )
    spoken = []

    async def speak(occ):
        spoken.append(occ)
        return "sent"

    poller = ComputeJobPoller(
        store=store, backend=backend, speak=speak, slots=fake_slots,
        metrics=fake_metrics, tick_seconds=1.0, clock=lambda: datetime.now(UTC),
    )
    await poller.tick()
    assert len(spoken) == 1
    assert "a haiku" in spoken[0].body
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.DONE
    assert fetched.result_url == "https://x/drop/1"


async def test_a_job_never_delivered_twice(fake_slots, fake_metrics):
    """The compare-and-set claim (Global Constraints), tested directly rather
    than by calling `tick()` twice sequentially. An earlier draft of this
    test did exactly that — but by the second `tick()`, the job is already
    `DONE` and `pending()` correctly excludes it, so `_maybe_deliver` is
    never even called a second time; the test would pass identically even if
    `claim()` had no compare-and-set at all (i.e. always returned `True`),
    which is precisely the defect this test exists to catch (CLAUDE.md: "if
    reverting the code under test doesn't turn the test red, the test is
    decorative"). Racing two concurrent `_maybe_deliver` calls doesn't work
    either — `_maybe_deliver` acquires the conversation SLOT before it claims
    the row, so a second concurrent call would already be blocked by the slot
    gate regardless of whether `claim`'s own CAS works, testing the wrong
    thing.

    The real test: PRE-CLAIM the row directly (simulating "a claim already
    won it," the exact state `_maybe_deliver` must respect), then call
    `_maybe_deliver` on the still-PENDING-looking `job` value and confirm it
    never calls `speak`. If `claim()` had no compare-and-set — i.e. it
    unconditionally returned `True` regardless of the row's current state —
    this test fails, because `_maybe_deliver` would proceed to deliver a row
    something else already claimed.
    """
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(
        job_id="job_1",
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="x"),
        polls_before_done=0,
    )
    spoken = []

    async def speak(occ):
        spoken.append(occ)
        return "sent"

    poller = ComputeJobPoller(
        store=store, backend=backend, speak=speak, slots=fake_slots,
        metrics=fake_metrics, tick_seconds=1.0, clock=lambda: datetime.now(UTC),
    )

    # Simulate another poller pass (or another process) having already won
    # the claim on this exact row, between this test's own `poll` seeing it
    # SUCCEEDED and `_maybe_deliver` reaching its own `claim` call.
    assert await store.claim(job.id) is True

    await poller._maybe_deliver(job)
    assert spoken == []
