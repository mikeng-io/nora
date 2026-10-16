"""One suite, both implementations of `ComputeJobStore` (Nora Compute spec
§4). Mirrors `tests/contract/test_reminder_store_contract.py`'s own shape —
the same real-Postgres `pg_url` fixture, migrated up before the SQL store is
constructed, parametrized alongside the in-memory fake.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nora.domain.compute import ComputeJob, ComputeJobState, NaiveComputeJobDatetimeError
from nora.infra.sql.compute_jobs import SqlComputeJobStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeComputeJobStore


def _job(**overrides):
    defaults = {
        "id": uuid4(), "backend_job_id": "job_x", "task": "write a haiku",
        "artifact_id": None, "platform": "signal", "conversation_id": "conv-1",
        "conversation_type": "group", "requester_person_id": "person-1",
        "created_at": datetime.now(UTC), "state": ComputeJobState.PENDING,
        "completed_at": None, "result_url": None, "result_summary": None, "error": None,
        "task_sha256": "hash-task", "context_sha256": "hash-context", "kind": "subagent",
    }
    defaults.update(overrides)
    return ComputeJob(**defaults)


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        s = FakeComputeJobStore()
    else:
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s = SqlComputeJobStore(create_sql_engine(pg_url))
    yield s
    await s.aclose()


async def test_create_then_get_round_trips(store):
    job = _job()
    await store.create(job)
    fetched = await store.get(job.id)
    assert fetched is not None
    assert fetched.backend_job_id == "job_x"


async def test_the_provenance_hashes_round_trip(store):
    job = _job(task_sha256="a" * 64, context_sha256="b" * 64)
    await store.create(job)
    fetched = await store.get(job.id)
    assert fetched is not None
    assert fetched.task_sha256 == "a" * 64
    assert fetched.context_sha256 == "b" * 64


async def test_pending_returns_only_pending_rows(store):
    pending_job = _job()
    await store.create(pending_job)
    done_job = _job(id=uuid4(), state=ComputeJobState.DONE)
    await store.create(done_job)
    result = await store.pending(limit=10)
    ids = {j.id for j in result}
    assert pending_job.id in ids
    assert done_job.id not in ids


async def test_pending_with_zero_limit_returns_nothing_on_both_stores(store):
    """`SqlComputeJobStore.pending` passes `limit` straight into `.limit(limit)` with no
    `check_limit`-style floor, unlike `ReminderStore.due()`. `limit=0` is the one input
    the two implementations could plausibly disagree on without either raising: Postgres
    accepts `LIMIT 0` and returns zero rows, and Python's `rows[:0]` also returns `[]`
    (unlike a NEGATIVE limit, where `rows[:-1]` silently returns everything but the last
    row instead of erroring — the divergence `ReminderStore.due()`'s own `check_limit`
    guards against). `ComputeJobPoller` never calls `pending` with a negative limit, so
    that case is not exercised here — only the real boundary input both implementations
    must agree on.
    """
    await store.create(_job())
    assert await store.pending(limit=0) == []


async def test_claim_is_a_compare_and_set_from_pending(store):
    job = _job()
    await store.create(job)
    assert await store.claim(job.id) is True
    assert await store.claim(job.id) is False  # already FIRING, second claim loses


async def test_mark_writes_the_terminal_result(store):
    job = _job()
    await store.create(job)
    await store.claim(job.id)
    changed = await store.mark(
        job.id, ComputeJobState.DONE, completed_at=datetime.now(UTC),
        result_url="https://portal.example.net/artifacts/x", result_summary="a haiku",
    )
    assert changed is True
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.DONE
    assert fetched.result_url == "https://portal.example.net/artifacts/x"


async def test_sweep_firing_resolves_to_failed(store):
    job = _job()
    await store.create(job)
    await store.claim(job.id)
    swept = await store.sweep_firing(now=datetime.now(UTC))
    # The ROWS, not a count: they are terminal the instant this returns, so
    # this is the only moment anything can still learn who was waiting on them
    # and close the promise they left open.
    assert [j.id for j in swept] == [job.id]
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.FAILED


async def test_sweep_pending_resolves_to_failed_and_says_why(store):
    """The restart half of boot recovery. `PENDING` here does NOT mean "not
    started yet" the way a pending reminder does — it means a container this
    process launched and can no longer identify, because `backend_job_id` is a
    handle into `ComputeBackend`'s in-memory registry. The recorded `error` is
    part of the contract, not decoration: it is what Nora would relay, so it
    must say the delivery was lost and never that the job itself failed."""
    job = _job()
    await store.create(job)
    swept = await store.sweep_pending(now=datetime.now(UTC))
    assert [j.id for j in swept] == [job.id]
    assert swept[0].conversation_id and swept[0].requester_person_id, (
        "the swept row must carry WHERE and WHO — telling them is the point"
    )
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.FAILED
    assert fetched.completed_at is not None
    assert "restart" in fetched.error


async def test_each_sweep_leaves_the_other_state_alone(store):
    """Neither sweep may generalise into "resolve everything unfinished". They
    run one after the other at boot and each records a DIFFERENT reason, so a
    sweep that also took the other's rows would stamp the wrong explanation on
    them — and, run in the other order, would make the second sweep's count
    zero and its log line a lie."""
    firing = _job()
    await store.create(firing)
    await store.claim(firing.id)
    pending = _job(id=uuid4())
    await store.create(pending)

    assert len(await store.sweep_firing(now=datetime.now(UTC))) == 1
    assert (await store.get(pending.id)).state is ComputeJobState.PENDING
    assert len(await store.sweep_pending(now=datetime.now(UTC))) == 1
    assert (await store.get(firing.id)).error == (
        "swept at boot: process crashed mid-delivery"
    )


async def test_naive_datetimes_are_refused_rather_than_guessed(store):
    """A naive datetime has no answer, so neither store may invent one.

    asyncpg would read it in the CLIENT HOST's zone (a deployment fact, not a
    domain one) and the fake would store it as-is and then raise `TypeError`
    from comparisons later. Both are guesses dressed as answers;
    `NaiveComputeJobDatetimeError` is the contract instead, on every datetime
    that crosses the port.
    """
    naive = datetime(2026, 8, 12, 9, 0)
    assert naive.tzinfo is None

    with pytest.raises(NaiveComputeJobDatetimeError):
        await store.create(_job(created_at=naive))

    ok = _job()
    await store.create(ok)
    await store.claim(ok.id)
    with pytest.raises(NaiveComputeJobDatetimeError):
        await store.mark(ok.id, ComputeJobState.DONE, completed_at=naive)

    with pytest.raises(NaiveComputeJobDatetimeError):
        await store.sweep_firing(now=naive)

    with pytest.raises(NaiveComputeJobDatetimeError):
        await store.sweep_pending(now=naive)


async def test_kind_tag_id_and_failure_reason_round_trip(store):
    job = _job(kind="video", tag_id="tag-7")
    await store.create(job)
    await store.claim(job.id)
    await store.mark(
        job.id, ComputeJobState.FAILED, completed_at=datetime.now(UTC),
        error="ran out of turns", failure_reason="limit_turns",
    )
    fetched = await store.get(job.id)
    assert (fetched.kind, fetched.tag_id) == ("video", "tag-7")
    assert (fetched.state, fetched.failure_reason) == (ComputeJobState.FAILED, "limit_turns")


async def test_a_new_row_has_no_tag_or_failure_reason_unless_given(store):
    job = _job()
    await store.create(job)
    fetched = await store.get(job.id)
    assert (fetched.tag_id, fetched.failure_reason) == (None, None)


async def test_a_cancelled_row_is_stored_and_is_never_pending(store):
    job = _job()
    await store.create(job)
    await store.claim(job.id)
    await store.mark(job.id, ComputeJobState.CANCELLED, completed_at=datetime.now(UTC))
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.CANCELLED
    assert job.id not in {j.id for j in await store.pending(limit=10)}


async def test_boot_sweeps_say_lost_as_the_failure_reason(store):
    firing, pending = _job(), _job(id=uuid4(), backend_job_id="job_y")
    await store.create(firing)
    await store.claim(firing.id)
    await store.create(pending)
    swept = [*await store.sweep_firing(now=datetime.now(UTC)),
             *await store.sweep_pending(now=datetime.now(UTC))]
    assert {j.id for j in swept} == {firing.id, pending.id}
    assert {j.failure_reason for j in swept} == {"lost"}


async def test_payload_result_and_trace_round_trip(store):
    payload = {"objective": "sort the sheets", "inputs": ["a.zip"], "n": 3}
    job = _job(payload=payload)
    await store.create(job)
    assert (await store.get(job.id)).payload == payload
    assert (await store.get(job.id)).result_document_ids == ()
    await store.claim(job.id)
    await store.mark(
        job.id, ComputeJobState.DONE, completed_at=datetime.now(UTC),
        result_report="3 sheets made", result_document_ids=("d1", "d2"),
    )
    fetched = await store.get(job.id)
    assert fetched.payload == payload
    assert fetched.result_report == "3 sheets made"
    assert fetched.result_document_ids == ("d1", "d2")
    assert fetched.trace_id is None


async def test_a_trace_id_given_at_create_round_trips(store):
    job = _job(trace_id="trace-abc")
    await store.create(job)
    assert (await store.get(job.id)).trace_id == "trace-abc"


async def test_mark_writes_the_workers_trace_id(store):
    job = _job()
    await store.create(job)
    await store.claim(job.id)
    await store.mark(job.id, ComputeJobState.DONE, trace_id="abc123")
    assert (await store.get(job.id)).trace_id == "abc123"


async def test_room_jobs_returns_only_that_rooms_rows_of_that_kind_newest_first(store):
    t0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    older = _job(created_at=t0)
    newer = _job(id=uuid4(), created_at=t0.replace(minute=30), state=ComputeJobState.DONE)
    other_room = _job(id=uuid4(), conversation_id="conv-2")
    other_platform = _job(id=uuid4(), platform="discord")
    other_kind = _job(id=uuid4(), kind="video")
    for j in (older, newer, other_room, other_platform, other_kind):
        await store.create(j)
    rows = await store.room_jobs(
        platform="signal", conversation_id="conv-1", kind="subagent", limit=10,
    )
    assert [j.id for j in rows] == [newer.id, older.id]
    capped = await store.room_jobs(
        platform="signal", conversation_id="conv-1", kind="subagent", limit=1,
    )
    assert [j.id for j in capped] == [newer.id]


# -- the sub-agent's checkpoint ---------------------------------------


async def test_a_new_row_has_no_checkpoint(store):
    job = _job()
    await store.create(job)
    fetched = await store.get(job.id)
    assert (fetched.checkpoint, fetched.checkpoint_at) == (None, None)


async def test_set_checkpoint_writes_a_pending_row_and_replaces_the_whole_value(store):
    job = _job(backend_job_id="bj-1")
    await store.create(job)
    first = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    second = datetime(2026, 10, 5, 12, 5, tzinfo=UTC)
    assert await store.set_checkpoint(
        "bj-1", {"done": ["a"], "doing": ["b"], "next": [], "decisions": ["x"],
                 "kept_files": ["out.txt"]}, first,
    )
    assert await store.set_checkpoint(
        "bj-1", {"done": ["a", "b"], "doing": [], "next": ["c"], "decisions": [],
                 "kept_files": []}, second,
    )
    fetched = await store.get(job.id)
    assert fetched.checkpoint == {
        "done": ["a", "b"], "doing": [], "next": ["c"], "decisions": [], "kept_files": [],
    }
    assert fetched.checkpoint_at == second


async def test_set_checkpoint_finds_no_row_for_an_unknown_or_claimed_job(store):
    job = _job(backend_job_id="bj-2")
    await store.create(job)
    at = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    assert not await store.set_checkpoint("nope", {"done": []}, at)
    assert await store.claim(job.id)
    assert not await store.set_checkpoint("bj-2", {"done": ["late"]}, at)
    assert (await store.get(job.id)).checkpoint is None


async def test_a_checkpoint_given_at_create_round_trips_and_naive_times_are_refused(store):
    at = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    job = _job(checkpoint={"done": ["a"], "kept_files": []}, checkpoint_at=at)
    await store.create(job)
    fetched = await store.get(job.id)
    assert fetched.checkpoint == {"done": ["a"], "kept_files": []}
    assert fetched.checkpoint_at == at
    other = _job(backend_job_id="bj-3")
    await store.create(other)
    with pytest.raises(NaiveComputeJobDatetimeError):
        await store.set_checkpoint("bj-3", {"done": []}, datetime(2026, 10, 5, 12, 0))


async def test_mark_writes_the_final_checkpoint_after_the_row_is_no_longer_pending(store):
    job = _job(backend_job_id="bj-9")
    await store.create(job)
    at = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    await store.claim(job.id)
    await store.mark(
        job.id, ComputeJobState.DONE, completed_at=at,
        checkpoint={"done": ["a"], "kept_files": []}, checkpoint_at=at,
    )
    fetched = await store.get(job.id)
    assert fetched.checkpoint == {"done": ["a"], "kept_files": []}
    assert fetched.checkpoint_at == at
    # a mark without one leaves what is there
    await store.mark(job.id, ComputeJobState.DONE, error="x")
    assert (await store.get(job.id)).checkpoint == {"done": ["a"], "kept_files": []}


async def test_sweep_pending_leaves_a_kept_kind_pending(store):
    """A kind whose handle survives a restart (a video job's is the provider's task id) is
    left pending by boot recovery; every other kind is swept as before."""
    video = _job(kind="video", backend_job_id="T1@1")
    worker = _job(id=uuid4(), kind="subagent")
    await store.create(video)
    await store.create(worker)
    swept = await store.sweep_pending(now=datetime.now(UTC), keep_kinds=frozenset({"video"}))
    assert [j.id for j in swept] == [worker.id]
    assert (await store.get(video.id)).state is ComputeJobState.PENDING


async def test_kind_since_reads_one_kind_from_a_time_any_room_any_state(store):
    """`video_create`'s daily cap reads this, so the count survives a restart."""
    now = datetime.now(UTC)
    old = _job(kind="video", created_at=now - timedelta(days=2))
    today = _job(id=uuid4(), kind="video", conversation_id="conv-2",
                 state=ComputeJobState.FAILED)
    other = _job(id=uuid4(), kind="subagent")
    for job in (old, today, other):
        await store.create(job)
    rows = await store.kind_since(kind="video", since=now - timedelta(hours=1))
    assert [j.id for j in rows] == [today.id]
