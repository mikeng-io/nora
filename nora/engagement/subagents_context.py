"""`SubagentsContextReader`: the `<subagents>` block's one read.

Nora must know what her sub-agents are doing and did without reading their execution
history, and not through Standing (a model's fold, which drops open plans). So the block is
read fresh from the job rows each turn by code; no model writes it.

**This room's jobs only.** `ComputeJobStore.room_jobs` is the whole filter: another room's
job is never read here, so it cannot be shown.

**Running jobs carry live progress** from the backend's in-memory status. When no worker is
attached or it does not know the job, the line says its progress is unknown; a job whose
row is still `pending` but whose worker has stopped is `ended` (the result is on its way),
never `running`.

**Never raises, and never a wrong fact.** A store that is down, or slow past `timeout`, is
logged, counted (`nora_errors_total{stage="subagents_context"}`) and read as no block. A
document title that will not read is `""`, and the renderer says so. `row.error` is never
read: it is free text a worker or a backend wrote, and only `failure_reason` (a controlled
word) reaches the block.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from nora.domain.checkpoint import read_row
from nora.domain.compute import ComputeJob, ComputeJobState, ComputeJobStore
from nora.domain.subagent import SUBAGENT_KIND, job_title
from nora.domain.subagents_context import (
    FINISHED_WINDOW,
    ROWS_READ,
    SUBAGENTS_SHOWN,
    SubagentLine,
    SubagentsView,
    SubagentWorker,
)
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.subagent.context")


class DocumentTitles(Protocol):
    async def read(
        self, *, platform: str, conversation_id: str, document_id: UUID | None = None,
        handle: str = "",
    ) -> Any: ...


class SubagentsContextReader:
    def __init__(
        self,
        *,
        jobs: ComputeJobStore,
        clock: Callable[[], datetime],
        metrics: Metrics,
        documents: DocumentTitles | None = None,
        shown: int = SUBAGENTS_SHOWN,
        rows: int = ROWS_READ,
        timeout: float = 1.0,
        title_timeout: float = 0.4,
    ) -> None:
        self._jobs = jobs
        self._clock = clock
        self._metrics = metrics
        self._documents = documents
        self._shown = shown
        self._rows = rows
        self._timeout = timeout
        self._title_timeout = title_timeout
        self._worker: SubagentWorker | None = None

    def attach(self, worker: SubagentWorker) -> None:
        """The backend, attached after the tool registry is built (as `SubagentDesk`'s is)."""
        self._worker = worker

    async def read(self, *, platform: str, conversation_id: str) -> SubagentsView | None:
        """This room's running jobs and the ones finished within `FINISHED_WINDOW`, or
        `None` (none, or the read failed)."""
        try:
            return await asyncio.wait_for(
                self._read(platform, conversation_id), timeout=self._timeout,
            )
        except Exception:
            log.exception("subagents context read failed; rendering no block")
            self._metrics.inc("nora_errors_total", stage="subagents_context", platform=platform)
            return None

    async def _read(self, platform: str, conversation_id: str) -> SubagentsView | None:
        rows = await self._jobs.room_jobs(
            platform=platform, conversation_id=conversation_id, kind=SUBAGENT_KIND,
            limit=self._rows,
        )
        # `room_jobs` is the room filter; this is the second layer, so a store that leaked
        # another room's row (a bug, a changed query) cannot put it in this room's prompt.
        rows = [r for r in rows if (r.platform, r.conversation_id) == (platform, conversation_id)]
        now = self._clock()
        live = [r for r in rows if _unfinished(r)]
        done = [
            r for r in rows
            if not _unfinished(r) and now - (r.completed_at or r.created_at) <= FINISHED_WINDOW
        ]
        if not live and not done:
            return None
        live.sort(key=lambda r: r.created_at, reverse=True)
        done.sort(key=lambda r: r.completed_at or r.created_at, reverse=True)
        ordered = live + done
        shown = ordered[: self._shown]
        lines = await asyncio.gather(*(self._line(r) for r in shown))
        return SubagentsView(
            lines=tuple(lines), now=now, omitted=len(ordered) - len(shown),
            omitted_at_least=len(rows) >= self._rows,
        )

    async def _line(self, row: ComputeJob) -> SubagentLine:
        if _unfinished(row):
            return self._running(row)
        state, delivered = _finished(row)
        return SubagentLine(
            id=str(row.id), state=state, title=job_title(row.payload, row.task),
            objective=row.task, created_at=row.created_at,
            finished_at=row.completed_at or row.created_at,
            report=(row.result_report or "").strip() or None,
            documents=await self._titles(row), failure_reason=row.failure_reason,
            delivered=delivered,
        )

    def _running(self, row: ComputeJob) -> SubagentLine:
        stored, _kept = read_row(row.checkpoint, row.checkpoint_at)
        base: dict[str, Any] = {"id": str(row.id), "objective": row.task,
                "title": job_title(row.payload, row.task), "created_at": row.created_at}
        if row.state is ComputeJobState.FIRING:
            return SubagentLine(state="delivering", **base)
        worker = self._worker
        status = None
        if worker is not None:
            try:
                status = worker.status(row.backend_job_id)
            except Exception:
                # The status read failed: nothing is known about the worker, so the job is
                # still running as far as the row says, with its progress unknown.
                log.exception("subagent status read failed for %s", row.id)
                return SubagentLine(
                    state="running", progress_known=False, checkpoint=stored, **base,
                )
        if worker is None:
            return SubagentLine(state="running", progress_known=False, checkpoint=stored, **base)
        if status is None or not status.running:
            return SubagentLine(state="ended", **base)
        return SubagentLine(
            state="running", progress_known=True, rounds=status.rounds,
            last_tool=status.last_tool,
            # memory first: it is fresher than a row whose write failed or came too early
            checkpoint=getattr(status, "checkpoint", None) or stored, **base,
        )

    async def _titles(self, row: ComputeJob) -> tuple[tuple[str, str], ...]:
        """Each document's title, read concurrently and each bounded on its own, so one slow
        or failing read costs that title (`""`, rendered «title not read» beside the id),
        never the block."""
        async def one(raw: str) -> tuple[str, str]:
            if self._documents is None:
                return raw, ""
            try:
                res = await asyncio.wait_for(
                    self._documents.read(
                        platform=row.platform, conversation_id=row.conversation_id,
                        document_id=UUID(raw),
                    ),
                    timeout=self._title_timeout,
                )
                return raw, (str(res.title) if res.status == "ok" else "")
            except Exception:
                log.exception("subagents context: document %s did not read", raw)
                return raw, ""

        return tuple(await asyncio.gather(*(one(r) for r in row.result_document_ids)))


def _unfinished(row: ComputeJob) -> bool:
    return row.state in (ComputeJobState.PENDING, ComputeJobState.FIRING)


def _finished(row: ComputeJob) -> tuple[str, bool]:
    """`(state, delivered)` for a row no longer pending. `DONE` is the poller's record that
    the delivery was sent, so a job that failed and was reported is `failed`, delivered. A
    `FAILED` row did not record a delivery; with a report and no reason it is a finished job
    whose delivery did not land."""
    if row.state is ComputeJobState.CANCELLED:
        return "cancelled", False
    delivered = row.state is ComputeJobState.DONE
    if row.failure_reason is not None:
        return "failed", delivered
    if delivered or row.result_report:
        return "done", delivered
    return "failed", delivered
