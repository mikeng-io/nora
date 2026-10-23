"""`ComputeJobPoller` — a finished compute job, delivered through
`FirePath.speak`, with no real model call. A stub `speak` that
answers an outcome string, a real `ConversationSlots` (its
`spawn` is a no-op — nothing here ever queues), and a real `Metrics()`.

`fake_slots`/`fake_metrics` below are the construction
`ConversationSlots(maxlen=..., spawn=lambda c: None)` and `Metrics()`, the same
one `tests/unit/test_heartbeat_loop.py` builds inline per test.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from nora.app.compute_poller import ComputeJobPoller, recover
from nora.app.fire import FAILED_POLICY
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.ports import JobResult, JobStatus
from nora.engagement.slots import ConversationSlots
from nora.obs.metrics import Metrics
from nora.policy import Policy, load_policy, permissive_policy
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
        policy=permissive_policy(),
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
        policy=permissive_policy(),
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
        policy=permissive_policy(),
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
        policy=permissive_policy(),
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
        policy=permissive_policy(),
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


def _poller(store, backends, fake_slots, fake_metrics, spoken, documents=None, policy=None):
    async def speak(occ):
        spoken.append(occ)
        return "sent"

    return ComputeJobPoller(
        policy=policy if policy is not None else permissive_policy(),
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

    async def read(
        self, *, platform, conversation_id, document_id=None, handle="", person_id=None,
    ):
        key = str(document_id)
        if key not in self._held:
            return SimpleNamespace(status="refused", title="", url="")
        return SimpleNamespace(status="ok", title=f"title-{key[-4:]}", url=self._held[key])


async def test_delivery_reads_each_document_back_and_names_a_missing_one(
    fake_slots, fake_metrics,
):
    # A one-to-one job: the requester is the whole audience, so the missing one is named.
    here, gone = UUID(int=1), UUID(int=2)
    url = f"https://nora.example/documents/{here.hex}"
    store = FakeComputeJobStore()
    await store.create(_pending_job(conversation_type="dm"))
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


class _Readable:
    """Authorization-sensitive, as `DocumentService.read` is: a document reads `ok` only for the
    people in `readers`, and is `refused` (no title, no link) for anyone else."""

    def __init__(self, *, document: UUID, title: str, url: str, readers: frozenset[str]) -> None:
        self.document, self.title, self.url, self.readers = document, title, url, readers

    async def read(
        self, *, platform, conversation_id, document_id=None, handle="", person_id=None,
    ):
        if document_id == self.document and person_id in self.readers:
            return SimpleNamespace(status="ok", title=self.title, url=self.url)
        return SimpleNamespace(status="refused", title="", url="")


async def _spoken_for(
    *, conversation_type, policy, documents, document_ids, fake_slots, fake_metrics,
) -> list:
    store = FakeComputeJobStore()
    await store.create(_pending_job(conversation_type=conversation_type, requester_person_id="ken"))
    backend = FakeComputeBackend()
    backend.set_result(job_id="job_1", result=JobResult(
        JobStatus.SUCCEEDED, report="two sheets made", document_ids=document_ids,
    ))
    spoken: list = []
    poller = _poller(
        store, {"subagent": backend}, fake_slots, fake_metrics, spoken,
        documents=documents, policy=policy,
    )
    await poller.tick()
    return spoken


async def test_a_room_event_shows_no_title_link_or_id_of_a_document_only_its_creator_reads(
    fake_slots, fake_metrics,
):
    """Ken's job made a document only Ken may read, and the room is `"*"`, which names nobody.
    The event goes to the room, so it carries none of the title, the link or the id. The same
    job one-to-one, to Ken, carries all three: the positive control."""
    doc = UUID(int=1)
    url = f"https://nora.example/documents/{doc.hex[:12]}"
    creator_only = _Readable(
        document=doc, title="Ken's private title", url=url, readers=frozenset({"ken"}),
    )
    room = await _spoken_for(
        conversation_type="group", policy=_policy(persons='"*"'), documents=creator_only,
        document_ids=(str(doc),), fake_slots=fake_slots, fake_metrics=fake_metrics,
    )
    assert len(room) == 1 and "two sheets made" in room[0].body   # the job's text still lands
    assert "Ken's private title" not in room[0].body
    assert url not in room[0].body and doc.hex[:12] not in room[0].body
    assert str(doc) not in room[0].body and "could not be found" not in room[0].body
    assert room[0].document_urls == ()

    own = await _spoken_for(
        conversation_type="dm", policy=_policy(persons='["ken"]'), documents=creator_only,
        document_ids=(str(doc),), fake_slots=fake_slots, fake_metrics=fake_metrics,
    )
    assert "Ken's private title" in own[0].body and url in own[0].body   # positive control
    assert own[0].document_urls == (url,)


async def test_a_room_event_shows_a_document_the_whole_room_names_can_read(
    fake_slots, fake_metrics,
):
    """The same document, in a room that names Ken and Mei, both of whom read it: the room
    is its audience, so the title and link ride with the event (the positive control)."""
    doc = UUID(int=1)
    url = f"https://nora.example/documents/{doc.hex[:12]}"
    shared = _Readable(
        document=doc, title="Shared title", url=url, readers=frozenset({"ken", "mei"}),
    )
    room = await _spoken_for(
        conversation_type="group", policy=_policy(persons='["ken", "mei"]'),
        documents=shared, document_ids=(str(doc),),
        fake_slots=fake_slots, fake_metrics=fake_metrics,
    )
    assert "Shared title" in room[0].body and url in room[0].body
    assert room[0].document_urls == (url,)


async def test_a_failed_read_in_a_room_leaves_no_id_in_the_event(fake_slots, fake_metrics):
    """A document that reads back shows its title and link; the one that does not leaves no id,
    no link and no «could not be found» line in a room event. One-to-one, the requester still
    hears the id named missing, as before."""
    doc, ghost = UUID(int=1), UUID("abcdef01-2345-6789-abcd-ef0123456789")
    url = f"https://nora.example/documents/{doc.hex[:12]}"
    shared = _Readable(
        document=doc, title="Shared title", url=url, readers=frozenset({"ken", "mei"}),
    )
    room = await _spoken_for(
        conversation_type="group", policy=_policy(persons='["ken", "mei"]'),
        documents=shared, document_ids=(str(doc), str(ghost)),
        fake_slots=fake_slots, fake_metrics=fake_metrics,
    )
    assert "Shared title" in room[0].body                          # positive control
    assert str(ghost) not in room[0].body and ghost.hex[:12] not in room[0].body
    assert "could not be found" not in room[0].body

    own = await _spoken_for(
        conversation_type="dm", policy=_policy(persons='["ken"]'),
        documents=shared, document_ids=(str(doc), str(ghost)),
        fake_slots=fake_slots, fake_metrics=fake_metrics,
    )
    assert str(ghost) in own[0].body and "could not be found" in own[0].body


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


# ---------------------------------------------------------------------------
# 8765_44864: a finished job is delivered only while the grant still stands
# ---------------------------------------------------------------------------


def _policy(*, room: str = "conv-1", persons: str = '["person-1"]') -> Policy:
    """A real `Policy` from a real policy.toml. `is_allowed` is the grant under test,
    so a permissive stub would prove nothing."""
    p = Path(tempfile.mkdtemp()) / "policy.toml"
    p.write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        f'id = "{room}"\nlabel = "{room}"\npersons = {persons}\n'
    )
    return load_policy(p)


async def _finished_job_under(policy: Policy, fake_slots, fake_metrics):
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(
        job_id="job_1",
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="a haiku"),
        polls_before_done=0,
    )
    spoken: list = []

    async def speak(occ):
        spoken.append(occ)
        return "sent"

    poller = ComputeJobPoller(
        store=store, backends={"subagent": backend}, speak=speak, slots=fake_slots,
        metrics=fake_metrics, tick_seconds=1.0, clock=lambda: datetime.now(UTC),
        policy=policy,
    )
    await poller.tick()
    return spoken, await store.get(job.id)


async def test_a_finished_job_is_not_delivered_once_its_room_is_removed(fake_slots, fake_metrics):
    """Room removal between creation and completion: the room is no longer in
    policy.toml, so nothing is spoken and the row is closed as policy-failed."""
    spoken, fetched = await _finished_job_under(
        _policy(room="another-room"), fake_slots, fake_metrics,
    )
    assert spoken == []
    assert fetched.state is ComputeJobState.FAILED
    assert fetched.failure_reason == FAILED_POLICY


async def test_a_finished_job_is_not_delivered_once_its_requester_is_removed(
    fake_slots, fake_metrics,
):
    """Requester removal: the room still lists the conversation, but its explicit
    `persons` list no longer names the person who asked."""
    spoken, fetched = await _finished_job_under(
        _policy(persons='["someone-else"]'), fake_slots, fake_metrics,
    )
    assert spoken == []
    assert fetched.state is ComputeJobState.FAILED
    assert fetched.failure_reason == FAILED_POLICY


async def test_a_finished_job_is_still_delivered_while_the_grant_stands(fake_slots, fake_metrics):
    """The positive control: the same job under the same policy shape, still granted."""
    spoken, fetched = await _finished_job_under(_policy(), fake_slots, fake_metrics)
    assert len(spoken) == 1
    assert fetched.state is ComputeJobState.DONE


async def test_a_lost_job_is_not_spoken_after_restart_once_its_grant_is_gone():
    """Restart recovery: `recover` closes the orphaned row, and the boot-time telling
    must not speak into a room the requester no longer has access to."""
    from nora.app.composition import _tell_about_lost_jobs

    job = _pending_job()
    spoken: list = []

    async def speak(occ):
        spoken.append(occ)
        return "sent"

    await _tell_about_lost_jobs(
        jobs=[job], speak=speak, slots=ConversationSlots(maxlen=16, spawn=lambda c: None),
        metrics=Metrics(), policy=_policy(persons='["someone-else"]'),
    )
    assert spoken == []


async def test_a_lost_job_is_still_told_while_the_grant_stands():
    """The positive control for the restart test: the same call, still granted."""
    from nora.app.composition import _tell_about_lost_jobs

    job = _pending_job()
    spoken: list = []

    async def speak(occ):
        spoken.append(occ)
        return "sent"

    await _tell_about_lost_jobs(
        jobs=[job], speak=speak, slots=ConversationSlots(maxlen=16, spawn=lambda c: None),
        metrics=Metrics(), policy=_policy(),
    )
    assert len(spoken) == 1
