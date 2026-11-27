"""`ComputeJobPoller` — one row failing must not strand the others delivered with it.

Jobs of one room are delivered as one occasion, so a fault in a single row (its poll, its
claim, its fetch, the turn itself, its closing write) is isolated to that row: the siblings
still deliver and end on their own state. The faults are injected at the collaborator seams
by thin subclasses; the clean control in each test is the sibling that must still finish.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, uuid5

import pytest

from nora.app.compute_poller import ComputeJobPoller
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.ports import JobResult, JobStatus
from nora.engagement.slots import ConversationSlots
from nora.obs.metrics import Metrics
from nora.policy import permissive_policy
from nora.testing import FakeComputeBackend, FakeComputeJobStore


def _job(name: str) -> ComputeJob:
    return ComputeJob(
        id=uuid5(NAMESPACE_DNS, name), backend_job_id=name, task=f"task {name}", artifact_id=None,
        platform="signal", conversation_id="conv-1", conversation_type="group",
        requester_person_id="person-1", created_at=datetime.now(UTC),
        state=ComputeJobState.PENDING, completed_at=None, result_url=None,
        result_summary=None, error=None, task_sha256="t", context_sha256="c", kind="subagent",
    )


class _Backend(FakeComputeBackend):
    def __init__(self, *, poll_raises: str = "") -> None:
        super().__init__()
        self._poll_raises = poll_raises

    async def poll(self, *, job_id):
        if job_id == self._poll_raises:
            raise RuntimeError("backend down")
        return await super().poll(job_id=job_id)


class _Store(FakeComputeJobStore):
    def __init__(self, *, claim_raises: str = "", mark_raises: str = "") -> None:
        super().__init__()
        self._claim_raises = uuid5(NAMESPACE_DNS, claim_raises) if claim_raises else None
        self._mark_raises = uuid5(NAMESPACE_DNS, mark_raises) if mark_raises else None

    async def claim(self, job_id):
        if job_id == self._claim_raises:
            raise RuntimeError("store down")
        return await super().claim(job_id)

    async def mark(self, job_id, state, **fields):
        if job_id == self._mark_raises:
            raise RuntimeError("write failed")
        return await super().mark(job_id, state, **fields)


async def _run(*, store, backend, speak):
    first, second = _job("a"), _job("b")
    for job in (first, second):
        await store.create(job)
        backend.set_result(
            job_id=job.backend_job_id, result=JobResult(status=JobStatus.SUCCEEDED, summary="ok"),
        )
    metrics = Metrics()
    poller = ComputeJobPoller(
        policy=permissive_policy(), store=store, backends={"subagent": backend}, speak=speak,
        slots=ConversationSlots(maxlen=16, spawn=lambda coro: None), metrics=metrics,
        tick_seconds=1.0, clock=lambda: datetime.now(UTC),
    )
    await poller.tick()
    return first, second, metrics


async def _sent(spoken):
    async def speak(occ):
        spoken.append(occ)
        return "sent"

    return speak


async def test_a_sibling_whose_poll_raises_does_not_discard_the_other_delivery():
    spoken: list = []
    store = _Store()
    first, second, _ = await _run(
        store=store, backend=_Backend(poll_raises="b"), speak=await _sent(spoken),
    )

    assert len(spoken) == 1 and "task a" in spoken[0].body
    assert (await store.get(first.id)).state is ComputeJobState.DONE
    assert (await store.get(second.id)).state is ComputeJobState.PENDING


async def test_a_sibling_whose_claim_raises_does_not_strand_the_claimed_row():
    spoken: list = []
    store = _Store(claim_raises="b")
    first, second, _ = await _run(store=store, backend=_Backend(), speak=await _sent(spoken))

    assert len(spoken) == 1 and "task a" in spoken[0].body
    assert (await store.get(first.id)).state is ComputeJobState.DONE
    assert (await store.get(second.id)).state is ComputeJobState.PENDING


async def test_a_turn_that_raises_ends_every_claimed_row_and_is_never_retried():
    calls = []

    async def speak(occ):
        calls.append(occ)
        raise RuntimeError("turn exploded")

    store = _Store()
    first, second, metrics = await _run(store=store, backend=_Backend(), speak=speak)

    assert [(await store.get(j.id)).state for j in (first, second)] == [ComputeJobState.FAILED] * 2
    assert metrics.get("nora_compute_jobs_delivered_total", outcome="failed_error") == 2
    assert await store.pending(limit=10) == []
    assert len(calls) == 1


@pytest.mark.parametrize("failing", ["a", "b"])
async def test_a_failed_closing_write_on_one_row_still_ends_the_other(failing):
    spoken: list = []
    store = _Store(mark_raises=failing)
    first, second, _ = await _run(store=store, backend=_Backend(), speak=await _sent(spoken))

    rows = {"a": first, "b": second}
    other = "b" if failing == "a" else "a"
    assert (await store.get(rows[other].id)).state is ComputeJobState.DONE
    assert len(spoken) == 1
