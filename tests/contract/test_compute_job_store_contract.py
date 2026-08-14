"""One suite, both implementations of `ComputeJobStore` (Nora Compute spec
§4). Mirrors `tests/contract/test_reminder_store_contract.py`'s own shape —
the same real-Postgres `pg_url` fixture, migrated up before the SQL store is
constructed, parametrized alongside the in-memory fake.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
        "task_sha256": "hash-task", "context_sha256": "hash-context",
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
    """`SqlComputeJobStore.pending` passes `limit` straight into `.limit(limit)`
    with no `check_limit`-style floor, unlike `ReminderStore.due()` — flagged
    by Task 2's own review rather than the brief. `limit=0` is the one input
    the two implementations could plausibly disagree on without either raising:
    Postgres accepts `LIMIT 0` and returns zero rows, and Python's `rows[:0]`
    also returns `[]` (unlike a NEGATIVE limit, where `rows[:-1]` silently
    returns everything but the last row instead of erroring — the divergence
    `ReminderStore.due()`'s own `check_limit` guards against). Nothing in this
    plan has `ComputeJobPoller` ever call `pending` with a negative limit, so
    that case is not exercised here — only the real boundary input both
    implementations must agree on.
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
        result_url="https://nora.example.net/drop/x", result_summary="a haiku",
    )
    assert changed is True
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.DONE
    assert fetched.result_url == "https://nora.example.net/drop/x"


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
