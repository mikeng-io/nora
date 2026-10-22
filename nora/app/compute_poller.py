"""`ComputeJobPoller` — delivers a finished compute job through `FirePath.speak`
(DEC-97), rather than `HeartbeatLoop`'s path: there is no `due_at` to compare
against, every `PENDING` row is a candidate every tick, and "is it done" is
answered by `ComputeBackend.poll`, not a clock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from nora.app.fire import FAILED_POLICY, SENT, compute_occasion
from nora.domain.checkpoint import read_row
from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    ComputeJobStore,
    DocumentReadBack,
    ReadBackDocument,
)
from nora.domain.events import Event
from nora.domain.media import MediaBlob, MediaObject
from nora.domain.ports import (
    BlobStore,
    ComputeBackend,
    JobResult,
    JobStatus,
    MediaObjectStore,
)
from nora.domain.subagent import job_title
from nora.engagement.slots import ConversationSlots, Key
from nora.language.checkpoint_render import render_checkpoint
from nora.obs.metrics import Metrics
from nora.policy import Policy

log = logging.getLogger("nora.app.compute_poller")

BATCH_LIMIT = 50

# `nora_compute_jobs_delivered_total{outcome}`'s one value that is not one of
# `speak()`'s own outcomes: this module's own decision. A tag holds the
# conversation, so nothing was said and NOTHING WAS CLAIMED: the row stays
# `PENDING` and the next tick tries again. It is the one outcome here that is
# not terminal, hence the counter: without one, a slot that never frees looks
# identical on every dashboard to a job that was never finished.
DEFERRED = "deferred"


class DocumentReader(Protocol):
    """The read `DocumentService.read` already is: one document, as its room sees it and as
    `person_id` may read it."""

    async def read(
        self, *, platform: str, conversation_id: str, document_id: UUID | None = None,
        handle: str = "", person_id: str | None = None,
    ) -> Any: ...


@runtime_checkable
class ResultFetcher(Protocol):
    """A backend whose finished result needs one more fetch before delivery (the video
    backend: the clip's bytes). Called only while the room's slot is held, so a deferred
    delivery never fetches; RUNNING means "not yet, ask again next tick"."""

    async def fetch_result(self, *, job_id: str, result: JobResult) -> JobResult: ...


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
        policy: Policy,
        documents: DocumentReader | None = None,
        blobs: BlobStore | None = None,
        media: MediaObjectStore | None = None,
    ) -> None:
        self._store = store
        # The grant is checked again at delivery, not only at launch: a job can finish long
        # after its room or its requester was removed from policy (8765_44864).
        self._policy = policy
        # Nora's own storage for a result that carries its artifact's bytes (a video clip):
        # the blob store holds the bytes, the media index the row `media_search` finds.
        self._blobs = blobs
        self._media = media
        # kind -> the backend that owns rows of that kind. A row whose kind has no entry is
        # left alone while the process runs (logged once), never failed or delivered. Boot
        # recovery (`recover`) fails every pending row whose kind's handle lives in memory (the
        # sub-agent worker); a kind named in its `durable_kinds` (video: the handle is the
        # provider's task id) stays pending and is polled again after a restart.
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
                    document_id=UUID(raw), person_id=job.requester_person_id,
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

    async def _keep(self, job: ComputeJob, result: JobResult) -> MediaBlob | None:
        """Keep a finished artifact's bytes in Nora's own storage, the generated-image library
        shape (DEC-138): the bytes in the blob store, a `generated` row in the media index whose
        description is the prompt, so `media_search` / `media_send` find and resend it after the
        provider's link has expired. A failure costs the library entry, never the delivery: the
        clip is still attached (as `generate_image` does), and it is counted and logged."""
        data = result.media
        if result.status is not JobStatus.SUCCEEDED or not data:
            return None
        media_type = result.media_type or "video/mp4"
        payload = job.payload or {}
        prompt = str(payload.get("prompt") or job.task)
        title = prompt[:80] or "generated video"
        sha256 = ""
        saved = False
        try:
            if self._blobs is not None and self._media is not None:
                sha256 = await self._blobs.put(data=data)
                saved = await self._media.save(obj=MediaObject(
                    sha256=sha256, media_type=media_type, size=len(data), source="generated",
                    description=prompt, title=title,
                    provenance={
                        "prompt": prompt, "title": title, "description": prompt,
                        "model": str(payload.get("model") or ""),
                        "kind": job.kind, "job_id": str(job.id),
                        "backend_job_id": job.backend_job_id,
                        "resolution": payload.get("resolution"),
                        "duration": payload.get("duration"), "ratio": payload.get("ratio"),
                        "platform": job.platform, "conversation_id": job.conversation_id,
                    },
                    created_at_ms=int(self._clock().timestamp() * 1000),
                ))
        except Exception:
            log.exception("compute job %s: its result could not be kept", job.id)
            saved = False
        if not saved:
            log.warning(
                "compute job %s: the result is attached but NOT kept in the library "
                "(blob store or media index %s)", job.id,
                "not wired" if self._blobs is None or self._media is None else "refused",
            )
            self._metrics.inc("nora_video_clip_keep_total", outcome="unstored")
            return None
        self._metrics.inc("nora_video_clip_keep_total", outcome="stored")
        return MediaBlob(sha256=sha256, media_type=media_type, size=len(data))

    def _occasion(
        self, job: ComputeJob, result: Any, readback: Any, kept: MediaBlob | None = None,
    ) -> Event:
        """The delivery's occasion. A job that STOPPED also carries where it had got to, from
        the one checkpoint renderer; a job that finished does not (its report has the same
        sections). The files it kept are code's, from the result, whatever the outcome."""
        stored, kept_files = read_row(result.checkpoint, None)
        text = ""
        if result.status is not JobStatus.SUCCEEDED and stored is not None:
            text = render_checkpoint(
                stored, None, title=job_title(job.payload, job.task), now=self._clock(),
                mode="delivery",
            )
        return compute_occasion(
            job, result, readback, checkpoint=text, kept_files=kept_files, kept=kept,
        )

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
            if not self._policy.is_allowed(
                platform=job.platform, conversation_id=job.conversation_id,
                person_id=job.requester_person_id,
            ):
                # The room was removed, or the requester was, after the job was launched.
                # Nothing is fetched, claimed or said: the row closes as policy-failed, and
                # the outbound adapter is never called (8765_44864).
                log.warning(
                    "compute job %s not delivered: %s is no longer allowed in %s",
                    job.id, job.requester_person_id, job.conversation_id,
                )
                if await self._store.claim(job.id):
                    await self._store.mark(
                        job.id, ComputeJobState.FAILED, completed_at=self._clock(),
                        error="the room or requester is no longer allowed",
                        failure_reason=FAILED_POLICY,
                    )
                self._metrics.inc("nora_compute_jobs_delivered_total", outcome=FAILED_POLICY)
                return
            if result.status is JobStatus.SUCCEEDED and isinstance(backend, ResultFetcher):
                result = await backend.fetch_result(job_id=job.backend_job_id, result=result)
                if result.status is JobStatus.RUNNING:
                    # Not fetched yet: nothing claimed, nothing said; the next tick asks again.
                    return
            if not await self._store.claim(job.id):
                # Already claimed by an earlier tick's overlapping poll.
                return
            readback = (
                await self._read_back(job, result.document_ids)
                if self._documents is not None else None
            )
            # ponytail: the media index's metric is video's, the one kind that carries bytes
            # today; a second such kind names its own.
            kept = await self._keep(job, result)
            outcome = await self._speak(self._occasion(job, result, readback, kept))
            # `SENT`, never the literal `"sent"`: the string decides the ROW's
            # terminal state, so a typo would record a delivery that never
            # happened — the reason `nora.app.fire` owns these four names
            # (`fire.SENT`'s own comment).
            state = ComputeJobState.DONE if outcome == SENT else ComputeJobState.FAILED
            await self._store.mark(
                job.id, state, completed_at=self._clock(),
                result_url=result.url, result_summary=result.summary,
                error=result.error, failure_reason=result.failure_reason,
                result_report=result.report,
                result_document_ids=result.document_ids or None,
                trace_id=result.trace_id,
                checkpoint=result.checkpoint, checkpoint_at=result.checkpoint_at,
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
    durable_kinds: frozenset[str] = frozenset(),
) -> list[ComputeJob]:
    """Boot recovery. BOTH non-terminal states resolve to `FAILED`; the
    `PENDING` half has no counterpart in `nora.app.heartbeat.recover`.

    `FIRING` is the crash-mid-delivery case the heartbeat has too.

    `PENDING`: a pending compute job is a HANDLE (`backend_job_id`) into
    `ComputeBackend`'s registry, and an in-process backend keeps that registry
    in memory, so ANY restart, not just a crash, orphans every surviving
    `PENDING` row. Left alone, the next tick would poll a handle nothing
    recognises, get `FAILED("unknown job_id")` back, claim the row and tell the
    group their job failed, which nobody knows: the container may have
    finished and registered its artifact perfectly well.

    Sweeping at boot records the one thing that IS known: the delivery was
    lost across a restart. The orphaned container is not addressed here and
    cannot be: this process has no handle to it either.

    **The rows are RETURNED so somebody can still be told.** The database
    must not claim the job failed (nobody knows), but the person who asked was
    told «I'm working on it» and must not then hear nothing: a promise made and
    never closed is R-5's own failure shape. What is spoken is an
    `unavailable`, never a `failed`: «I lost track of it, ask me again», the
    one statement that is true. See `nora.app.fire.lost_job_occasion`.

    `durable_kinds` are the exception to the `PENDING` half: a kind whose handle is NOT in
    memory (a video job's is the provider's task id, queryable for days) loses nothing across
    a restart, so its pending rows stay pending and the next tick polls them. `FIRING` is
    swept whatever the kind: a crash mid-delivery is at-most-once for every kind.

    Returns the swept rows, both states together.
    """
    firing = await store.sweep_firing(now=now)
    if firing:
        log.warning(
            "boot recovery: %d compute job(s) were left FIRING and are now FAILED",
            len(firing),
        )
    pending = await store.sweep_pending(now=now, keep_kinds=durable_kinds)
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
