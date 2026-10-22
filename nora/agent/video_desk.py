"""The video desk: what `video_create` calls.

It is the one place a clip is started from a turn, so the two rules that guard the spend live
once, in code, BEFORE any provider call:

- **The daily cap, per room.** `max_per_room_per_day` clips per room (platform plus
  conversation id) per local day (the deployment's timezone), counted from `compute_jobs`
  itself (`ComputeJobStore.kind_since`), so a restart never resets it. A clip the
  provider failed costs nothing and does not count
  (`nora.domain.video.clip_counts`). Checked and launched under one lock (one lock for all
  rooms: a per-room count under it is still race-free), so two calls racing cannot both pass
  a cap of one. A create that broke after it was sent is recorded against
  the cap as `uncertain`, since it may have rendered and a retry would spend again.
- **One clip per turn.** A model that calls again in the same turn gets the first clip back,
  never a second.

The room is never an argument: the tool closure binds `platform` and `conversation_id` from the
turn, and the row it writes is delivered into that room by `ComputeJobPoller`.

The backend's provider vocabulary stays behind `VideoStarter.start`; this module speaks
`nora.domain.video` only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from nora.domain.compute import ComputeJob, ComputeJobState, ComputeJobStore
from nora.domain.video import VIDEO_KIND, VideoRequest, VideoStart, clip_counts

log = logging.getLogger("nora.agent.video_desk")

# ponytail: one process-wide lock held across the create call (up to its timeout), so a slow
# create delays every room's next clip; per-room locks would let two rooms race the cap.


class VideoStarter(Protocol):
    """The backend as the desk uses it (`nora.infra.video_gen.MiniMaxVideoBackend.start`)."""

    async def start(self, request: VideoRequest) -> VideoStart: ...


@dataclass(frozen=True)
class VideoOutcome:
    """`started` with the row's id; `at_cap` when this room's clips today are used
    (`used` of `cap`);
    `refused` when the provider turned the request away (`reason`); `unavailable` otherwise."""

    status: Literal["started", "at_cap", "refused", "unavailable"]
    id: str = ""
    reason: str = ""
    used: int = 0
    cap: int = 0
    repeated: bool = False


class VideoDesk:
    def __init__(
        self, *, jobs: ComputeJobStore, backend: VideoStarter, clock: Callable[[], datetime],
        tz: str, max_per_room_per_day: int,
    ) -> None:
        self._jobs = jobs
        self._backend = backend
        self._clock = clock
        self._tz = ZoneInfo(tz)
        self._cap = max_per_room_per_day
        self._lock = asyncio.Lock()
        self._turn_jobs: dict[tuple[str, str, str], str] = {}
        # A create this turn that may have reached the provider without a delivery (`uncertain`,
        # `row_not_written`): repeated, never re-sent, so the tool's second call cannot pay twice.
        self._turn_spent: dict[tuple[str, str, str], str] = {}
        # Rows whose write failed AFTER the provider accepted the clip (or after an uncertain
        # create). They are held here and counted against the cap until `_reconcile` writes them,
        # which the next create does before it reads the cap. Not durable: a restart during a
        # database outage loses them, and that residual is named in the PR, not papered over.
        self._unrecorded: list[ComputeJob] = []

    def _day_start(self) -> datetime:
        local = self._clock().astimezone(self._tz)
        return datetime.combine(local.date(), time.min, tzinfo=self._tz)

    async def used_today(self, *, platform: str, conversation_id: str) -> int:
        """This room's clips since local midnight; other rooms' rows are not counted.

        Held rows are counted too, so a failed write cannot reopen the cap in this process.
        They are read BEFORE the table: a row reconciled during the read is then counted twice,
        which fails closed, never once-too-few."""
        held = sum(
            1 for r in self._unrecorded
            if r.platform == platform and r.conversation_id == conversation_id
        )
        rows = await self._jobs.kind_since(kind=VIDEO_KIND, since=self._day_start())
        return held + sum(
            1 for r in rows
            if r.platform == platform and r.conversation_id == conversation_id
            and clip_counts(failure_reason=r.failure_reason, result_url=r.result_url)
        )

    async def _persist(self, row: ComputeJob) -> bool:
        """Write `row`. On failure it is held for `_reconcile` and False is returned: the
        caller must not report a clip as recorded that the table does not hold."""
        try:
            await self._jobs.create(row)
            return True
        except Exception:
            log.exception("video_create: the row for %s was not written; held to retry", row.id)
            self._unrecorded.append(row)
            return False

    async def _reconcile(self) -> None:
        """Write held rows, oldest first, stopping at the first that still fails. A row the
        table already holds (its write landed but the call raised) is dropped, not re-inserted."""
        while self._unrecorded:
            row = self._unrecorded[0]
            try:
                if await self._jobs.get(row.id) is None:
                    await self._jobs.create(row)
            except Exception:
                log.exception("video_create: a held row is still unwritten; retried next create")
                return
            self._unrecorded.pop(0)

    async def _record_uncertain(
        self, *, platform: str, conversation_id: str, conversation_type: str,
        requester_person_id: str, tag_id: str | None, request: VideoRequest, reason: str,
    ) -> None:
        """A create whose request may have reached the provider and gave no task id: a
        terminal `failed` row with reason `uncertain`, so the cap counts it (a retry would
        spend again) and nothing ever polls or delivers it."""
        row = _row(
            request=request, payload=request.as_payload(), backend_job_id="uncertain@0",
            platform=platform, conversation_id=conversation_id,
            conversation_type=conversation_type, requester_person_id=requester_person_id,
            tag_id=tag_id, created_at=self._clock(), state=ComputeJobState.FAILED,
            error=reason or "the create call broke after it was sent",
            failure_reason="uncertain",
        )
        await self._persist(row)

    async def create(
        self, *, platform: str, conversation_id: str, conversation_type: str,
        requester_person_id: str, tag_id: str | None, request: VideoRequest,
    ) -> VideoOutcome:
        """Check the cap, start the clip, write the row. Never raises; returns at once."""
        key = (tag_id or "", platform, conversation_id)
        prepare = getattr(self._backend, "prepare", None)
        repeat = bool(tag_id) and (key in self._turn_jobs or key in self._turn_spent)
        to_fit = any(r.kind != "image" for r in request.references)
        if prepare is not None and not repeat and to_fit:
            # Fitting a clip or a sound spends no quota but takes seconds of encoding, so it runs
            # before the lock that every room's create waits on. A repeat in this turn skips it:
            # the lock below answers it from memory. A room already at its cap is answered before
            # any encode, so the cap bounds the work as well as the spend; the count is read
            # again under the lock, which stays the rule.
            try:
                used = await self.used_today(platform=platform, conversation_id=conversation_id)
            except Exception:
                log.exception("video_create: today's clip count could not be read")
                return VideoOutcome("unavailable", reason="cap_unreadable")
            if used >= self._cap:
                return VideoOutcome("at_cap", used=used, cap=self._cap)
            try:
                prepared = await prepare(request)
            except Exception:
                log.exception("video_create: preparing the references raised")
                return VideoOutcome("unavailable", reason="backend_raised")
            if isinstance(prepared, VideoStart):
                return VideoOutcome(
                    "refused" if prepared.status == "refused" else "unavailable",
                    reason=prepared.reason)
            request = prepared
        async with self._lock:
            if tag_id and key in self._turn_jobs:
                return VideoOutcome("started", id=self._turn_jobs[key], repeated=True)
            if tag_id and key in self._turn_spent:
                return VideoOutcome("unavailable", reason=self._turn_spent[key])
            await self._reconcile()
            try:
                used = await self.used_today(platform=platform, conversation_id=conversation_id)
            except Exception:
                # Fail closed: a cap that cannot be read is not a cap that passed.
                log.exception("video_create: today's clip count could not be read")
                return VideoOutcome("unavailable", reason="cap_unreadable")
            if used >= self._cap:
                log.info(
                    "video_create refused: %d of %d clips used today in this room", used, self._cap)
                return VideoOutcome("at_cap", used=used, cap=self._cap)
            try:
                started = await self._backend.start(request)
            except Exception:
                log.exception("video_create: the backend raised")
                return VideoOutcome("unavailable", reason="backend_raised")
            if started.status != "started":
                if started.uncertain:
                    await self._record_uncertain(
                        platform=platform, conversation_id=conversation_id,
                        conversation_type=conversation_type,
                        requester_person_id=requester_person_id, tag_id=tag_id,
                        request=request, reason=started.reason,
                    )
                    self._remember(self._turn_spent, key, "uncertain", tag_id)
                    return VideoOutcome("unavailable", reason="uncertain")
                return VideoOutcome(started.status, reason=started.reason)
            row = _row(
                request=request, payload={**request.as_payload(), **started.params},
                backend_job_id=started.job_id, platform=platform,
                conversation_id=conversation_id, conversation_type=conversation_type,
                requester_person_id=requester_person_id, tag_id=tag_id,
                created_at=self._clock(), state=ComputeJobState.PENDING,
            )
            if not await self._persist(row):
                # The clip is rendering and nothing will deliver it, unless the held row is
                # written (`_reconcile`). Said plainly: there is no cancel call to stop the spend.
                log.error("video_create: task %s renders undelivered until its row is written",
                          started.job_id)
                self._remember(self._turn_spent, key, "row_not_written", tag_id)
                return VideoOutcome("unavailable", reason="row_not_written")
            self._remember(self._turn_jobs, key, str(row.id), tag_id)
            return VideoOutcome("started", id=str(row.id), used=used + 1, cap=self._cap)

    @staticmethod
    def _remember(
        turns: dict[tuple[str, str, str], str], key: tuple[str, str, str], value: str,
        tag_id: str | None,
    ) -> None:
        """Keep `value` for this turn, the oldest of 256 turns dropped first."""
        if not tag_id:
            return
        if len(turns) >= 256:
            turns.pop(next(iter(turns)))
        turns[key] = value


def _row(
    *, request: VideoRequest, payload: dict[str, Any], backend_job_id: str, platform: str,
    conversation_id: str, conversation_type: str, requester_person_id: str,
    tag_id: str | None, created_at: datetime, state: ComputeJobState,
    error: str | None = None, failure_reason: str | None = None,
) -> ComputeJob:
    return ComputeJob(
        id=uuid4(), backend_job_id=backend_job_id, task=request.prompt,
        artifact_id=None, platform=platform, conversation_id=conversation_id,
        conversation_type=conversation_type, requester_person_id=requester_person_id,
        created_at=created_at, state=state,
        completed_at=created_at if state is not ComputeJobState.PENDING else None,
        result_url=None, result_summary=None, error=error,
        task_sha256=_sha(request.prompt),
        context_sha256=_sha(json.dumps(payload, sort_keys=True, ensure_ascii=False)),
        kind=VIDEO_KIND, tag_id=tag_id, payload=payload, failure_reason=failure_reason,
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
