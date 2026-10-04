"""`SubagentBackend` — the in-process `ComputeBackend` for the sub-agent worker
(design/subagent §4, §6; the shape the retired per-job container backend had, minus the
container).

`launch` starts an asyncio task that runs `SubagentRunner` and returns an id at once; the task
is the worker. `poll` answers from the job's in-memory state; `cancel` cancels the task and the
job ends `cancelled`. The registry is in memory, so a restart loses a running job and
`compute_poller.recover` fails its row at boot and the room is told (design §6: resuming would
mean saving the worker's conversation, a later step).

`launch` NEVER raises (the port's rule): a brief that is not a `SubagentBrief`, or a requester
who is not on file, is a job that is already `FAILED`, reported by `poll`.

Progress (rounds, last tool, the trace id) is held per running job in memory, for the model-
facing read tool of step 3; reading Langfuse at runtime is not a dependency Nora takes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from nora.agent.subagent_progress import SubagentProgress
from nora.app.subagent_runner import REASON_TEXT, SubagentOutcome, SubagentRunner
from nora.domain.ports import JobResult, JobStatus, LaunchRequest
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.subagent_backend")


@dataclass
class _Job:
    id: str
    request: LaunchRequest
    started_at: datetime
    progress: SubagentProgress = field(default_factory=SubagentProgress)
    task: asyncio.Task[None] | None = None
    outcome: SubagentOutcome | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class SubagentStatus:
    """What `subagent_read` shows of a running job: from memory, never from the worker."""

    job_id: str
    running: bool
    started_at: datetime
    rounds: int
    last_tool: str | None
    trace_id: str | None


class SubagentBackend:
    def __init__(
        self, *, runner: SubagentRunner, metrics: Metrics, clock: Callable[[], datetime],
        requester_name: Callable[[str], str],
    ) -> None:
        self._runner = runner
        self._metrics = metrics
        self._clock = clock
        self._requester_name = requester_name
        self._jobs: dict[str, _Job] = {}

    async def launch(self, *, request: LaunchRequest) -> str:
        job = _Job(id=uuid.uuid4().hex, request=request, started_at=self._clock())
        self._jobs[job.id] = job
        job.task = asyncio.create_task(self._work(job), name=f"subagent-{job.id}")
        return job.id

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
        self._metrics.inc(
            "nora_subagent_jobs_total", outcome=job.outcome.failure_reason or "done",
        )

    def _mark_cancelled(self, job: _Job) -> None:
        """Once per job, and also for a task cancelled before its first step, whose body never
        ran to see the cancellation."""
        if job.cancelled or job.outcome is not None:
            return
        job.cancelled = True
        self._metrics.inc("nora_subagent_jobs_total", outcome="cancelled")

    async def poll(self, *, job_id: str) -> JobResult:
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
                trace_id=job.progress.trace_id,
            )
        return JobResult(
            status=JobStatus.SUCCEEDED, summary=out.report, report=out.report,
            document_ids=out.document_ids, trace_id=job.progress.trace_id,
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
