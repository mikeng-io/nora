"""`ComputeJobPoller` — a finished compute job, delivered through
`FirePath.speak`, with no real model call. A stub `speak` that
answers an outcome string, a real `ConversationSlots` (its
`spawn` is a no-op — nothing here ever queues), and a real `Metrics()`.

`fake_slots`/`fake_metrics` below are the construction
`ConversationSlots(maxlen=..., spawn=lambda c: None)` and `Metrics()`, the same
one `tests/unit/test_heartbeat_loop.py` builds inline per test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

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
        "task_sha256": "hash-task", "context_sha256": "hash-context", "kind": "subagent",
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
        store=store, backends={"subagent": backend}, speak=lambda occ: spoken.append(occ) or "sent",
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
        store=store, backends={"subagent": backend}, speak=speak, slots=fake_slots,
        metrics=fake_metrics, tick_seconds=1.0, clock=lambda: datetime.now(UTC),
    )
    await poller.tick()
    assert len(spoken) == 1
    assert "a haiku" in spoken[0].body
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.DONE
    assert fetched.result_url == "https://x/drop/1"


async def test_a_job_never_delivered_twice(fake_slots, fake_metrics):
    """`claim()` is a compare-and-set: a row another claimant already won is not
    delivered.

    Calling `tick()` twice would not test it: by the second `tick()` the job is
    `DONE` and `pending()` excludes it, so `_maybe_deliver` is not called again.
    Racing two concurrent `_maybe_deliver` calls would not either: it acquires the
    conversation SLOT before it claims the row, so the slot gate blocks the second
    call whether or not `claim`'s own CAS works.

    So the test PRE-CLAIMS the row directly (the state `_maybe_deliver` must
    respect), then calls `_maybe_deliver` on the still-PENDING-looking `job` value
    and confirms it never calls `speak`. A `claim()` that unconditionally returned
    `True` would proceed to deliver a row something else already claimed.
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
        store=store, backends={"subagent": backend}, speak=speak, slots=fake_slots,
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

    A row left alone instead of `FAILED` fails the
    state assertion; the counter assertion catches a row marked terminal without
    the series ever saying it happened."""
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
        store=store, backends={"subagent": backend}, speak=speak, slots=fake_slots,
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

    The counter is the point of this test, not the deferral. A deferral
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
        store=store, backends={"subagent": backend}, speak=speak, slots=fake_slots,
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

    An in-process backend's job registry is IN-MEMORY, so a `PENDING` row that
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

    # The ROWS, not a count — boot recovery returns them so each requester can
    # be told she lost track of their job. They were promised «I'm working on
    # it».
    assert len(swept) == 2
    assert all(j.conversation_id and j.requester_person_id for j in swept), (
        "a swept row must carry WHERE and WHO, or nobody can be told"
    )
    assert await store.pending(limit=10) == []
    still_pending = await store.get(stranded.id)
    assert still_pending.state is ComputeJobState.FAILED
    assert "restart" in still_pending.error
    assert "unknown job_id" not in still_pending.error
    assert (await store.get(crashed.id)).state is ComputeJobState.FAILED
    assert fake_metrics.get(
        "nora_compute_jobs_delivered_total", outcome="failed_error",
    ) == 2


# ---------------------------------------------------------------------------
# telling the person who was left waiting
# ---------------------------------------------------------------------------


async def test_a_lost_job_says_it_lost_track_and_never_that_it_failed():
    """The promise this closes, and the claim it must not make.

    A requester was told «I'm working on it» and then the process restarted.
    Sweeping the row terminal is right, and NOT saying the job failed is right
    — the handle lived in an in-memory registry and the container may have
    finished perfectly well. Together they would tell the requester nothing.

    So what is spoken is R-27's `unavailable` — «I lost track of it» — which is
    the only statement that is true. The header must forbid both other claims
    explicitly, because a model handed «the job is over» will otherwise pick
    one.
    """
    from nora.app.fire import lost_job_occasion

    job = _pending_job()
    occasion = lost_job_occasion(job)

    assert occasion.platform == job.platform
    assert occasion.conversation_id == job.conversation_id
    assert occasion.person_id == job.requester_person_id
    assert occasion.kind == "compute_lost"

    header = occasion.header(None, datetime.now(UTC)).lower()
    assert "lost" in header
    assert "do not say it failed" in header
    assert "do not say it worked" in header


# ---------------------------------------------------------------------------
# the outbox is generic: a row is delivered by the backend its `kind` names
# ---------------------------------------------------------------------------


def _poller(store, backends, fake_slots, fake_metrics, spoken, documents=None):
    async def speak(occ):
        spoken.append(occ)
        return "sent"

    return ComputeJobPoller(
        store=store, backends=backends, speak=speak, slots=fake_slots,
        metrics=fake_metrics, tick_seconds=1.0, clock=lambda: datetime.now(UTC),
        documents=documents,
    )


async def test_each_row_is_polled_by_the_backend_of_its_own_kind(fake_slots, fake_metrics):
    store = FakeComputeJobStore()
    await store.create(_pending_job(kind="subagent", backend_job_id="s-1"))
    await store.create(_pending_job(kind="video", backend_job_id="v-1"))
    subagent, video = FakeComputeBackend(), FakeComputeBackend()
    subagent.set_result(
        job_id="s-1", result=JobResult(JobStatus.SUCCEEDED, summary="from subagent"),
    )
    video.set_result(job_id="v-1", result=JobResult(JobStatus.SUCCEEDED, summary="from video"))
    spoken: list = []
    poller = _poller(
        store, {"subagent": subagent, "video": video}, fake_slots, fake_metrics, spoken,
    )

    await poller.tick()

    assert sorted(("from subagent" in o.body, "from video" in o.body) for o in spoken) == [
        (False, True), (True, False),
    ]
    assert await store.pending(limit=10) == []


async def test_a_row_whose_kind_has_no_backend_is_left_pending_not_failed(
    fake_slots, fake_metrics, caplog,
):
    store = FakeComputeJobStore()
    orphan = _pending_job(kind="video", backend_job_id="v-1")
    owned = _pending_job(kind="subagent", backend_job_id="s-1")
    await store.create(orphan)
    await store.create(owned)
    backend = FakeComputeBackend()
    backend.set_result(job_id="s-1", result=JobResult(JobStatus.SUCCEEDED, summary="ok"))
    spoken: list = []
    poller = _poller(store, {"subagent": backend}, fake_slots, fake_metrics, spoken)

    await poller.tick()
    await poller.tick()

    assert (await store.get(orphan.id)).state is ComputeJobState.PENDING
    assert (await store.get(owned.id)).state is ComputeJobState.DONE   # positive control
    assert len(spoken) == 1
    assert sum("no backend is wired" in r.message for r in caplog.records) == 1   # once


async def test_boot_recovery_still_sweeps_pending_and_firing_rows_of_any_kind(fake_metrics):
    store = FakeComputeJobStore()
    pending = _pending_job(kind="video")
    firing = _pending_job(kind="subagent", backend_job_id="job_2")
    await store.create(pending)
    await store.create(firing)
    await store.claim(firing.id)

    swept = await recover(store=store, metrics=fake_metrics, now=datetime.now(UTC))

    assert {j.id for j in swept} == {pending.id, firing.id}
    assert await store.pending(limit=10) == []


class _Documents:
    """What the document store answers: `ok` for the ids it holds, `refused` for the rest."""

    def __init__(self, held: dict[str, str]) -> None:
        self._held = held

    async def read(self, *, platform, conversation_id, document_id=None, handle=""):
        key = str(document_id)
        if key not in self._held:
            return SimpleNamespace(status="refused", title="", url="")
        return SimpleNamespace(status="ok", title=f"title-{key[-4:]}", url=self._held[key])


async def test_delivery_reads_each_document_back_and_names_a_missing_one(
    fake_slots, fake_metrics,
):
    here, gone = UUID(int=1), UUID(int=2)
    url = f"https://nora.example/documents/{here.hex}"
    store = FakeComputeJobStore()
    await store.create(_pending_job())
    backend = FakeComputeBackend()
    backend.set_result(job_id="job_1", result=JobResult(
        JobStatus.SUCCEEDED, report="two sheets made", document_ids=(str(here), str(gone)),
    ))
    spoken: list = []
    poller = _poller(
        store, {"subagent": backend}, fake_slots, fake_metrics, spoken,
        documents=_Documents({str(here): url}),
    )

    await poller.tick()

    body = spoken[0].body
    assert "two sheets made" in body
    assert url in body
    assert f"{gone}" in body and "could not be found" in body
    assert spoken[0].document_urls == (url,)   # only the one that read back


async def test_delivery_keeps_the_report_and_document_ids_on_the_row(fake_slots, fake_metrics):
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(job_id="job_1", result=JobResult(
        JobStatus.SUCCEEDED, report="made it", document_ids=("d1",),
    ))
    poller = _poller(store, {"subagent": backend}, fake_slots, fake_metrics, [])

    await poller.tick()

    row = await store.get(job.id)
    assert (row.result_report, row.result_document_ids) == ("made it", ("d1",))


async def test_delivery_writes_the_workers_trace_id_on_the_row(fake_slots, fake_metrics):
    """A finished job links to every call its worker made."""
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(job_id="job_1", result=JobResult(
        JobStatus.SUCCEEDED, report="made it", trace_id="t-1",
    ))
    poller = _poller(store, {"subagent": backend}, fake_slots, fake_metrics, [])

    await poller.tick()

    assert (await store.get(job.id)).trace_id == "t-1"
