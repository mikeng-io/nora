"""The per-scope computer FIFO: durable admission, one segment at a time.

`ComputerAdmission.submit` is the only way work reaches a scope's VM. It
writes the `computer_tasks` row FIRST — the durable intent — then:

- **Fast path.** The row is claimed head-of-queue in the same call
  (`try_claim_head`: still `queued`, no `running` row and no earlier
  `queued` row of the scope). The CALLER executes its segment inline and
  finishes the row itself. No stream entry is needed at all.
- **Queued.** The row is not head: a stream pointer goes on
  `nq:computer:<scope>` (a dedicated computer stream, never a bridge lane)
  and the caller may wait a bounded window for the grant, or hand its
  continuation to a background task — the conversation slot is never held
  while queued, and completion reaches the origin room through the existing
  deferred delivery (`compute_jobs`, `ComputeJobPoller`).

`ComputerQueueRunner` is the GRANTER and the WATCHDOG, not the executor:
segments always run in their submitter's context, so a segment's effect can
never be double-run by the queue. Its tick grants the head row of each
scope's stream (`try_claim_head` — the one atomic rule, shared with the
submitter's fast path), then a per-row watcher waits for the finisher:
deadline passed while queued means the row never starts; past it while
`running`, or an owner that died without finishing, closes the row
`interrupted` — uncertain effects are NEVER replayed. The terminal row is
written before the stream ack, so a lost ack replays a pointer to a closed
row only.

`current_segment` marks code running inside a granted slot: a nested call
made under it (a worker's own computer use) inherits the parent's admission
and runs inline — it never enqueues behind its own parent, the deadlock the
design rules out.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from nora.domain.computer_tasks import (
    QUEUED,
    RUNNING,
    TERMINAL,
    ComputerTask,
    ComputerTaskStore,
    current_segment,
)

log = logging.getLogger("nora.computer.queue")

STREAM_PREFIX = "nq:computer:"
GROUP = "nora-computer"
CONSUMER = "nora"
#: A `running` row with no deadline that stays unfinished this long is
#: orphaned: its owner died mid-segment. Never re-run — close `interrupted`.
ORPHAN_AFTER = timedelta(minutes=45)

@dataclass(frozen=True, slots=True)
class SubmittedTask:
    """What `submit` answers.

    `granted`: the row is `running` FOR THIS CALLER — execute the segment
    now and `finish` it. `inherited`: the caller is inside a parent's slot —
    run inline, no row was made. `result`: the row reached a terminal state
    while the caller waited (only a cancel can land before execution — the
    caller is the only executor). Otherwise the task is queued and its
    completion is delivered, not answered now."""

    task: ComputerTask
    granted: bool = False
    inherited: bool = False
    result: ComputerTask | None = None


def _stream(scope: str) -> str:
    return f"{STREAM_PREFIX}{scope}"


class ComputerAdmission:
    def __init__(
        self, *, tasks: ComputerTaskStore, redis: Any,
        clock: Callable[[], datetime], poll_seconds: float = 0.25,
    ) -> None:
        self._tasks = tasks
        self._redis = redis
        self._clock = clock
        self._poll = poll_seconds

    async def submit(
        self, *, scope: str, kind: str, payload: Mapping[str, Any],
        origin: Mapping[str, Any], wait_seconds: float = 0.0,
        deadline_at: datetime | None = None,
    ) -> SubmittedTask:
        """Admit one segment, FIFO within the scope."""
        if current_segment.get() is not None:
            return SubmittedTask(task=_ephemeral(scope, kind, self._clock()),
                                 inherited=True)
        now = self._clock()
        task = ComputerTask(
            id=uuid.uuid4(), scope=scope, kind=kind, status=QUEUED,
            payload=dict(payload), origin=dict(origin),
            created_at=now, updated_at=now, deadline_at=deadline_at,
        )
        task = await self._tasks.enqueue(task)
        claimed = await self._tasks.try_claim_head(task.id, now=self._clock())
        if claimed is not None:
            return SubmittedTask(task=claimed, granted=True)
        try:
            await self._redis.xadd(_stream(scope), {"task": str(task.id)})
        except Exception:  # noqa: BLE001 - a publish gap reconciles, never loses the row
            log.warning("computer task publish failed; the reconcile pass will")
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            row = await self._tasks.get(task.id)
            if row is None or row.status in TERMINAL:
                return SubmittedTask(task=task, result=row)
            if row.status == RUNNING:
                return SubmittedTask(task=row, granted=True)
            await asyncio.sleep(self._poll)
        row = await self._tasks.get(task.id)
        if row is not None and row.status == RUNNING:
            return SubmittedTask(task=row, granted=True)
        return SubmittedTask(
            task=task, result=row if row is not None and row.status in TERMINAL else None)

    async def finish(self, task_id: UUID, *, status: str,
                     result: Mapping[str, Any]) -> None:
        """The executing caller's close: terminal row persisted."""
        await self._tasks.finish(task_id, status=status,
                                 result=result, now=self._clock())

    async def cancel(self, task_id: UUID) -> ComputerTask | None:
        return await self._tasks.request_cancel(task_id, now=self._clock())

    async def delivered(self, task_id: UUID) -> None:
        """The terminal result was consumed live: `delivered_at`, so
        `on_terminal` does not hand it to deferred delivery."""
        await self._tasks.mark_delivered(task_id, now=self._clock())

    async def get(self, task_id: UUID) -> ComputerTask | None:
        return await self._tasks.get(task_id)

    async def open_for_scope(self, scope: str, *, limit: int = 50) -> list[ComputerTask]:
        return await self._tasks.open_for_scope(scope, limit=limit)


def _ephemeral(scope: str, kind: str, now: datetime) -> ComputerTask:
    return ComputerTask(
        id=uuid.uuid4(), scope=scope, kind=kind, status=RUNNING,
        payload={}, origin={}, created_at=now, updated_at=now, started_at=now)


class ComputerQueueRunner:
    """The granter/watchdog loop. `run_forever` is composition's task;
    `tick` is one reconcile+grant pass (the testable half)."""

    def __init__(
        self, *, tasks: ComputerTaskStore, redis: Any,
        clock: Callable[[], datetime], metrics: Any = None,
        batch: int = 20, watch_seconds: float = 1.0,
        on_terminal: Callable[[ComputerTask], Awaitable[None]] | None = None,
        reconcile: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._tasks = tasks
        self._redis = redis
        self._clock = clock
        self._metrics = metrics
        self._batch = batch
        self._on_terminal = on_terminal
        self._reconcile_scope = reconcile
        self._watch_seconds = watch_seconds
        self._group_ready: set[str] = set()
        self._watchers: set[asyncio.Task[Any]] = set()

    async def _group(self, stream: str) -> None:
        if stream in self._group_ready:
            return
        with contextlib.suppress(Exception):
            await self._redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
        self._group_ready.add(stream)

    async def _scopes(self) -> list[str]:
        keys = []
        async for key in self._redis.scan_iter(match=f"{STREAM_PREFIX}*", count=100):
            keys.append(key.decode() if isinstance(key, bytes) else key)
        return [k[len(STREAM_PREFIX):] for k in keys]

    async def reconcile(self) -> None:
        """Publish gaps and dead grants: an unannounced `queued` row gets its
        stream entry; a stale `running` row is closed `interrupted` — its
        effects are uncertain and never re-run."""
        try:
            # Only rows old enough that a normal publish would already have
            # landed: an every-tick re-xadd would duplicate pointers, not
            # close gaps (a duplicate is harmless — a pointer is never the
            # record — but it grows the pending list for nothing).
            grace = self._clock() - timedelta(seconds=10)
            for task in await self._tasks.unannounced(limit=self._batch):
                if task.created_at < grace:
                    await self._redis.xadd(_stream(task.scope), {"task": str(task.id)})
            for task in await self._tasks.interrupted(
                    older_than=self._clock() - ORPHAN_AFTER, limit=self._batch):
                await self._tasks.mark_interrupted(
                    task.id, now=self._clock(), reason="the owner was lost")
        except Exception:
            log.exception("computer queue reconcile failed")

    async def tick(self) -> int:
        """One pass over every scope's stream; how many segments were granted."""
        await self.reconcile()
        granted = 0
        for scope in await self._scopes():
            granted += await self._dispatch(scope)
        return granted

    async def _dispatch(self, scope: str) -> int:
        stream = _stream(scope)
        await self._group(stream)
        if self._reconcile_scope is not None:
            with contextlib.suppress(Exception):
                await self._reconcile_scope(scope)
        try:
            auto = await self._redis.xautoclaim(
                stream, GROUP, CONSUMER, min_idle_time=60_000, start_id="0-0",
                count=self._batch)
            pending = list(auto[1] if len(auto) > 1 else [])
            fresh = await self._redis.xreadgroup(
                GROUP, CONSUMER, {stream: ">"}, count=self._batch)
            # Pointers this consumer already holds: "0" re-reads the pending
            # list NOW, not after xautoclaim's idle bound — the grant after
            # a finish cannot stall a minute on an entry it already had.
            held = await self._redis.xreadgroup(
                GROUP, CONSUMER, {stream: "0"}, count=self._batch)
            for _, items in held or []:
                have = {mid for mid, _ in pending}
                pending.extend((mid, fields) for mid, fields in items
                               if mid not in have)
        except Exception:
            log.exception("computer queue read failed")
            return 0
        seen: list[tuple[Any, Mapping[str, Any]]] = []
        for _, items in fresh or []:
            seen.extend((mid, fields) for mid, fields in items)
        seen.extend(pending)
        # A scope with a running row cannot grant anything this pass.
        try:
            open_rows = await self._tasks.open_for_scope(scope, limit=self._batch)
        except Exception:
            log.exception("computer queue scope read failed")
            return 0
        if any(r.status == RUNNING for r in open_rows):
            return 0
        granted = 0
        for mid, fields in seen:
            task_id = _task_id(fields)
            if task_id is None:
                await self._ack(stream, mid)
                continue
            row = await self._tasks.get(task_id)
            if row is None or row.status != QUEUED:
                await self._ack(stream, mid)
                continue
            # Re-check at the queue head: cancelled or past-deadline never
            # starts.
            if (row.deadline_at is not None and row.deadline_at <= self._clock()
                    ) or row.cancel_requested_at is not None:
                await self._tasks.request_cancel(row.id, now=self._clock())
                await self._ack(stream, mid)
                continue
            claimed = await self._tasks.try_claim_head(row.id, now=self._clock())
            if claimed is None:
                continue  # not head: FIFO, never skip ahead
            granted += 1
            watcher = asyncio.ensure_future(self._watch(stream, mid, claimed))
            self._watchers.add(watcher)
            watcher.add_done_callback(self._watch_done)
            break
        return granted

    def _watch_done(self, watcher: asyncio.Task[Any]) -> None:
        self._watchers.discard(watcher)
        if watcher.cancelled():
            return
        exc = watcher.exception()
        if exc is not None:
            log.error("computer queue watcher died", exc_info=exc)

    async def _ack(self, stream: str, mid: Any) -> None:
        with contextlib.suppress(Exception):
            await self._redis.xack(stream, GROUP, mid)

    async def _watch(self, stream: str, mid: Any, task: ComputerTask) -> None:
        """Wait out the granted segment: its caller finishes the row, or the
        deadline/orphan bound closes it `interrupted`. The row is terminal
        before the ack."""
        try:
            while True:
                row = await self._tasks.get(task.id)
                if row is None or row.status in TERMINAL:
                    break
                now = self._clock()
                if (row.deadline_at is not None and row.deadline_at <= now) or (
                        row.started_at is not None
                        and row.started_at < now - ORPHAN_AFTER):
                    await self._tasks.mark_interrupted(
                        task.id, now=now, reason="the grant ran out")
                    row = await self._tasks.get(task.id)
                    break
                await asyncio.sleep(self._watch_seconds)
            if row is not None and self._on_terminal is not None:
                with contextlib.suppress(Exception):
                    await self._on_terminal(row)
        finally:
            await self._ack(stream, mid)

    async def run_forever(self, *, tick_seconds: float = 1.0) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("computer queue tick failed")
            await asyncio.sleep(tick_seconds)


def _task_id(fields: Mapping[Any, Any]) -> UUID | None:
    raw = fields.get(b"task") or fields.get("task")
    try:
        return UUID(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


class ComputerTaskBackend:
    """`ComputeBackend` over a `computer_tasks` row: the deferred-delivery
    half of the queue. `launch` is never called — a computer task is
    submitted through `ComputerAdmission`, and the `compute_jobs` row is
    written only when its terminal state needs delivering. `poll` reads the
    row back: still open is `RUNNING`, `done` is `SUCCEEDED`, anything else
    is `FAILED` with the row's reason — the same three states the generic
    poller already renders."""

    def __init__(self, *, tasks: ComputerTaskStore) -> None:
        self._tasks = tasks

    async def launch(self, *, request: Any) -> str:
        raise NotImplementedError(
            "computer tasks are submitted through ComputerAdmission, not launched")

    async def poll(self, *, job_id: str) -> Any:
        from nora.domain.ports import JobResult, JobStatus

        try:
            task_id = UUID(str(job_id))
        except (ValueError, TypeError, AttributeError):
            return JobResult(status=JobStatus.FAILED,
                             error="the computer task id is unreadable",
                             failure_reason="lost")
        row = await self._tasks.get(task_id)
        if row is None:
            return JobResult(status=JobStatus.FAILED,
                             error="the computer task is gone",
                             failure_reason="lost")
        if row.status in (QUEUED, RUNNING):
            return JobResult(status=JobStatus.RUNNING)
        note = str(row.result.get("note") or row.result.get("reply_status")
                   or row.status)
        if row.status == "done":
            return JobResult(status=JobStatus.SUCCEEDED, report=note,
                             summary=note)
        return JobResult(status=JobStatus.FAILED, error=note,
                         failure_reason=row.status)

    async def cancel(self, *, job_id: str) -> None:
        try:
            task_id = UUID(str(job_id))
        except (ValueError, TypeError, AttributeError):
            return
        await self._tasks.request_cancel(task_id, now=datetime.now(UTC))


async def deliver_computer_task(
    row: ComputerTask, *, jobs: Any, tasks: ComputerTaskStore,
    clock: Callable[[], datetime],
) -> None:
    """Hand a terminal queued task's result to the deferred-delivery outbox.

    Called by the runner's `on_terminal` for every row that closes. A row
    its caller already consumed (`delivered_at` set by the live/granted
    path) or that names no room is skipped; otherwise one `compute_jobs`
    row of kind `computer` is written and the task is marked delivered, so
    the result reaches the origin room through `ComputeJobPoller` — the
    same path every other deferred job takes — without holding a
    conversation slot while it was queued."""
    import hashlib
    import json as _json
    from uuid import uuid4

    from nora.domain.compute import ComputeJob, ComputeJobState, normalise_compute_job

    if row.delivered_at is not None:
        return
    origin = dict(row.origin or {})
    platform = str(origin.get("platform") or "")
    conversation_id = str(origin.get("conversation_id") or "")
    if platform and conversation_id:
        tool = str(row.payload.get("tool") or row.kind)
        task_text = _json.dumps({"kind": row.kind, "payload": row.payload},
                                sort_keys=True, default=str)
        await jobs.create(normalise_compute_job(ComputeJob(
            id=uuid4(), backend_job_id=str(row.id),
            task=f"computer {row.kind}: {tool}",
            artifact_id=None, platform=platform,
            conversation_id=conversation_id,
            conversation_type=str(origin.get("conversation_type") or "group"),
            requester_person_id=str(origin.get("requester_person_id") or ""),
            created_at=clock(), state=ComputeJobState.PENDING,
            completed_at=None, result_url=None, result_summary=None,
            error=None,
            task_sha256=hashlib.sha256(task_text.encode()).hexdigest(),
            context_sha256=hashlib.sha256(b"{}").hexdigest(),
            kind="computer",
            tag_id=str(origin.get("tag_id") or "") or None,
            payload={"task": str(row.id), "tool": tool, "kind": row.kind},
        )))
    # `delivered_at`, whether or not a room was named: the row's terminal
    # state was dispositioned, so a second `on_terminal` call cannot file a
    # duplicate delivery.
    await tasks.mark_delivered(row.id, now=clock())

