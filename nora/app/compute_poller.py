"""`ComputeJobPoller` — delivers a finished compute job (Nora Compute spec
§4) through `FirePath.speak`, mirroring `VersionAnnouncer`'s shape (DEC-97)
rather than `HeartbeatLoop`'s: there is no `due_at` to compare against,
every `PENDING` row is a candidate every tick, and "is it done" is answered
by `ComputeBackend.poll`, not a clock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from nora.app.fire import SENT, compute_occasion
from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    ComputeJobStore,
    DocumentReadBack,
    ReadBackDocument,
)
from nora.domain.events import Event
from nora.domain.ports import ComputeBackend, JobStatus
from nora.engagement.slots import ConversationSlots, Key
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.compute_poller")

BATCH_LIMIT = 50

# `nora_compute_jobs_delivered_total{outcome}`'s one value that is not one of
# `speak()`'s own §8.1 outcomes — this module's own decision, exactly as
# `nora.app.announce.DEFERRED` is that module's. A tag holds the conversation,
# so nothing was said and NOTHING WAS CLAIMED: the row stays `PENDING` and the
# next tick tries again. That makes it the one outcome here that is not
# terminal, and the reason it needs a counter at all — without one, a slot
# that never frees looks identical on every dashboard to a job that was never
# finished.
DEFERRED = "deferred"


class DocumentReader(Protocol):
    """The read `DocumentService.read` already is: one document, as its room sees it."""

    async def read(
        self, *, platform: str, conversation_id: str, document_id: UUID | None = None,
        handle: str = "",
    ) -> Any: ...


class ComputeJobPoller:
    def __init__(
        self,
        *,
        store: ComputeJobStore,
        backends: Mapping[str, ComputeBackend],
        speak: Callable[[Event], Awaitable[str]],
        slots: ConversationSlots,
        metrics: Metrics,
        tick_seconds: float,
        clock: Callable[[], datetime],
        documents: DocumentReader | None = None,
    ) -> None:
        self._store = store
        # kind -> the backend that owns rows of that kind. A row whose kind has no entry is
        # left alone (logged once): another deployment's or a later build's worker may own it.
        self._backends = dict(backends)
        self._documents = documents
        self._unowned: set[UUID] = set()
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

    async def _read_back(self, job: ComputeJob, ids: tuple[str, ...]) -> DocumentReadBack:
        """Read each `document_id` the result named back through the document store, in the
        job's own room. An id that is not a document here, or whose read raised, is missing:
        fail closed, never linked (R-100)."""
        found: list[ReadBackDocument] = []
        missing: list[str] = []
        for raw in ids:
            try:
                out = await self._documents.read(  # type: ignore[union-attr]
                    platform=job.platform, conversation_id=job.conversation_id,
                    document_id=UUID(raw),
                )
                ok = out.status == "ok"
            except Exception:
                log.exception("compute job %s: document %s did not read back", job.id, raw)
                ok = False
            if ok:
                found.append(ReadBackDocument(raw, out.title, out.url))
            else:
                missing.append(raw)
        return DocumentReadBack(tuple(found), tuple(missing))

    async def _maybe_deliver(self, job: ComputeJob) -> None:
        backend = self._backends.get(job.kind)
        if backend is None:
            if job.id not in self._unowned:
                self._unowned.add(job.id)
                log.warning(
                    "compute job %s has kind %r and no backend is wired for it; left pending",
                    job.id, job.kind,
                )
            return
        result = await backend.poll(job_id=job.backend_job_id)
        if result.status is JobStatus.RUNNING:
            return

        slot: Key = (job.platform, job.conversation_id)
        if not self._slots.try_acquire(slot):
            log.info("compute job %s deferred: slot held", job.id)
            self._metrics.inc("nora_compute_jobs_delivered_total", outcome=DEFERRED)
            return
        try:
            if not await self._store.claim(job.id):
                # Already claimed by an earlier tick's overlapping poll.
                return
            readback = (
                await self._read_back(job, result.document_ids)
                if self._documents is not None else None
            )
            outcome = await self._speak(compute_occasion(job, result, readback))
            # `SENT`, never the literal `"sent"`: the string decides the ROW's
            # terminal state, so a typo would record a delivery that never
            # happened — the reason `nora.app.fire` owns these four names
            # (`fire.SENT`'s own comment) and `nora.app.announce` reads them
            # from there too.
            state = ComputeJobState.DONE if outcome == SENT else ComputeJobState.FAILED
            await self._store.mark(
                job.id, state, completed_at=self._clock(),
                result_url=result.url, result_summary=result.summary,
                error=result.error, failure_reason=result.failure_reason,
                result_report=result.report,
                result_document_ids=result.document_ids or None,
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


async def recover(
    *, store: ComputeJobStore, metrics: Metrics, now: datetime,
) -> list[ComputeJob]:
    """Boot recovery. BOTH non-terminal states resolve to `FAILED`, and the
    second half is where this stops mirroring `nora.app.heartbeat.recover`.

    `FIRING` is the crash-mid-delivery case the heartbeat has too.

    `PENDING` has no heartbeat counterpart, and leaving it un-swept was a real
    defect rather than a theoretical one. A pending compute job is a HANDLE
    (`backend_job_id`) into `ComputeBackend`'s registry, and
    an in-process backend keeps that registry in memory (the one that
    shipped did) — so ANY restart, not
    just a crash, orphans every surviving `PENDING` row. The next tick polls a
    handle nothing recognises, gets `FAILED("unknown job_id")` back, claims the
    row and delivers it: Nora tells the group, in her own voice, that their job
    failed. Nobody knows that. The container may have finished and registered
    its artifact perfectly well.

    Sweeping at boot replaces that with the one thing that IS known — the
    delivery was lost across a restart. The orphaned container is not
    addressed here and cannot be: this process has no handle to it either.
    That is a known cost of the in-memory registry, not something this
    function hides.

    **The rows are RETURNED so somebody can still be told** (operator, live,
    2026-08-14). Making them terminal here is right, and NOT claiming the job
    failed is right — the container may have finished perfectly well and
    nobody knows. But "never spoken at all" was a third thing, and it was
    wrong: the person who asked was told «I'm working on it» and then heard
    nothing, ever. A promise made and never closed is R-5's own failure shape,
    and it was on the honest side of the ledger only because the DATABASE was
    honest; the human was not told anything.

    What is spoken is therefore an `unavailable`, never a `failed`: «I lost
    track of it, ask me again» — which is the one statement that is true. See
    `nora.app.fire.lost_job_occasion`.

    Returns the swept rows, both states together.
    """
    firing = await store.sweep_firing(now=now)
    if firing:
        log.warning(
            "boot recovery: %d compute job(s) were left FIRING and are now FAILED",
            len(firing),
        )
    pending = await store.sweep_pending(now=now)
    if pending:
        log.warning(
            "boot recovery: %d compute job(s) were still PENDING and their backend "
            "handles did not survive the restart; they are now FAILED and each "
            "requester will be told she lost track of it (any container still "
            "running is orphaned)",
            len(pending),
        )
    swept = [*firing, *pending]
    if swept:
        metrics.inc(
            "nora_compute_jobs_delivered_total", len(swept), outcome="failed_error",
        )
    return swept
