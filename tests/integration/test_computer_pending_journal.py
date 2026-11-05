"""Fault-injection proofs for the durable pending-result journal — through
the live `run_segment`/`delivered`/`_persist_pending` callers, never by
staging the journal by hand.

Boundary being pinned: the original terminal outcome is delivered to the
room/caller EXACTLY once. A live handoff requires a durable disposition
(journal `consumed` + SQL `delivered_at`); when either checkpoint fails the
caller is told `unavailable` WITH the task's identity — never the result
body — and the deferred path delivers the original outcome once instead.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nora.app.computer_queue import (
    ComputerAdmission,
    SegmentRegistry,
    _persist_pending,
)
from nora.domain.computer_tasks import ComputerTask
from nora.testing import FakeComputerTaskStore, FakeStreamRedis

NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
SCOPE = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def _task(task_id=None):
    return ComputerTask(
        id=task_id or uuid.uuid4(), scope=SCOPE, kind="native",
        status="queued", payload={}, origin={},
        created_at=NOW, updated_at=NOW)


class _FailingTasks(FakeComputerTaskStore):
    """Contract fake plus switchable finish/mark_delivered faults."""

    def __init__(self):
        super().__init__()
        self.fail_finish = False
        self.fail_delivered = False

    async def finish(self, task_id, *, status, result, now):
        if self.fail_finish:
            raise RuntimeError("injected SQL finish failure")
        return await super().finish(task_id, status=status, result=result, now=now)

    async def mark_delivered(self, task_id, *, now):
        if self.fail_delivered:
            raise RuntimeError("injected SQL mark_delivered failure")
        return await super().mark_delivered(task_id, now=now)


async def _work(result):
    return result


def _rig(pending_dir):
    executors = SegmentRegistry(pending_dir=pending_dir)
    tasks = _FailingTasks()
    admission = ComputerAdmission(tasks=tasks, redis=FakeStreamRedis(),
                                  clock=lambda: NOW, executors=executors)
    return executors, tasks, admission


def _seal(directory: Path):
    directory.chmod(0o500)


def _open(directory: Path):
    directory.chmod(0o700)


async def test_a_healthy_segment_returns_the_reply_and_stamps_delivered(tmp_path):
    """Positive control: journal + SQL healthy — live reply goes out once,
    `delivered_at` lands, journal cleared, nothing deferred-eligible."""
    executors, tasks, admission = _rig(tmp_path / "pending")
    task = _task()
    await tasks.enqueue(task)
    await tasks.try_claim_head(task.id, now=NOW)

    reply = await admission.run_segment(task, _work({"status": "ok", "v": 7}))
    assert reply["v"] == 7
    row = await tasks.get(task.id)
    assert row.status == "done" and row.delivered_at is not None
    assert executors.pending_result(task.id) is None


async def test_a_sealed_journal_with_healthy_sql_still_delivers(tmp_path):
    """Positive control, single-authority: the journal cannot take a
    checkpoint but SQL takes the terminal row and `delivered_at` — one
    authoritative durable disposition suffices, so the live reply goes out
    and nothing stays deferred-eligible."""
    directory = tmp_path / "pending"
    _executors, tasks, admission = _rig(directory)
    task = _task()
    await tasks.enqueue(task)
    await tasks.try_claim_head(task.id, now=NOW)
    _seal(directory)

    reply = await admission.run_segment(task, _work({"status": "ok", "v": 7}))
    assert reply["v"] == 7
    row = await tasks.get(task.id)
    assert row.status == "done" and row.delivered_at is not None
    assert await tasks.undelivered(limit=10) == []


async def test_failed_consumed_checkpoint_returns_identity_not_the_result(tmp_path):
    """The B2 closure case: BOTH stores refuse — journal sealed AND SQL
    `mark_delivered` down — so no durable disposition exists. The caller
    gets `unavailable` + task id, NOT the result body; the consumed marker
    is reverted so the original outcome stays deferred-eligible for exactly
    one deferred delivery."""
    directory = tmp_path / "pending"
    executors, tasks, admission = _rig(directory)
    task = _task()
    await tasks.enqueue(task)
    await tasks.try_claim_head(task.id, now=NOW)
    _seal(directory)
    tasks.fail_delivered = True

    reply = await admission.run_segment(task, _work({"status": "ok", "v": 7}))
    assert reply["status"] == "unavailable" and reply["task"] == str(task.id)
    assert "v" not in reply  # the result body did NOT go out
    row = await tasks.get(task.id)
    assert row.status == "done" and row.delivered_at is None
    assert row.result == {"status": "ok", "v": 7}
    pending = executors.pending_result(task.id)
    assert pending is None or not pending.consumed  # unmarked for deferred

    # Recovery: SQL alone takes the terminal result and the deferred stamp.
    tasks.fail_delivered = False
    _open(directory)
    os.close(executors._lock_fd)  # simulate process death
    second = SegmentRegistry(pending_dir=directory)
    assert [r.id for r in await tasks.undelivered(limit=10)] == [task.id]
    os.close(second._lock_fd)


async def test_sql_mark_delivered_failure_keeps_the_result_deliverable(tmp_path):
    """Journal lands `consumed`, SQL `mark_delivered` fails: caller told
    `unavailable`+task id, consumed marker is reverted so the deferred path
    can still deliver the original result once."""
    executors, tasks, admission = _rig(tmp_path / "pending")
    task = _task()
    await tasks.enqueue(task)
    await tasks.try_claim_head(task.id, now=NOW)
    tasks.fail_delivered = True

    reply = await admission.run_segment(task, _work({"status": "ok", "v": 7}))
    assert reply["status"] == "unavailable" and reply["task"] == str(task.id)
    pending = executors.pending_result(task.id)
    assert pending is None or not pending.consumed
    # Once SQL recovers the pending converges; the row stays
    # deferred-eligible (delivered_at never stamped) for ONE delivery.
    tasks.fail_delivered = False
    await _persist_pending(executors=executors, tasks=tasks,
                           clock=lambda: NOW, task_id=task.id)
    row = await tasks.get(task.id)
    assert row.status == "done" and row.delivered_at is None
    assert [r.id for r in await tasks.undelivered(limit=10)] == [task.id]


async def test_a_corrupt_journal_refuses_every_construction(tmp_path):
    """Not one-start refusal: the corrupt file stays in place and EVERY
    construction raises until an operator removes it; evidence is copied
    aside, never renamed away into a silent first-use."""
    directory = tmp_path / "pending"
    reg = SegmentRegistry(pending_dir=directory)
    assert reg.retain(uuid.uuid4(), "done", {"v": 1})
    os.close(reg._lock_fd)
    active = directory / "pending-results.json"
    active.write_text("{not json")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            SegmentRegistry(pending_dir=directory)
        assert active.exists()  # still refusing, still in place
    assert list(directory.glob("pending-results.json.corrupt-*"))


async def test_a_second_owner_is_refused_for_the_store_lifetime(tmp_path):
    first = SegmentRegistry(pending_dir=tmp_path / "pending")
    with pytest.raises(RuntimeError, match="second owner"):
        SegmentRegistry(pending_dir=tmp_path / "pending")
    assert first.retain(uuid.uuid4(), "done", {"v": 1})


async def test_the_result_is_journaled_before_the_sql_finish_wait(tmp_path):
    """Window proof: kill the SQL finish mid-flight — the ORIGINAL typed
    result is already in the journal, so a reconstructed owner persists it
    instead of losing it to an orphaned `running` row."""
    executors, tasks, admission = _rig(tmp_path / "pending")
    task = _task()
    await tasks.enqueue(task)
    await tasks.try_claim_head(task.id, now=NOW)

    tasks.fail_finish = True
    reply = await admission.run_segment(task, _work({"status": "ok", "v": 9}))
    assert reply["v"] == 9
    assert reply["v"] == 9
    assert (await tasks.get(task.id)).status == "running"  # SQL never took it
    os.close(executors._lock_fd)

    second = SegmentRegistry(pending_dir=tmp_path / "pending")
    pending = second.pending_result(task.id)
    assert pending is not None and pending.result["v"] == 9
    tasks.fail_finish = False
    assert await _persist_pending(executors=second, tasks=tasks,
                                  clock=lambda: NOW, task_id=task.id)
    row = await tasks.get(task.id)
    assert row.status == "done" and row.result == {"status": "ok", "v": 9}
