"""`SubagentBackend`, the in-process `ComputeBackend` for the sub-agent worker.

`launch` starts an asyncio task that runs `SubagentRunner` and returns an id at once; the task
is the worker. `poll` answers from the job's in-memory state; `cancel` cancels the task and the
job ends `cancelled`. The registry is in memory, so a restart loses a running job and
`compute_poller.recover` fails its row at boot and the room is told. A job is not resumed: that
would mean saving the worker's conversation.

`launch` NEVER raises (the port's rule): a brief that is not a `SubagentBrief`, or a requester
who is not on file, is a job that is already `FAILED`, reported by `poll`.

Progress (rounds, last tool, the trace id) is held per running job in memory, for the
model-facing read tool; reading Langfuse at runtime is not a dependency Nora takes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nora.agent.subagent_progress import SubagentProgress
from nora.app.subagent_runner import REASON_TEXT, SubagentOutcome, SubagentRunner
from nora.domain.checkpoint import Checkpoint
from nora.domain.compute import ComputeJobStore
from nora.domain.ports import JobResult, JobStatus, LaunchRequest
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.subagent_backend")

#: How long a finished job stays in memory. The poller reads a result on its next tick and
#: writes it to the row, which is what `subagent_read` serves afterwards, so this only has to
#: outlast a few ticks.
FINISHED_TTL_SECONDS = 3600.0


@dataclass
class _Job:
    id: str
    request: LaunchRequest
    started_at: datetime
    progress: SubagentProgress = field(default_factory=SubagentProgress)
    task: asyncio.Task[None] | None = None
    outcome: SubagentOutcome | None = None
    cancelled: bool = False
    finished_at: datetime | None = None


@dataclass(frozen=True)
class SubagentStatus:
    """What `subagent_read` shows of a running job: from memory, never from the worker."""

    job_id: str
    running: bool
    started_at: datetime
    rounds: int
    last_tool: str | None
    trace_id: str | None
    # The worker's last checkpoint and what code recorded, from memory:
    # fresher than the row, which a failed or not-yet-possible write can lag.
    checkpoint: Checkpoint | None = None
    document_ids: tuple[str, ...] = ()
    kept_files: tuple[str, ...] = ()


class SubagentBackend:
    def __init__(
        self, *, runner: SubagentRunner, metrics: Metrics, clock: Callable[[], datetime],
        requester_name: Callable[[str], str], jobs: ComputeJobStore | None = None,
    ) -> None:
        self._store = jobs
        self._runner = runner
        self._metrics = metrics
        self._clock = clock
        self._requester_name = requester_name
        self._jobs: dict[str, _Job] = {}

    async def launch(self, *, request: LaunchRequest) -> str:
        self._prune()
        job_id = uuid.uuid4().hex
        job = _Job(
            id=job_id, request=request, started_at=self._clock(),
            progress=SubagentProgress(
                persist=lambda payload, at: self._persist(job_id, payload, at),
            ),
        )
        self._jobs[job.id] = job
        job.task = asyncio.create_task(self._work(job), name=f"subagent-{job.id}")
        return job.id

    async def _persist(self, backend_job_id: str, payload: Mapping[str, Any], at: datetime) -> None:
        """Write a job's checkpoint to its row. The row is written after `launch` returns, so
        an update that comes first finds none (False) and stays in memory until the next."""
        if self._store is not None and not await self._store.set_checkpoint(
            backend_job_id, payload, at,
        ):
            # no pending row yet (the row is written after launch) or it has moved on: the
            # checkpoint stays in memory and the terminal `mark` writes the final one
            log.info("sub-agent %s: checkpoint not written, no pending row", backend_job_id)
            self._metrics.inc("nora_subagent_checkpoint_unwritten_total")

    async def _work(self, job: _Job) -> None:
        try:
            job.outcome = await self._runner.run(
                job_id=job.id, request=job.request,
                requester_name=self._requester_name(job.request.requester_person_id),
                progress=job.progress,
            )
        except asyncio.CancelledError:
            self._mark_cancelled(job)
            raise
        except Exception:
            log.exception("sub-agent %s raised outside its run", job.id)
            job.outcome = SubagentOutcome(
                document_ids=job.progress.document_ids, failure_reason="error",
            )
        job.finished_at = self._clock()
        self._metrics.inc(
            "nora_subagent_jobs_total", outcome=job.outcome.failure_reason or "done",
        )

    def _prune(self) -> None:
        """Drop jobs that finished more than `FINISHED_TTL_SECONDS` ago (the row keeps them)."""
        now = self._clock()
        for job_id in [
            j.id for j in self._jobs.values()
            if j.finished_at is not None
            and (now - j.finished_at).total_seconds() > FINISHED_TTL_SECONDS
        ]:
            del self._jobs[job_id]

    def _mark_cancelled(self, job: _Job) -> None:
        """Once per job, and also for a task cancelled before its first step, whose body never
        ran to see the cancellation."""
        if job.cancelled or job.outcome is not None:
            return
        job.cancelled = True
        job.finished_at = self._clock()
        self._metrics.inc("nora_subagent_jobs_total", outcome="cancelled")

    async def poll(self, *, job_id: str) -> JobResult:
        self._prune()
        job = self._jobs.get(job_id)
        if job is None:
            return JobResult(
                status=JobStatus.FAILED, error="unknown job_id", failure_reason="lost",
            )
        if job.cancelled:
            return JobResult(
                status=JobStatus.FAILED, error="it was cancelled", failure_reason="cancelled",
                document_ids=job.progress.document_ids, trace_id=job.progress.trace_id,
            )
        out = job.outcome
        if out is None:
            return JobResult(status=JobStatus.RUNNING)
        if out.failure_reason is not None:
            return JobResult(
                status=JobStatus.FAILED, error=out.error or REASON_TEXT["error"],
                failure_reason=out.failure_reason, document_ids=out.document_ids,
                trace_id=job.progress.trace_id, checkpoint=job.progress.checkpoint_payload(),
                checkpoint_at=job.progress.stamp(),
            )
        return JobResult(
            status=JobStatus.SUCCEEDED, summary=out.report, report=out.report,
            document_ids=out.document_ids, trace_id=job.progress.trace_id,
            checkpoint=job.progress.checkpoint_payload(),
            checkpoint_at=job.progress.stamp(),
        )

    async def cancel(self, *, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.task is None or job.task.done():
            return
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        self._mark_cancelled(job)

    def status(self, job_id: str) -> SubagentStatus | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return SubagentStatus(
            job_id=job.id, running=job.outcome is None and not job.cancelled,
            started_at=job.started_at, rounds=job.progress.rounds,
            last_tool=job.progress.last_tool, trace_id=job.progress.trace_id,
            checkpoint=job.progress.checkpoint, document_ids=job.progress.document_ids,
            kept_files=job.progress.kept_files,
        )

    async def shutdown(self) -> None:
        """Cancel every job still running (a process stop). Their rows are failed by the next
        boot's recovery."""
        tasks = [j.task for j in self._jobs.values() if j.task is not None and not j.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for job in self._jobs.values():
            self._mark_cancelled(job)
