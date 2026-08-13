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

from nora.app.compute_poller import ComputeJobPoller, recover
from nora.app.fire import FAILED_POLICY
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
        "task_sha256": "hash-task", "context_sha256": "hash-context",
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


async def test_a_turn_that_did_not_speak_still_ends_the_job(fake_slots, fake_metrics):
    """`speak` returning anything but `SENT` — a policy refusal, a timeout, a
    turn that raised — is still TERMINAL for the row. One job, one delivery,
    ever: leaving it FIRING would be a row no tick ever picks up again (only
    `pending()` is polled) and no boot recovery explains, and re-delivering it
    would break at-most-once. `FAILED` with the outcome recorded is the only
    honest end.

    Reverting the `else FAILED` half to «leave it alone» turns this red on the
    state assertion; the counter assertion catches the other half, a row
    marked terminal without the series ever saying it happened."""
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(
        job_id="job_1",
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="x"),
        polls_before_done=0,
    )

    async def speak(occ):
        return FAILED_POLICY

    poller = ComputeJobPoller(
        store=store, backend=backend, speak=speak, slots=fake_slots,
        metrics=fake_metrics, tick_seconds=1.0, clock=lambda: datetime.now(UTC),
    )
    await poller.tick()

    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.FAILED
    assert fetched.completed_at is not None
    assert fake_metrics.get(
        "nora_compute_jobs_delivered_total", outcome=FAILED_POLICY,
    ) == 1
    # And it is gone from the poller's own batch — never a second attempt.
    assert await store.pending(limit=10) == []


async def test_a_held_slot_defers_the_delivery_and_says_so_in_the_series(
    fake_slots, fake_metrics,
):
    """A tag owns its conversation, so a finished job WAITS rather than
    barging in — the heartbeat's own rule. Nothing is claimed and nothing is
    said, so the row stays PENDING and the next tick tries again.

    The counter is the point of this test, not the deferral (which
    `VersionAnnouncer` established and this poller already did). A deferral
    that only logs is invisible to whoever is watching `nora_compute_jobs_
    delivered_total`: a slot that never frees looks EXACTLY like a job that
    never finished — both are silence on every series — and that is the one
    distinction an operator needs to tell "compute is broken" from "the room
    is busy"."""
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
    # A tag is holding this conversation, exactly as `try_acquire` sees it.
    assert fake_slots.try_acquire((job.platform, job.conversation_id)) is True

    await poller.tick()

    assert spoken == []
    assert fake_metrics.get(
        "nora_compute_jobs_delivered_total", outcome="deferred",
    ) == 1
    # Deferred is the one non-terminal outcome: the row is untouched.
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.PENDING


async def test_boot_recovery_fails_pending_rows_a_restart_orphaned(fake_metrics):
    """The restart defect, at the seam that fixes it.

    `LocalDockerBackend`'s job registry is IN-MEMORY, so a `PENDING` row that
    survives any restart — a crash, a deploy, a config change — names a
    `backend_job_id` the new process has never heard of. Its `poll` answers
    `FAILED("unknown job_id")`, which the poller would claim, deliver, and
    Nora would relay in her own voice as «your job failed». Nobody knows that:
    the container may have finished and registered its artifact perfectly.

    So boot recovery resolves those rows itself, before the first tick, with
    the one thing that IS true. The recorded reason is asserted because it is
    the difference between an honest report and a fabricated one — and it must
    NOT be the backend's "unknown job_id", which describes a lookup, not what
    happened."""
    store = FakeComputeJobStore()
    stranded = _pending_job()
    await store.create(stranded)
    crashed = _pending_job(backend_job_id="job_2")
    await store.create(crashed)
    await store.claim(crashed.id)   # FIRING: the older, already-handled case

    swept = await recover(store=store, metrics=fake_metrics, now=datetime.now(UTC))

    assert swept == 2
    assert await store.pending(limit=10) == []
    still_pending = await store.get(stranded.id)
    assert still_pending.state is ComputeJobState.FAILED
    assert "restart" in still_pending.error
    assert "unknown job_id" not in still_pending.error
    assert (await store.get(crashed.id)).state is ComputeJobState.FAILED
    assert fake_metrics.get(
        "nora_compute_jobs_delivered_total", outcome="failed_error",
    ) == 2
