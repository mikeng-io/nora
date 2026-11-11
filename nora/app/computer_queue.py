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
import fcntl
import json as _json
import logging
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
#: The stream's approximate bound. It is a POINTER, never the record —
#: `computer_tasks` holds every segment; a lost pointer is reconcilable.
STREAM_MAX = 128
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

    def __init__(self, *, pending_dir: Path | None = None) -> None:
        self._tasks: dict[UUID, asyncio.Task[Any]] = {}
        # `task_id` → the terminal outcome the durable row could not yet
        # take: an uncertain exit holding its real result, or a finish the
        # store refused — retried until the write lands, never replayed.
        self._pending: dict[UUID, _PendingResult] = {}
        # `pending_dir` makes the map durable across restart. It MUST be an
        # app-owned writable persistent root (`/app/state/computer` in the
        # deployment: the same mount class as OAuth state — private, not the
        # read-only provisioner scope registry and not the ephemeral image
        # layer). One process owns it for its whole lifetime via an
        # exclusive `flock`; a second owner fails closed at construction —
        # two owners writing whole-map snapshots would lose each other's
        # entries. Construction refuses unsafe state rather than treating
        # corrupt/unreadable data as an empty store.
        self._pending_dir = pending_dir
        self._pending_path: Path | None = None
        self._lock_fd: int | None = None
        self._closed = False
        self.accepting = True
        if pending_dir is not None:
            self._open_pending_store()

    def close(self) -> None:
        """Release the lifetime lock for explicit shutdown and for GC.

        One process owns the journal for its lifetime; `close` is how that
        lifetime ENDS cleanly — a final checkpoint, then the fd (whose close
        drops the flock). Idempotent, and safe from `__del__`: an
        abandoned registry still releases its lock, so the next process —
        or the next owner in the same tests — is not refused forever."""
        if self._closed:
            return
        self.accepting = False
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("computer executors still hold the journal")
        if self._pending_path is not None and self._pending and not self._spill():
            raise RuntimeError("computer results could not be checkpointed before close")
        self._closed = True
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)  # closing the fd releases the flock

    def __del__(self) -> None:
        self.close()

    def _open_pending_store(self) -> None:
        """Take exclusive lifetime ownership and recover the durable map.

        Refuses (raises) when: the root is not a real private directory, a
        second owner holds the lock, or the stored snapshot is
        missing-shaped/corrupt — quarantined to `<name>.corrupt-<utc>` so
        evidence survives and an operator decides, never silently emptied
        and later overwritten."""
        directory = self._pending_dir
        if directory is None:
            return  # invariant: called only when pending_dir is set
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeError(
                f"computer pending store root {directory} is not a real directory")
        directory.chmod(0o700)
        lock_fd = os.open(directory / "pending-results.lock",
                          os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            raise RuntimeError(
                "a second owner was refused the computer pending store at "
                f"{directory} — one process owns it for its lifetime") from None
        self._pending_path = directory / "pending-results.json"
        try:
            self._load_pending()
        except BaseException:
            os.close(lock_fd)
            raise
        self._lock_fd = lock_fd

    def _load_pending(self) -> None:
        path = self._pending_path
        if path is None or not path.exists():
            return  # first use: absent is empty, never an error
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or path.is_symlink():
            return self._quarantine(path, "not a regular file")
        try:
            raw = path.read_bytes().decode("utf-8")
            data = _json.loads(raw)
        except (OSError, UnicodeDecodeError, _json.JSONDecodeError) as exc:
            return self._quarantine(path, f"unreadable: {exc}")
        if not isinstance(data, dict):
            return self._quarantine(path, "top level is not an object")
        pending: dict[UUID, _PendingResult] = {}
        for key, entry in data.items():
            try:
                task_id = UUID(key)
                status = entry["status"]
                result = entry["result"]
                consumed = entry["consumed"]
            except (KeyError, TypeError, ValueError) as exc:
                return self._quarantine(path, f"entry {key!r} invalid: {exc}")
            if (status not in TERMINAL or not isinstance(result, dict)
                    or type(consumed) is not bool):
                return self._quarantine(
                    path, f"entry {key!r} has non-strict fields")
            pending[task_id] = _PendingResult(
                status=status, result=result, consumed=consumed)
        self._pending = pending

    def _quarantine(self, path: Path, why: str) -> None:
        # COPY the evidence aside; the corrupt ORIGINAL stays in place so
        # every later construction refuses again — a single rename would let
        # the next start read "absent" as first-use and discard it.
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        quarantined = path.with_name(f"{path.name}.corrupt-{stamp}")
        with contextlib.suppress(OSError):
            shutil.copy2(path, quarantined)
        raise RuntimeError(
            f"computer pending store at {path} refused as corrupt ({why}); "
            f"evidence copied to {quarantined} — the file stays corrupt in "
            "place until an operator removes or repairs it deliberately")

    def _spill(self) -> bool:
        """Rewrite the durable map: strict typed serialization (no coercion —
        a result that does not round-trip JSON fails the checkpoint), a
        fresh private temp file, fsync, atomic replace, then a directory
        fsync so the rename survives power loss. `False` means NOT durable:
        callers propagate that, and the in-memory copy still holds the
        same-process semantics."""
        if self._pending_path is None:
            return False
        payload = {
            str(task_id): {"status": p.status, "result": p.result,
                           "consumed": p.consumed}
            for task_id, p in self._pending.items()
        }
        directory = self._pending_path.parent
        tmp_name: str | None = None
        try:
            body = _json.dumps(payload)  # strict: no default=str coercion
            fd, tmp_name = tempfile.mkstemp(
                dir=directory, prefix="pending-results.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self._pending_path)
            tmp_name = None
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            return True
        except (OSError, TypeError, ValueError):
            log.warning("computer pending-result checkpoint failed "
                        "(entry kept in memory; callers told)", exc_info=True)
            return False
        finally:
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)

    def register(self, task_id: UUID, task: asyncio.Task[Any]) -> None:
        self._tasks[task_id] = task

    def unregister(self, task_id: UUID, task: asyncio.Task[Any]) -> None:
        if self._tasks.get(task_id) is task:
            del self._tasks[task_id]

    def executor(self, task_id: UUID) -> asyncio.Task[Any] | None:
        task = self._tasks.get(task_id)
        return task if task is not None and not task.done() else None

    def retain(self, task_id: UUID, status: str, result: Mapping[str, Any],
               *, consumed: bool = False) -> bool:
        """Journal an outcome the store could not (yet) take. Returns the
        durable-checkpoint result: `False` means the file did not take it —
        the entry still holds same-process semantics in memory, and the
        caller must not imply durable retention happened."""
        old = self._pending.get(task_id)
        self._pending[task_id] = _PendingResult(
            status=status, result=dict(result),
            consumed=consumed or (old.consumed if old is not None else False))
        # Durable only when a journal exists AND took the checkpoint; the
        # memory entry always holds so this process keeps its semantics —
        # the CALLER decides whether the failed write must revert a
        # `consumed` claim (`mark_consumed` handles it).
        return self._pending_path is not None and self._spill()

    def mark_consumed(self, task_id: UUID) -> bool:
        """The caller saw the retained reply: the row, once it persists,
        counts as delivered, never handed to the room a second time.
        `False` = the consumed disposition did not reach disk — the
        in-memory marker still prevents a same-process re-delivery, but a
        restart before the next successful checkpoint can hand the
        live-delivered result to deferred delivery again. That residual
        cannot be closed locally once the reply is out; the caller logs it
        as a real boundary, never a durable success."""
        old = self._pending.get(task_id)
        if old is None or old.consumed:
            return self._pending_path is not None
        self._pending[task_id] = replace(old, consumed=True)
        if not self._spill():
            self._pending[task_id] = old  # durable claim failed: undo the
            return False                  # in-memory suppression as well
        return True

    def pending_result(self, task_id: UUID) -> _PendingResult | None:
        return self._pending.get(task_id)

    def clear_pending(self, task_id: UUID) -> bool:
        """Drop the entry and checkpoint. `False` = the durable map still
        holds it: a stale entry replays safely (`_persist_pending` converges
        on the terminal row, SQL `delivered_at` is the authority), so the
        caller logs and continues rather than treating the drop as lost."""
        self._pending.pop(task_id, None)
        return self._spill()


async def _lifecycle_work(lifecycle: Any, task: ComputerTask,
                          work: Awaitable[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Both executor entry points share this boundary; observations never enter."""
    if lifecycle is None or task.kind == "maintenance":
        return await work
    acquired = False
    started = False
    try:
        result = await lifecycle.request("acquire", task.scope, str(task.id))
        if result.get("status") != "ok":
            return {"status": "unavailable", "reason": result.get("reason"),
                    "no_guest_effect": True}
        acquired = True
        started = True
        return await work
    finally:
        if not started and hasattr(work, "close"):
            work.close()
        if acquired:
            # The controller only releases after positive drain; failure leaves
            # durable exclusion for elected recovery rather than lease expiry.
            await asyncio.shield(lifecycle.request("release", task.scope, str(task.id)))


class ComputerAdmission:
    def deadline_after(self, seconds: float) -> datetime:
        return self._clock() + timedelta(seconds=seconds)

    def __init__(
        self, *, tasks: ComputerTaskStore, redis: Any,
        clock: Callable[[], datetime], poll_seconds: float = 0.25,
        executors: SegmentRegistry | None = None,
        reconcile: Callable[[str], Awaitable[str]] | None = None,
        audit: Callable[[ComputerTask, str], Awaitable[None]] | None = None,
        lifecycle: Any = None,
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
        self._lifecycle = lifecycle

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
        if not self.executors.accepting:
            raise RuntimeError("computer admission is closing")
        segment_id = current_segment.get()
        if segment_id is not None:
            parent = await self._tasks.get(segment_id)
            now = self._clock()
            # Inheritance needs a LIVE grant: `running`, not cancel-flagged,
            # not past its deadline, and still OWNED by a registered
            # executor of this registry. A terminal, cancelled, expired or
            # owner-lost parent — or a segment id nothing owns — cannot
            # authorize new work under its slot; the call falls through to
            # a FRESH admission on the caller's own authority.
            live = (
                parent is not None and parent.status == RUNNING
                and parent.cancel_requested_at is None
                and (parent.deadline_at is None or parent.deadline_at > now)
                and self.executors.executor(parent.id) is not None)
            if live and parent is not None and parent.scope == scope:
                return SubmittedTask(task=_ephemeral(scope, kind, now),
                                     inherited=True)
            if live:
                # The durable row is bound to the scope it was admitted for. A
                # rebuilt call that resolves a DIFFERENT partition — the room's
                # unit or DM anchor moved under the queued row — is never run
                # under the old grant and never retargeted: it is unavailable.
                moved = _ephemeral(scope, kind, now)
                return SubmittedTask(task=moved, result=replace(
                    moved, status=UNAVAILABLE, finished_at=self._clock(),
                    result={
                        "status": "unavailable",
                        "reason": "the task's scope moved while it was queued",
                        "note": "That computer task could not run: the unit or DM "
                                "it belonged to changed while it waited, and it is "
                                "never run against a different computer.",
                        "no_guest_effect": True,
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
                reply = dict(await _lifecycle_work(self._lifecycle, task, work))
            except asyncio.CancelledError:
                reply = {"status": "cancelled",
                         "note": "The computer task was stopped while it ran."}
                # `cancelled` is written only when the VM itself reports no
                # surviving work — a stopped HTTP awaiter is not a stopped
                # guest command. Busy or unreadable leaves the row
                # `running`: owner-lost, the watchdog's reconcile owns it.
                await self._finish_guarded(task, "cancelled", reply)
                raise
            except BaseException as exc:
                reply = {"status": "unavailable", "reason": type(exc).__name__}
                await self._finish_guarded(task, "unavailable", reply)
                raise
            else:
                # `_finish_guarded` journals the returned outcome before its
                # quiet/SQL waits — the shared path every executor and the
                # cancellation/exception exits pass through — and the entry
                # is NOT cleared after a successful finish: `consumed`/
                # `delivered` disposition is only durably settled by
                # `delivered()` (live handoff) or the deferred path.
                await self._finish_guarded(task, row_status(reply), reply)
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
            if row is not None and row.status in TERMINAL \
                    and not await self.delivered(task.id):
                return {"status": "unavailable", "task": str(task.id),
                        "note": "That computer task was stopped, but the "
                                "delivery mark could not be committed."}
            return {"status": "cancelled",
                    "note": "The computer task was stopped while it ran."}
        if worker.exception() is not None:
            # The executor failed outside the cancelled path; the row is
            # already closed `unavailable` by its own except.
            return {"status": "unavailable",
                    "note": "The computer task failed while it ran."}
        reply = dict(outcome.get("reply") or {"status": "unavailable"})
        row = await self._tasks.get(task.id)
        if row is not None and row.status in TERMINAL:
            durable = await self.delivered(task.id)
        else:
            # The row could not be confirmed terminal (an uncertain exit
            # still holding the scope, or a finish the store refused). The
            # caller saw the reply: the retained pending persists later and
            # marks delivered then — never a second handoff.
            durable = self.executors.mark_consumed(task.id)
        if not durable:
            # The durable handoff could not be committed — the reply does
            # NOT go out as the answer (delivering it AND leaving the result
            # deferred-eligible would be the duplicate this contract
            # forbids). The caller gets the task's identity: the original
            # outcome stays journaled and the deferred path delivers it
            # while the row's `delivered_at` is empty — the journal never
            # carries an unconfirmed `consumed` claim.
            return {"status": "unavailable", "task": str(task.id),
                    "note": "That computer task finished, but its answer "
                            "could not be committed for delivery — it may "
                            "still arrive on its own."}
        return reply

    async def _finish_guarded(
        self, task: ComputerTask, status: str, reply: Mapping[str, Any],
    ) -> bool:
        return await _finish_guarded(
            executors=self.executors, reconcile=self._reconcile,
            finish=self._safe_finish, task=task, status=status,
            reply=reply)

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
        """The executing caller's close: terminal row persisted, under the
        same guest-quiet gate every executor honors — a worker whose nested
        segments left detached guest work stays `running` until the VM is
        observed quiet."""
        task = await self._tasks.get(task_id)
        if task is None:
            return
        await self._finish_guarded(task, status, result)

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

    async def delivered(self, task_id: UUID) -> bool:
        """The terminal result was consumed live: `delivered_at`, so
        `on_terminal` does not hand it to deferred delivery. Returns the
        DURABLE handoff — journal checkpoint AND SQL stamp — not merely
        that the caller was told."""
        row = await self._tasks.get(task_id)
        if row is None:
            return True  # nothing owed: no row, no disposition to settle
        if row.status not in TERMINAL:
            return False  # not durable evidence — a live row's mark is not
        # The journal records the OUTCOME (`consumed` stays False). The
        # delivery intent is operation-local and goes straight to the SQL
        # authority — never into the serialized map, where an unrelated
        # scope's checkpoint would flush an unconfirmed `consumed` claim to
        # disk and a restart would stamp a never-delivered result.
        self.executors.retain(task_id, row.status, row.result)
        try:
            # The election IS the durable disposition: a deferred outbox
            # filing that beats this stamp means the room is being told
            # through the job — the caller must NOT also get the result.
            won = await self._tasks.elect_delivery(task_id, now=self._clock())
        except Exception:  # noqa: BLE001 - the stamp store is the boundary; it must answer
            log.error("computer task %s delivery stamp could not be "
                      "committed; the original result stays "
                      "deferred-eligible", task_id)
            return False
        if won is None:
            # Another disposition landed first (the deferred outbox elected
            # itself): the result is owned elsewhere, not by this caller.
            log.info("computer task %s live delivery lost the election; the "
                     "deferred outbox owns it", task_id)
            return False
        self.executors.clear_pending(task_id)
        return True

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
) -> bool:
    """Terminalize, gated on the guest having actually stopped.

    EVERY guest-effectful outcome — answered, refused mid-run, uncertain or
    cancelled — holds the scope's exclusion until the guest reports quiet:
    an answered `computer_run` can leave a detached `nohup` or tmux job
    behind, and the HTTP reply is not proof the work stopped. Only an
    observed `quiet` verdict proves it: busy or unknown keeps the row
    `running` and the scope blocked, with the real outcome retained for
    the reconcile pass to persist (never replayed). The one exception is a
    reply that provably never reached the guest (`no_guest_effect` — a
    pre-admission refusal); it terminalizes at once."""
    # The ORIGINAL outcome is journaled before ANY wait — quiet-check or
    # SQL write — because a restart inside either would otherwise lose it
    # and a fresh owner would close the row as a generic interruption.
    # `consumed` stays False until a durable disposition exists; a failed
    # checkpoint is logged, never faked.
    if not executors.retain(task.id, status, cut_result(dict(reply))):
        log.error("computer task %s outcome retained in memory only; the "
                  "durable journal did not take it", task.id)
    persisted = False
    if reply.get("no_guest_effect") or await _guest_quiet(
            reconcile, task.scope, seconds=DRAIN_SECONDS):
        persisted = await finish(task.id, status, reply)
    if not persisted:
        log.error("computer task %s terminal row did not land; the retained "
                  "outcome stays journaled for recovery", task.id)
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
        row = await tasks.get(task_id)
        if row is None or row.status not in TERMINAL:
            await tasks.finish(task_id, status=pending.status,
                               result=pending.result, now=clock())
            row = await tasks.get(task_id)
    except Exception:
        log.exception("computer task retained finish failed (kept for the next pass)")
        return False
    if row is not None and row.status in TERMINAL:
        if pending.consumed and row.delivered_at is None:
            try:
                # A caller-consumed row elects the stamp alone — never a
                # deferred job, the reply already went out live.
                await tasks.elect_delivery(task_id, now=clock())
            except Exception:
                log.exception("computer task consumed disposition failed (retained)")
                return False
        if not executors.clear_pending(task_id):
            log.error("computer task %s pending entry cleared but the "
                      "checkpoint did not land; a stale entry may replay "
                      "safely on restart", task_id)
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
        lifecycle: Any = None,
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
        self._lifecycle = lifecycle
        self._watch_seconds = watch_seconds
        self._group_ready: set[str] = set()
        self._watchers: set[asyncio.Task[Any]] = set()

    async def stop(self, *, drain_seconds: float = 30.0) -> None:
        self.stop_admission()
        active = [t for t in self._executors._tasks.values() if not t.done()]
        for task in active:
            if not task.cancelling():
                task.cancel()
        if active:
            _, pending = await asyncio.wait(active, timeout=drain_seconds)
            if pending:
                raise TimeoutError("computer executors have not drained")

    def stop_admission(self) -> None:
        self._executors.accepting = False

    async def close(self) -> None:
        if any(not t.done() for t in self._executors._tasks.values()):
            raise RuntimeError("computer executors have not drained")
        close = getattr(self._redis, "aclose", None)
        if close is not None:
            await close()
        self._executors.close()

    def set_dispatch(
        self, kind: str,
        executor: Callable[[ComputerTask], Awaitable[Mapping[str, Any]]],
    ) -> None:
        """Bind a kind's executor after construction — composition builds the
        tool-facing dispatcher after the runner exists."""
        if kind in TASK_KINDS:
            self._dispatch[kind] = executor

    async def _group(self, stream: str) -> None:
        """Create the consumer group once per stream. Only an actual create
        or the group's own BUSYGROUP answer marks it ready — a transient or
        NOGROUP failure is retried next pass, never cached as success."""
        if stream in self._group_ready:
            return
        try:
            await self._redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001 - BUSYGROUP is the only ready verdict
            if "BUSYGROUP" not in str(exc):
                log.warning("computer queue group create failed for %s; "
                            "retried next pass", stream)
                return
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
        if not self._executors.accepting:
            return 0
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
            pending = self._executors.pending_result(row.id)
            if pending is not None and pending.consumed:
                await _persist_pending(
                    executors=self._executors, tasks=self._tasks,
                    clock=self._clock, task_id=row.id)
                continue
            try:
                await self._on_terminal(row)
            except Exception:
                log.exception(
                    "computer task %s delivery failed; retried next pass", row.id)
            else:
                # The handoff landed (`on_terminal` stamped `delivered_at`):
                # the journal's duty is done — an unconsumed entry left
                # here would linger until the next process's persist pass.
                if pending is not None:
                    self._executors.clear_pending(row.id)

    async def _dispatch_scope(self, scope: str) -> int:
        stream = _stream(scope)
        await self._group(stream)
        seen: list[tuple[Any, Mapping[str, Any]]] = []
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
            for _, items in fresh or []:
                seen.extend((mid, fields) for mid, fields in items)
            seen.extend(pending)
        except Exception as exc:
            # NOGROUP means the cached "group exists" went stale (a flush or
            # a trimmed stream dropped it): re-create next pass rather than
            # stay broken behind the cache. The table-side maintenance
            # below still runs — its authority is the row, not the stream.
            if "NOGROUP" in str(exc):
                self._group_ready.discard(stream)
            log.exception("computer queue read failed")
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
        """Ack the pointer, then bound the stream: the `computer_tasks` row
        is the record of truth, so a trimmed pointer loses nothing — an
        unannounced `queued` row is republished by reconcile, an acked one
        needs no entry at all. `MAXLEN ~` trims whole nodes: cheap and
        approximate is the right bound here."""
        with contextlib.suppress(Exception):
            await self._redis.xack(stream, GROUP, mid)
        with contextlib.suppress(Exception):
            await self._redis.xtrim(stream, maxlen=STREAM_MAX, approximate=True)

    async def _settle(self, stream: str, mid: Any, row: ComputerTask) -> None:
        """Terminal delivery THEN ack: the row stands first, then the room
        is told, then the pointer is dropped. A delivery failure leaves the
        entry pending — reclaimed and retried, never silently dropped. A
        row that is not terminal (its `finish` write could not persist) is
        neither delivered nor acked: the pending pointer drives the later
        pass that reconciles it."""
        if row.status not in TERMINAL:
            return
        pending = self._executors.pending_result(row.id)
        if pending is not None:
            # Any retained entry on a terminal row converges: `consumed`
            # decides whether the stamp is owed inside `_persist_pending`;
            # the journal is then cleared either way — the row is the
            # authority, `undelivered()` drives the one deferred handoff.
            if not await _persist_pending(
                    executors=self._executors, tasks=self._tasks,
                    clock=self._clock, task_id=row.id):
                return
            row = await self._tasks.get(row.id) or row
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
                reply = dict(await _lifecycle_work(self._lifecycle, task, executor(task)))
            except asyncio.CancelledError:
                reply = {"status": "cancelled",
                         "note": "The computer task was stopped while it ran."}
                # `cancelled` only on an observed-quiet guest — the same
                # rule as the submitter side: a stopped awaiter is not a
                # stopped guest command.
                await self._finish_guarded(task, "cancelled", reply)
                raise
            except Exception as exc:
                log.exception("computer task %s dispatch failed", task.id)
                reply = {"status": "unavailable", "reason": type(exc).__name__}
                await self._finish_guarded(task, "unavailable", reply)
            else:
                await self._finish_guarded(task, row_status(reply), reply)
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
    ) -> bool:
        return await _finish_guarded(
            executors=self._executors, reconcile=self._reconcile_scope,
            finish=self._safe_finish, task=task, status=status,
            reply=reply)

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
        job = normalise_compute_job(ComputeJob(
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
        ))
        # Election AND filing in one durable step: `elect_delivery` stamps
        # `delivered_at` and writes the outbox row in the same transaction,
        # so a live `delivered()` stamp landing in this window wins the
        # election and the job is never created — the single-disposition
        # rule `delivered_at` alone could not make atomic.
        await tasks.elect_delivery(row.id, now=clock(), job=job)
        return
    # `delivered_at`, whether or not a room was named: the row's terminal
    # state was dispositioned, so a second `on_terminal` call cannot file a
    # duplicate delivery.
    await tasks.elect_delivery(row.id, now=clock())
