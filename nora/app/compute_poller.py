"""`ComputeJobPoller` — delivers a finished compute job (Nora Compute spec
§4) through `FirePath.speak`, mirroring `VersionAnnouncer`'s shape (DEC-97)
rather than `HeartbeatLoop`'s: there is no `due_at` to compare against,
every `PENDING` row is a candidate every tick, and "is it done" is answered
by `ComputeBackend.poll`, not a clock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from nora.app.fire import compute_occasion
from nora.domain.compute import ComputeJob, ComputeJobState, ComputeJobStore
from nora.domain.ports import ComputeBackend, JobStatus
from nora.engagement.slots import ConversationSlots, Key
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.compute_poller")

BATCH_LIMIT = 50


class ComputeJobPoller:
    def __init__(
        self,
        *,
        store: ComputeJobStore,
        backend: ComputeBackend,
        speak: Callable[[Any], Awaitable[str]],
        slots: ConversationSlots,
        metrics: Metrics,
        tick_seconds: float,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._backend = backend
        self._speak = speak
        self._slots = slots
        self._metrics = metrics
        self._tick_seconds = tick_seconds
        self._clock = clock

    async def tick(self) -> None:
        pending = await self._store.pending(limit=BATCH_LIMIT)
        for job in pending:
            try:
                await self._maybe_deliver(job)
            except Exception:
                log.exception(
                    "compute job %s failed while polling/delivering; the rest "
                    "of the tick continues", job.id,
                )
                self._metrics.inc("nora_errors_total", stage="compute_poller")

    async def _maybe_deliver(self, job: ComputeJob) -> None:
        result = await self._backend.poll(job_id=job.backend_job_id)
        if result.status is JobStatus.RUNNING:
            return

        slot: Key = (job.platform, job.conversation_id)
        if not self._slots.try_acquire(slot):
            log.info("compute job %s deferred: slot held", job.id)
            return
        try:
            if not await self._store.claim(job.id):
                # Already claimed by an earlier tick's overlapping poll.
                return
            outcome = await self._speak(compute_occasion(job, result))
            state = ComputeJobState.DONE if outcome == "sent" else ComputeJobState.FAILED
            await self._store.mark(
                job.id, state, completed_at=self._clock(),
                result_url=result.url, result_summary=result.summary,
                error=result.error,
            )
            self._metrics.inc("nora_compute_jobs_delivered_total", outcome=outcome)
        finally:
            self._slots.release_and_dispatch(slot)

    async def run(self) -> None:
        log.info("compute job poller started: tick every %.1fs", self._tick_seconds)
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                await self.tick()
            except Exception:
                log.exception("compute job poller tick failed")
                self._metrics.inc("nora_errors_total", stage="compute_poller")


async def recover(*, store: ComputeJobStore, metrics: Metrics, now: datetime) -> int:
    """Boot recovery, mirroring `nora.app.heartbeat.recover` — `FIRING` rows
    (a crash mid-delivery) resolve to `FAILED`, never re-delivered."""
    swept = await store.sweep_firing(now=now)
    if swept:
        log.warning(
            "boot recovery: %d compute job(s) were left FIRING and are now FAILED",
            swept,
        )
        metrics.inc("nora_compute_jobs_delivered_total", swept, outcome="failed_error")
    return swept
