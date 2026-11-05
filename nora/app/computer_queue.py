"""The per-scope computer FIFO: durable admission, one segment at a time.

`ComputerAdmission.submit` is the only way work reaches a scope's VM. It
writes the `computer_tasks` row FIRST — the durable intent, carrying the
typed request (`payload.tool` + `payload.args`) and the delivery provenance
(`origin`) — then:

- **Fast path.** The row is claimed head-of-queue in the same call
  (`try_claim_head`: still `queued`, the scope's oldest `queued` row, and NO
  `running` row). The CALLER executes its segment through `run_segment` — a
  child task the registry can cancel — and the terminal row is marked
  `delivered` when the reply reached the caller.
- **Queued.** The row is not head: a stream pointer goes on
  `nq:computer:<scope>` and `ComputerQueueRunner` claims and EXECUTES it,
  rebuilding the call from the durable payload through `dispatch` under
  `current_segment` — so a restart loses nothing the row held, and the
  rebuild re-runs the tool's own admission path (fresh policy) rather than
  replaying a captured closure. Completion reaches the origin room through
  the existing deferred delivery (`compute_jobs`, `ComputeJobPoller`),
  retried until `delivered_at` lands.
- **Worker.** A sub-agent's job is a `kind="worker"` segment its own
  backend submits and waits on (`wait_for_grant`): the runner never
  executes it (no dispatcher for the kind), the job task executes under
  `current_segment`, and its nested computer calls inherit the parent's
  slot instead of enqueueing behind it.

`ComputerQueueRunner` is granter, dispatcher and watchdog. A `running` row
whose executor is not in the registry is owner-lost: before any successor
is granted, `reconcile` reads the VM's actual process state — quiet closes
the row `interrupted` (uncertain effects are NEVER replayed); busy or
unknown keeps the scope blocked (an idle timeout is not proof the old
command stopped), bounded by `ORPHAN_AFTER`. The terminal row is written
before the stream ack; a delivery failure leaves the pointer pending, so
delivery is retried, never dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from nora.domain.computer_tasks import (
    QUEUED,
    RUNNING,
    TASK_KINDS,
    TERMINAL,
    UNAVAILABLE,
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
#: How long a cancellation waits for the executor to stop on its own before
#: the row is closed `interrupted` — its effects could not be confirmed.
DRAIN_SECONDS = 10.0
#: The most of a tool reply persisted in `result`: the typed payload the
#: delivery path and `poll` read back.
RESULT_CHARS = 20000


@dataclass(frozen=True, slots=True)
class SubmittedTask:
    """What `submit` answers.

    `granted`: the row is `running` FOR THIS CALLER — execute the segment
    through `run_segment`. `inherited`: the caller is inside a parent's
    slot — run inline, no row was made. `result`: the row reached a
    terminal state while the caller waited. Otherwise the task is queued
    and its completion is delivered, not answered now. `granted` is never
    set for a row the RUNNER claimed — a claim grants execution to exactly
    the claimer."""

    task: ComputerTask
    granted: bool = False
    inherited: bool = False
    result: ComputerTask | None = None


def _stream(scope: str) -> str:
    return f"{STREAM_PREFIX}{scope}"


def row_status(reply: Mapping[str, Any]) -> str:
    """The terminal status a tool reply maps to: `unavailable` and
    `cancelled` pass through, an answered refusal is `failed` (the segment
    ran; the work did not land), every other answer is `done`."""
    status = str(reply.get("status") or "unavailable")
    if status in ("unavailable", "cancelled"):
        return status
    if status in ("ok", "nothing_found", "ambiguous"):
        return "done"
    return "failed"


def cut_result(reply: Mapping[str, Any]) -> dict[str, Any]:
    """The reply dict persisted as the row's `result`, kept whole but for
    over-long string leaves (stdout bodies, page text) — provenance fields
    (path, sha256, task, tab) are short and always survive."""
    import json as _json

    def trimmed(value: Any) -> Any:
        if isinstance(value, str) and len(value) > RESULT_CHARS:
            return value[:RESULT_CHARS]
        if isinstance(value, Mapping):
            return {str(k): trimmed(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [trimmed(v) for v in value]
        return value

    out: dict[str, Any] = trimmed(dict(reply))
    try:
        _json.dumps(out, default=str)
    except (TypeError, ValueError):
        out = {"status": str(reply.get("status") or "unavailable"),
               "note": "the result could not be persisted whole"}
    return out


@dataclass(frozen=True)
class _PendingResult:
    """A terminal outcome the store could not take yet: the segment's OWN
    result, retained with the execution owner — never genericised into
    `interrupted` — retried until the write lands. `consumed` is set when
    the caller already saw the reply (the fast path answers live), so the
    persisted row marks delivered instead of being handed off twice."""

    status: str
    result: dict[str, Any]
    consumed: bool = False


class SegmentRegistry:
    """The in-process executors holding granted segments: `task_id` → the
    asyncio task actually running it. Shared by `ComputerAdmission` (the
    submitter-side fast path and worker jobs) and `ComputerQueueRunner` (the
    deferred dispatch), so a cancel or a watchdog can reach the real work —
    a `running` row with no registered executor is owner-lost, never
    silently handed a successor."""

    def __init__(self) -> None:
        self._tasks: dict[UUID, asyncio.Task[Any]] = {}
        # `task_id` → the terminal outcome the durable row could not yet
        # take: an uncertain exit holding its real result, or a finish the
        # store refused — retried until the write lands, never replayed.
        self._pending: dict[UUID, _PendingResult] = {}

    def register(self, task_id: UUID, task: asyncio.Task[Any]) -> None:
        self._tasks[task_id] = task

    def unregister(self, task_id: UUID, task: asyncio.Task[Any]) -> None:
        if self._tasks.get(task_id) is task:
            del self._tasks[task_id]

    def executor(self, task_id: UUID) -> asyncio.Task[Any] | None:
        task = self._tasks.get(task_id)
        return task if task is not None and not task.done() else None

    def retain(self, task_id: UUID, status: str, result: Mapping[str, Any],
               *, consumed: bool = False) -> None:
        old = self._pending.get(task_id)
        self._pending[task_id] = _PendingResult(
            status=status, result=dict(result),
            consumed=consumed or (old.consumed if old is not None else False))

    def mark_consumed(self, task_id: UUID) -> None:
        """The caller saw the retained reply: the row, once it persists,
        counts as delivered, never handed to the room a second time."""
        old = self._pending.get(task_id)
        if old is not None and not old.consumed:
            self._pending[task_id] = replace(old, consumed=True)

    def pending_result(self, task_id: UUID) -> _PendingResult | None:
        return self._pending.get(task_id)

    def clear_pending(self, task_id: UUID) -> None:
        self._pending.pop(task_id, None)


class ComputerAdmission:
    def __init__(
        self, *, tasks: ComputerTaskStore, redis: Any,
        clock: Callable[[], datetime], poll_seconds: float = 0.25,
        executors: SegmentRegistry | None = None,
        reconcile: Callable[[str], Awaitable[str]] | None = None,
        audit: Callable[[ComputerTask, str], Awaitable[None]] | None = None,
    ) -> None:
        self._tasks = tasks
        self._redis = redis
        self._clock = clock
        self._poll = poll_seconds
        self.executors = executors or SegmentRegistry()
        # The VM's own quiet/busy probe — the only authority that can
        # confirm a cancelled command really stopped on the guest.
        self._reconcile = reconcile
        self._audit = audit

    async def _note(self, task: ComputerTask, transition: str) -> None:
        if self._audit is None:
            return
        try:
            await self._audit(task, transition)
        except Exception:
            log.exception("computer queue audit write failed")

    async def submit(
        self, *, scope: str, kind: str, payload: Mapping[str, Any],
        origin: Mapping[str, Any], wait_seconds: float = 0.0,
        deadline_at: datetime | None = None,
    ) -> SubmittedTask:
        """Admit one segment, FIFO within the scope."""
        segment_id = current_segment.get()
        if segment_id is not None:
            parent = await self._tasks.get(segment_id)
            if parent is not None and parent.scope == scope:
                return SubmittedTask(task=_ephemeral(scope, kind, self._clock()),
                                     inherited=True)
            # The durable row is bound to the scope it was admitted for. A
            # rebuilt call that resolves a DIFFERENT partition — the room's
            # unit or DM anchor moved under the queued row — is never run
            # under the old grant and never retargeted: it is unavailable.
            moved = _ephemeral(scope, kind, self._clock())
            return SubmittedTask(task=moved, result=replace(
                moved, status=UNAVAILABLE, finished_at=self._clock(),
                result={
                    "status": "unavailable",
                    "reason": "the task's scope moved while it was queued",
                    "note": "That computer task could not run: the unit or DM "
                            "it belonged to changed while it waited, and it is "
                            "never run against a different computer.",
                }))
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
        # The wait is for the RUNNER's grant+dispatch: `granted` is never
        # answered here — a row the runner claimed executes runner-side, and
        # returning it to the caller would run the segment twice. The caller
        # only ever sees the terminal result inside the window.
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            row = await self._tasks.get(task.id)
            if row is None or row.status in TERMINAL:
                return SubmittedTask(task=task, result=row)
            await asyncio.sleep(self._poll)
        row = await self._tasks.get(task.id)
        return SubmittedTask(
            task=task, result=row if row is not None and row.status in TERMINAL else None)

    async def wait_for_grant(self, task_id: UUID, *, until: datetime) -> ComputerTask | None:
        """A WORKER submitter's wait for its own claim: `try_claim_head`
        until the row grants, goes terminal, or `until` passes. Worker rows
        have no dispatcher, so the submitter's claim is the only grant."""
        while self._clock() < until:
            row = await self._tasks.get(task_id)
            if row is None or row.status in TERMINAL:
                return row
            if row.deadline_at is not None and row.deadline_at <= self._clock():
                return row  # past its bound: never claim expired work
            claimed = await self._tasks.try_claim_head(task_id, now=self._clock())
            if claimed is not None:
                return claimed
            await asyncio.sleep(self._poll)
        return await self._tasks.get(task_id)

    async def run_segment(
        self, task: ComputerTask, work: Awaitable[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Execute a granted segment in a registered child task.

        The child sets `current_segment` (nested calls inherit) and writes
        the terminal row itself — cancelled by the queue, failed by an
        exception, or the reply's own status — so a caller that dies
        mid-segment leaves a closing executor, not an orphaned grant. The
        reply reaching the caller marks the row `delivered`; a caller
        cancelled first leaves delivery to `on_terminal`."""
        outcome: dict[str, Mapping[str, Any]] = {}

        async def _run() -> None:
            token = current_segment.set(task.id)
            try:
                reply = dict(await work)
            except asyncio.CancelledError:
                reply = {"status": "cancelled",
                         "note": "The computer task was stopped while it ran."}
                # `cancelled` is written only when the VM itself reports no
                # surviving work — a stopped HTTP awaiter is not a stopped
                # guest command. Busy or unreadable leaves the row
                # `running`: owner-lost, the watchdog's reconcile owns it.
                await self._finish_guarded(task, "cancelled", reply,
                                           uncertain=True)
                raise
            except BaseException as exc:
                reply = {"status": "unavailable", "reason": type(exc).__name__}
                await self._finish_guarded(task, "unavailable", reply,
                                           uncertain=True)
                raise
            else:
                await self._finish_guarded(
                    task, row_status(reply), reply,
                    uncertain=bool(reply.get("uncertain")))
                outcome["reply"] = reply
            finally:
                current_segment.reset(token)

        worker = asyncio.ensure_future(_run())
        self.executors.register(task.id, worker)
        # Unregister when the EXECUTOR ends — never when the caller does: a
        # cancelled caller must not orphan a running segment's ownership.
        worker.add_done_callback(
            lambda done: self.executors.unregister(task.id, done))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            if not worker.done():
                # The CALLER was cancelled: the segment runs on and finishes
                # the row itself; `delivered_at` stays empty, so
                # `on_terminal` hands the terminal row to deferred delivery.
                raise
            # The queue cancelled the segment: its own except path closed
            # the row. The cancelled reply is what the caller is told.
            row = await self._tasks.get(task.id)
            if row is not None and row.status in TERMINAL:
                with contextlib.suppress(Exception):
                    await self._tasks.mark_delivered(task.id, now=self._clock())
            return {"status": "cancelled",
                    "note": "The computer task was stopped while it ran."}
        if worker.exception() is not None:
            # The executor failed outside the cancelled path; the row is
            # already closed `unavailable` by its own except.
            return {"status": "unavailable",
                    "note": "The computer task failed while it ran."}
        reply = outcome.get("reply") or {"status": "unavailable"}
        row = await self._tasks.get(task.id)
        if row is not None and row.status in TERMINAL:
            with contextlib.suppress(Exception):
                await self._tasks.mark_delivered(task.id, now=self._clock())
        else:
            # The row could not be confirmed terminal (an uncertain exit
            # still holding the scope, or a finish the store refused). The
            # caller saw the reply: the retained pending persists later and
            # marks delivered then — never a second handoff.
            self.executors.mark_consumed(task.id)
        return reply

    async def _finish_guarded(
        self, task: ComputerTask, status: str, reply: Mapping[str, Any],
        *, uncertain: bool,
    ) -> bool:
        return await _finish_guarded(
            executors=self.executors, reconcile=self._reconcile,
            finish=self._safe_finish, task=task, status=status,
            reply=reply, uncertain=uncertain)

    async def _safe_finish(
        self, task_id: UUID, status: str, result: Mapping[str, Any],
    ) -> bool:
        try:
            await self._tasks.finish(
                task_id, status=status, result=cut_result(result), now=self._clock())
        except Exception:
            log.exception("computer task %s could not write its terminal row", task_id)
            return False
        return True

    async def finish(self, task_id: UUID, *, status: str,
                     result: Mapping[str, Any]) -> None:
        """The executing caller's close: terminal row persisted."""
        await self._safe_finish(task_id, status, result)

    async def cancel(self, task_id: UUID) -> ComputerTask | None:
        """Cancel a task AND drain its executor. A queued row closes
        `cancelled` at once; a running one is flagged, its executor task is
        cancelled and awaited — only a confirmed stop returns `cancelled`.
        An executor that will not stop, or an owner that is gone, closes
        `interrupted` (uncertain effects are never replayed), never
        `cancelled` on a flag alone."""
        row = await self._tasks.request_cancel(task_id, now=self._clock())
        if row is None or row.status != RUNNING:
            return row
        executor = self.executors.executor(task_id)
        if executor is None:
            # No executor on this side: the GUEST's own quiet is the only
            # proof its command stopped. Busy or unreadable stays `running`
            # and the scope stays excluded.
            if await _guest_quiet(self._reconcile, row.scope, seconds=DRAIN_SECONDS) \
                    and not await _persist_pending(
                        executors=self.executors, tasks=self._tasks,
                        clock=self._clock, task_id=task_id) \
                    and self.executors.pending_result(task_id) is None:
                await self._tasks.mark_interrupted(
                    task_id, now=self._clock(), reason="the owner was lost")
                await self._note(row, "interrupted")
            return await self._tasks.get(task_id)
        executor.cancel()
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(executor), DRAIN_SECONDS)
        fresh = await self._tasks.get(task_id)
        if fresh is not None and fresh.status == RUNNING \
                and await _guest_quiet(self._reconcile, fresh.scope,
                                       seconds=DRAIN_SECONDS) \
                and not await _persist_pending(
                    executors=self.executors, tasks=self._tasks,
                    clock=self._clock, task_id=task_id) \
                    and self.executors.pending_result(task_id) is None:
            await self._tasks.mark_interrupted(
                task_id, now=self._clock(),
                reason="the cancellation could not be confirmed")
            await self._note(fresh, "interrupted")
            fresh = await self._tasks.get(task_id)
        return fresh

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


async def _finish_guarded(
    *, executors: SegmentRegistry,
    reconcile: Callable[[str], Awaitable[str]] | None,
    finish: Callable[[UUID, str, Mapping[str, Any]], Awaitable[bool]],
    task: ComputerTask, status: str, reply: Mapping[str, Any],
    uncertain: bool,
) -> bool:
    """Terminalize, gated on the guest having actually stopped.

    An UNCERTAIN exit — the transport dropped mid-command, the executor
    raised, the caller cancelled, the outcome itself says so — cannot say
    the guest's work ended. Only an observed `quiet` verdict proves it:
    busy or unknown keeps the row `running` and the scope blocked, with
    the real outcome retained for the reconcile pass to persist (never
    replayed). A certain exit — an answered result or refusal — finishes
    straight away; a finish the store refuses is retained the same way,
    so the durable row is written before delivery rather than lost."""
    persisted = False
    if uncertain and not await _guest_quiet(reconcile, task.scope,
                                            seconds=DRAIN_SECONDS):
        pass
    else:
        persisted = await finish(task.id, status, reply)
    if not persisted:
        executors.retain(task.id, status, cut_result(dict(reply)))
    return persisted


async def _persist_pending(
    *, executors: SegmentRegistry, tasks: ComputerTaskStore,
    clock: Callable[[], datetime], task_id: UUID,
) -> bool:
    """Persist a retained outcome once the guest is observed quiet (or the
    row is past `ORPHAN_AFTER`): the row goes terminal with the ORIGINAL
    result, never a generic interruption. True when the row is terminal —
    its result landed, or someone else already closed it. False keeps the
    pending result for the next pass."""
    pending = executors.pending_result(task_id)
    if pending is None:
        return False
    try:
        await tasks.finish(task_id, status=pending.status,
                           result=pending.result, now=clock())
        row = await tasks.get(task_id)
    except Exception:
        log.exception("computer task retained finish failed (kept for the next pass)")
        return False
    if row is not None and row.status in TERMINAL:
        executors.clear_pending(task_id)
        if pending.consumed and row.delivered_at is None:
            with contextlib.suppress(Exception):
                await tasks.mark_delivered(task_id, now=clock())
        return True
    if row is None:
        executors.clear_pending(task_id)
    return False


async def _guest_quiet(
    reconcile: Callable[[str], Awaitable[str]] | None,
    scope: str, *, seconds: float,
) -> bool:
    """Bounded wait for the scope's VM to report NO surviving work: `quiet`
    is the one verdict that confirms a cancelled command actually stopped
    (its process group is gone). Busy — or the VM that will not say — is
    never a stop, so the row keeps `running` and the scope stays excluded."""
    if reconcile is None:
        return False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        with contextlib.suppress(Exception):
            if await reconcile(scope) == "quiet":
                return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.5)


class ComputerQueueRunner:
    """The granter/dispatcher/watchdog loop. `run_forever` is composition's
    task; `tick` is one reconcile+grant pass (the testable half).

    `dispatch` maps a task `kind` to the executor that rebuilds and runs its
    durable request — `native`, `browse` and `capture` re-enter the tool
    path; kinds with no dispatcher (`worker`) are claimed only by their own
    submitter, never by the runner."""

    def __init__(
        self, *, tasks: ComputerTaskStore, redis: Any,
        clock: Callable[[], datetime], metrics: Any = None,
        batch: int = 20, watch_seconds: float = 1.0,
        on_terminal: Callable[[ComputerTask], Awaitable[None]] | None = None,
        reconcile: Callable[[str], Awaitable[str]] | None = None,
        dispatch: Mapping[str, Callable[[ComputerTask], Awaitable[Mapping[str, Any]]]]
        | None = None,
        executors: SegmentRegistry | None = None,
        audit: Callable[[ComputerTask, str], Awaitable[None]] | None = None,
    ) -> None:
        self._tasks = tasks
        self._redis = redis
        self._clock = clock
        self._metrics = metrics
        self._batch = batch
        self._on_terminal = on_terminal
        self._reconcile_scope = reconcile
        self._dispatch = dict(dispatch or {})
        self._executors = executors or SegmentRegistry()
        self._audit = audit
        self._watch_seconds = watch_seconds
        self._group_ready: set[str] = set()
        self._watchers: set[asyncio.Task[Any]] = set()

    def set_dispatch(
        self, kind: str,
        executor: Callable[[ComputerTask], Awaitable[Mapping[str, Any]]],
    ) -> None:
        """Bind a kind's executor after construction — composition builds the
        tool-facing dispatcher after the runner exists."""
        if kind in TASK_KINDS:
            self._dispatch[kind] = executor

    async def _group(self, stream: str) -> None:
        if stream in self._group_ready:
            return
        with contextlib.suppress(Exception):
            await self._redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
        self._group_ready.add(stream)

    async def _scopes(self) -> list[str]:
        """The scopes with open work, from the table — the record of truth —
        never a Redis SCAN (the dedicated credential stays narrow)."""
        return await self._tasks.open_scopes(limit=self._batch * 4)

    async def _note(self, task: ComputerTask, transition: str) -> None:
        if self._audit is None:
            return
        try:
            await self._audit(task, transition)
        except Exception:
            log.exception("computer queue audit write failed")

    async def reconcile(self) -> None:
        """Publish gaps and dead grants: an unannounced `queued` row gets its
        stream entry; a stale `running` row past the orphan bound is closed
        `interrupted` — its effects are uncertain and never re-run."""
        try:
            grace = self._clock() - timedelta(seconds=10)
            for task in await self._tasks.unannounced(limit=self._batch):
                if task.created_at < grace:
                    await self._redis.xadd(_stream(task.scope), {"task": str(task.id)})
            for task in await self._tasks.interrupted(
                    older_than=self._clock() - ORPHAN_AFTER, limit=self._batch):
                if self._executors.executor(task.id) is not None:
                    continue  # alive in-process: its own deadline governs it
                verdict = "unknown"
                if self._reconcile_scope is not None:
                    with contextlib.suppress(Exception):
                        verdict = await self._reconcile_scope(task.scope)
                if verdict != "quiet":
                    continue  # busy or unreadable: never closed on age alone
                # A retained outcome persists first: the row closes with
                # the segment's REAL result, `interrupted` only when the
                # owner left nothing behind.
                if not await _persist_pending(
                        executors=self._executors, tasks=self._tasks,
                        clock=self._clock, task_id=task.id) \
                        and self._executors.pending_result(task.id) is None:
                    await self._tasks.mark_interrupted(
                        task.id, now=self._clock(), reason="the owner was lost")
                    await self._note(task, "interrupted")
        except Exception:
            log.exception("computer queue reconcile failed")

    async def tick(self) -> int:
        """One pass over every scope's stream; how many segments were granted."""
        await self.reconcile()
        granted = 0
        for scope in await self._scopes():
            granted += await self._dispatch_scope(scope)
        await self._deliver_pending()
        return granted

    async def _deliver_pending(self) -> None:
        """Terminal rows whose result never reached a room, discovered from
        the table — not the stream: a last terminal row with no open
        siblings (a fast-path row its caller lost, a delivery that kept
        failing after the row's siblings closed) is re-driven until
        `delivered_at` lands, never stranded."""
        if self._on_terminal is None:
            return
        try:
            rows = await self._tasks.undelivered(limit=self._batch)
        except Exception:
            log.exception("computer queue undelivered read failed")
            return
        for row in rows:
            try:
                await self._on_terminal(row)
            except Exception:
                log.exception(
                    "computer task %s delivery failed; retried next pass", row.id)

    async def _dispatch_scope(self, scope: str) -> int:
        stream = _stream(scope)
        await self._group(stream)
        try:
            auto = await self._redis.xautoclaim(
                stream, GROUP, CONSUMER, min_idle_time=60_000, start_id="0-0",
                count=self._batch)
            pending = list(auto[1] if len(auto) > 1 else [])
            fresh = await self._redis.xreadgroup(
                GROUP, CONSUMER, {stream: ">"}, count=self._batch)
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
        try:
            open_rows = await self._tasks.open_for_scope(scope, limit=self._batch)
        except Exception:
            log.exception("computer queue scope read failed")
            return 0
        running = [r for r in open_rows if r.status == RUNNING]
        if running:
            # One running segment blocks the scope; whether it is still
            # OWNED decides what blocks and for how long.
            for running_row in running:
                if self._executors.executor(running_row.id) is not None:
                    if (running_row.deadline_at is not None
                            and running_row.deadline_at <= self._clock()):
                        # Past its bound: cancel the real work; its own
                        # handler closes the row.
                        executor = self._executors.executor(running_row.id)
                        if executor is not None:
                            executor.cancel()
                    continue
                # Owner-lost: no executor here. Reconcile the VM's ACTUAL
                # process state before this scope may grant again — an idle
                # timeout is not proof the old command stopped.
                verdict = "unknown"
                if self._reconcile_scope is not None:
                    with contextlib.suppress(Exception):
                        verdict = await self._reconcile_scope(scope)
                if verdict == "quiet" and not await _persist_pending(
                        executors=self._executors, tasks=self._tasks,
                        clock=self._clock, task_id=running_row.id) \
                        and self._executors.pending_result(running_row.id) is None:
                    await self._tasks.mark_interrupted(
                        running_row.id, now=self._clock(),
                        reason="the owner was lost; the VM is quiet")
                    await self._note(running_row, "interrupted")
                # busy/unknown: keep the row running and the scope blocked;
                # the orphan bound is the outer limit.
            return 0
        granted = 0
        for mid, fields in seen:
            task_id = _task_id(fields)
            if task_id is None:
                await self._ack(stream, mid)
                continue
            row = await self._tasks.get(task_id)
            if row is None:
                await self._ack(stream, mid)
                continue
            if row.status in TERMINAL:
                await self._settle(stream, mid, row)
                continue
            if row.status != QUEUED:
                await self._ack(stream, mid)
                continue
            # Re-check at the queue head: cancelled or past-deadline never
            # starts — and a queued cancel is still DELIVERED. A
            # `maintenance` row is claimed by its own submitter in the same
            # call it was written: still `queued` here means the submitter
            # died between the two, so the row is closed, never started —
            # it has no dispatcher and must not sit at the head forever.
            if (row.kind == "maintenance"
                    or (row.deadline_at is not None
                        and row.deadline_at <= self._clock())
                    or row.cancel_requested_at is not None):
                cancelled = await self._tasks.request_cancel(row.id, now=self._clock())
                await self._note(row, "cancelled")
                await self._settle(stream, mid, cancelled or row)
                continue
            if row.kind not in self._dispatch:
                continue  # submitter-owned kind (a worker job): never runner-run
            claimed = await self._tasks.try_claim_head(row.id, now=self._clock())
            if claimed is None:
                continue  # not head: FIFO, never skip ahead
            granted += 1
            await self._note(claimed, "granted")
            worker = asyncio.ensure_future(self._execute(stream, mid, claimed))
            self._executors.register(claimed.id, worker)
            self._watchers.add(worker)
            worker.add_done_callback(self._watch_done)
            break
        return granted

    def _watch_done(self, watcher: asyncio.Task[Any]) -> None:
        self._watchers.discard(watcher)
        if watcher.cancelled():
            return
        exc = watcher.exception()
        if exc is not None:
            log.error("computer queue worker died", exc_info=exc)

    async def _ack(self, stream: str, mid: Any) -> None:
        with contextlib.suppress(Exception):
            await self._redis.xack(stream, GROUP, mid)

    async def _settle(self, stream: str, mid: Any, row: ComputerTask) -> None:
        """Terminal delivery THEN ack: the row stands first, then the room
        is told, then the pointer is dropped. A delivery failure leaves the
        entry pending — reclaimed and retried, never silently dropped. A
        row that is not terminal (its `finish` write could not persist) is
        neither delivered nor acked: the pending pointer drives the later
        pass that reconciles it."""
        if row.status not in TERMINAL:
            return
        if row.delivered_at is None and self._on_terminal is not None:
            try:
                await self._on_terminal(row)
            except Exception:
                log.exception("computer task %s delivery failed; the pointer stays", row.id)
                return
            await self._note(row, "delivered")
        await self._ack(stream, mid)

    async def _execute(self, stream: str, mid: Any, task: ComputerTask) -> None:
        """Run a claimed row's durable request through its dispatcher, close
        the row with the typed reply, then settle (deliver + ack)."""
        me = asyncio.current_task()
        try:
            executor = self._dispatch[task.kind]
            token = current_segment.set(task.id)
            try:
                reply = dict(await executor(task))
            except asyncio.CancelledError:
                reply = {"status": "cancelled",
                         "note": "The computer task was stopped while it ran."}
                # `cancelled` only on an observed-quiet guest — the same
                # rule as the submitter side: a stopped awaiter is not a
                # stopped guest command.
                await self._finish_guarded(task, "cancelled", reply,
                                           uncertain=True)
                raise
            except Exception as exc:
                log.exception("computer task %s dispatch failed", task.id)
                reply = {"status": "unavailable", "reason": type(exc).__name__}
                await self._finish_guarded(task, "unavailable", reply,
                                           uncertain=True)
            else:
                await self._finish_guarded(
                    task, row_status(reply), reply,
                    uncertain=bool(reply.get("uncertain")))
            finally:
                current_segment.reset(token)
            row = await self._tasks.get(task.id)
            if row is not None:
                await self._settle(stream, mid, row)
        finally:
            if me is not None:
                self._executors.unregister(task.id, me)

    async def _finish_guarded(
        self, task: ComputerTask, status: str, reply: Mapping[str, Any],
        *, uncertain: bool,
    ) -> bool:
        return await _finish_guarded(
            executors=self._executors, reconcile=self._reconcile_scope,
            finish=self._safe_finish, task=task, status=status,
            reply=reply, uncertain=uncertain)

    async def _safe_finish(self, task_id: UUID, status: str,
                           result: Mapping[str, Any]) -> bool:
        try:
            await self._tasks.finish(
                task_id, status=status, result=cut_result(result), now=self._clock())
        except Exception:
            log.exception("computer task %s could not write its terminal row", task_id)
            return False
        return True

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
    row back: still open is `RUNNING`, `done` is `SUCCEEDED` with the
    segment's actual report (its answer, capture path or note — the typed
    result, never a generic word), anything else is `FAILED` with the row's
    reason."""

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
        result = dict(row.result or {})
        # The typed result the row actually persisted: the prose the tool
        # wrote first, then its provenance fields (capture path and digest,
        # landing url, exit code), then the command output itself — a
        # delivery that drops stdout or the capture's digest answers
        # nothing.
        parts = [
            str(result.get(key)) for key in ("answer", "report", "note", "reason")
            if result.get(key)
        ]
        for key in ("path", "url", "title", "media_type", "sha256",
                    "exit_code", "steps", "task", "tab"):
            value = result.get(key)
            if value not in (None, ""):
                parts.append(f"{key}: {value}")
        for key in ("stdout", "stderr"):
            value = result.get(key)
            if value:
                # Persisted rows are already bounded (`RESULT_CHARS`):
                # reporting them whole keeps the tail the producer wrote.
                parts.append(f"{key}:\n{value}")
        report = "\n".join(parts) or row.status
        if row.status == "done":
            return JobResult(status=JobStatus.SUCCEEDED, report=report,
                             summary=report)
        return JobResult(status=JobStatus.FAILED, error=report,
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

    Retryable and deduplicated: an already-`delivered` row, a worker whose
    own job row IS the delivery (`origin.deliver == "job"`), and a task with
    a `compute_jobs` row already filed for it (a retried delivery after a
    crash) all stop here — the entry is re-driven until `delivered_at`
    lands, never duplicated. A row naming no room is only marked delivered.
    """
    import hashlib
    import json as _json
    from uuid import uuid4

    from nora.domain.compute import ComputeJob, ComputeJobState, normalise_compute_job

    if row.status not in TERMINAL or row.delivered_at is not None:
        return
    origin = dict(row.origin or {})
    platform = str(origin.get("platform") or "")
    conversation_id = str(origin.get("conversation_id") or "")
    if platform and conversation_id and origin.get("deliver") != "job":
        # Re-check live: a fast-path `mark_delivered` landing between the
        # settle read and here must not file a second delivery.
        fresh = await tasks.get(row.id)
        if fresh is not None and fresh.delivered_at is not None:
            return
        existing = await jobs.kind_since(
            kind="computer", since=row.created_at - timedelta(minutes=1))
        if any(j.backend_job_id == str(row.id) for j in existing):
            await tasks.mark_delivered(row.id, now=clock())
            return
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
