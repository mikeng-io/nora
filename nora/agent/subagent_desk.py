"""The sub-agent desk: what Nora's four `subagent_*` tools call.

It is the one place a job is launched, listed, read or cancelled FROM A TURN, so the room rule
lives once. Every method takes the room's `platform` and `conversation_id` from the turn (the
tool closure binds them; a model never names a room) and every row it returns or changes is
checked against them: a job id from another room behaves exactly as an unknown id, so its
existence is never revealed.

It holds no worker. The backend (`nora.app.subagent_backend.SubagentBackend`) is attached after
the tool registry is built, because the backend's closed tool list is built FROM the registry;
until then, and under `[subagent] enabled = false`, `create` answers `unavailable`.

A job's state as the model sees it is derived from two sources and never guessed: the outbox
row (`pending` / `firing` / `done` / `failed` / `cancelled`) and, while the row is still
`pending`, the backend's in-memory progress (rounds, last tool). A row that is `pending` whose
backend job has already ended is `finishing`: the result is on its way to the room.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from nora.domain.checkpoint import Outputs, read_row
from nora.domain.compute import ComputeJob, ComputeJobState, ComputeJobStore
from nora.domain.ports import LaunchRequest
from nora.domain.subagent import (
    SUBAGENT_KIND,
    TITLE_CHARS,
    SubagentBrief,
    job_title,
    workspace_path,
)
from nora.language.checkpoint_render import render_checkpoint

log = logging.getLogger("nora.agent.subagent_desk")

#: How many of a room's jobs `subagent_list` shows.
LIST_LIMIT = 10

#: What `subagent_list` shows of a job's description (its objective) and its report; a read
#: shows them whole (the report to `REPORT_CHARS`).
LIST_DESCRIPTION_CHARS = 120
LIST_REPORT_CHARS = 200

#: The report a read returns is the worker's own prose; cut so one read cannot flood a turn.
REPORT_CHARS = 6000


class RunStatus(Protocol):
    """What `SubagentBackend.status` answers for a job it still holds in memory."""

    @property
    def running(self) -> bool: ...

    @property
    def rounds(self) -> int: ...

    @property
    def last_tool(self) -> str | None: ...


class SubagentWorker(Protocol):
    """The backend as the desk uses it: the `ComputeBackend` verbs plus live progress."""

    async def launch(self, *, request: LaunchRequest) -> str: ...

    async def cancel(self, *, job_id: str) -> None: ...

    def status(self, job_id: str) -> RunStatus | None: ...


class DocumentTitles(Protocol):
    async def read(
        self, *, platform: str, conversation_id: str, document_id: UUID | None = None,
        handle: str = "", person_id: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class CreateOutcome:
    """`started` with the job's id, or why not: `bad_arguments` (`problem` says what),
    or `unavailable` (`reason`)."""

    status: str
    id: str = ""
    problem: str = ""
    reason: str = ""
    #: True when this turn had already started a job and `id` is that first job's.
    repeated: bool = False


@dataclass(frozen=True)
class JobView:
    """One job as `subagent_list` / `subagent_read` show it. No link of any kind: documents are
    named by id and title, and a link reaches the room only through the delivery message."""

    id: str
    state: str
    title: str
    #: The job's objective, as Nora wrote it: cut in a list, whole in a read.
    description: str
    age_seconds: int
    rounds: int | None = None
    last_tool: str | None = None
    report: str | None = None
    document_ids: tuple[str, ...] = ()
    document_titles: tuple[str, ...] = ()
    failure: str | None = None
    #: The job's checkpoint and what code recorded, rendered (`render_checkpoint`, mode `read`),
    #: on a read only; `None` for a job with neither.
    checkpoint: str | None = None


class SubagentUnavailableError(RuntimeError):
    """The desk cannot act: no worker is attached."""


class SubagentDesk:
    def __init__(
        self, *, jobs: ComputeJobStore, clock: Callable[[], datetime],
        documents: DocumentTitles | None = None,
    ) -> None:
        self._jobs = jobs
        self._clock = clock
        self._documents = documents
        self._worker: SubagentWorker | None = None
        # One job per tag turn per room: the first job a turn started, by `(tag_id, room)`. A
        # model that calls create again in the same turn gets the first job back, never a
        # duplicate. Only a created job is recorded, so a refused or failed first attempt
        # leaves the next one free to start.
        self._turn_jobs: dict[tuple[str, str, str], str] = {}
        self._create_lock = asyncio.Lock()

    def attach(self, worker: SubagentWorker) -> None:
        self._worker = worker

    @property
    def attached(self) -> bool:
        return self._worker is not None

    async def create(
        self, *, platform: str, conversation_id: str, conversation_type: str,
        requester_person_id: str, tag_id: str | None, brief: SubagentBrief,
    ) -> CreateOutcome:
        """Launch the worker, then write the row with the full brief. Returns at once; the job
        is never awaited. Bad input is refused before anything is launched."""
        problem = _problem(brief)
        if problem:
            return CreateOutcome("bad_arguments", problem=problem)
        if not tag_id:
            return await self._create(
                platform=platform, conversation_id=conversation_id,
                conversation_type=conversation_type,
                requester_person_id=requester_person_id, tag_id=tag_id, brief=brief)
        key = (tag_id, platform, conversation_id)
        async with self._create_lock:
            first = self._turn_jobs.get(key)
            if first is not None:
                return CreateOutcome("started", id=first, repeated=True)
            out = await self._create(
                platform=platform, conversation_id=conversation_id,
                conversation_type=conversation_type,
                requester_person_id=requester_person_id, tag_id=tag_id, brief=brief)
            if out.status == "started":
                if len(self._turn_jobs) >= 256:
                    self._turn_jobs.pop(next(iter(self._turn_jobs)))
                self._turn_jobs[key] = out.id
            return out

    async def _create(
        self, *, platform: str, conversation_id: str, conversation_type: str,
        requester_person_id: str, tag_id: str | None, brief: SubagentBrief,
    ) -> CreateOutcome:
        worker = self._worker
        if worker is None:
            return CreateOutcome("unavailable", reason="not_wired")
        clean = SubagentBrief(
            objective=brief.objective.strip(), title=" ".join(brief.title.split()),
            decisions=brief.decisions,
            inputs=tuple(workspace_path(p) or "" for p in brief.inputs),
            document_ids=tuple(i.strip() for i in brief.document_ids),
            deliverable=brief.deliverable.strip(),
        )
        try:
            backend_job_id = await worker.launch(request=LaunchRequest(
                task=clean.objective, platform=platform, conversation_id=conversation_id,
                conversation_type=conversation_type,
                requester_person_id=requester_person_id, tag_id=tag_id, payload=clean,
            ))
        except Exception:
            log.exception("subagent_create: the worker would not launch")
            return CreateOutcome("unavailable", reason="launch_failed")
        payload = clean.as_payload()
        row = ComputeJob(
            id=uuid4(), backend_job_id=backend_job_id, task=clean.objective,
            artifact_id=None, platform=platform, conversation_id=conversation_id,
            conversation_type=conversation_type, requester_person_id=requester_person_id,
            created_at=self._clock(), state=ComputeJobState.PENDING, completed_at=None,
            result_url=None, result_summary=None, error=None,
            task_sha256=_sha(clean.objective),
            context_sha256=_sha(json.dumps(payload, sort_keys=True, ensure_ascii=False)),
            kind=SUBAGENT_KIND, tag_id=tag_id, payload=payload,
        )
        try:
            await self._jobs.create(row)
        except Exception:
            # A worker with no row would run and never be delivered or listed: stop it.
            log.exception("subagent_create: the job row was not written; stopping the worker")
            try:
                await worker.cancel(job_id=backend_job_id)
            except Exception:
                log.exception("subagent_create: the orphaned worker could not be stopped")
            return CreateOutcome("unavailable", reason="row_not_written")
        return CreateOutcome("started", id=str(row.id))

    async def recent(
        self, *, platform: str, conversation_id: str, person_id: str | None,
    ) -> list[JobView]:
        """This room's jobs, newest first. A job's document ids are listed to `person_id`, the
        one asking, only where they may read them, as `read` shows them; no one, no ids."""
        rows = await self._jobs.room_jobs(
            platform=platform, conversation_id=conversation_id, kind=SUBAGENT_KIND,
            limit=LIST_LIMIT,
        )
        views = []
        for r in rows:
            readable = await self._titles(r, r.result_document_ids, person_id)
            views.append(replace(
                self._view(r, titles=(), full=False),
                document_ids=tuple(i for i, _ in readable),
            ))
        return views

    async def read(
        self, *, platform: str, conversation_id: str, job_id: str, person_id: str | None,
    ) -> JobView | None:
        """One job of THIS room, or None: unknown, malformed, another room's, another kind's.
        Its documents are shown to `person_id`, the one asking, only where they may read them."""
        row = await self._own(platform, conversation_id, job_id)
        if row is None:
            return None
        live = self._live(row)
        ids = (tuple(getattr(live, "document_ids", ())) if live is not None else ()) or (
            row.result_document_ids
        )
        readable = await self._titles(row, ids, person_id)
        ids = tuple(i for i, _ in readable)
        titles = tuple(t for _, t in readable)
        view = self._view(row, titles=titles, full=True)
        checkpoint = self._checkpoint_text(row, live, ids, titles) or None
        return replace(view, document_ids=ids, checkpoint=checkpoint)

    async def cancel(
        self, *, platform: str, conversation_id: str, job_id: str,
    ) -> tuple[JobView, bool] | None:
        """End a running job of THIS room as cancelled. `(view, changed)`; `changed` is False
        when there was nothing left to cancel (already cancelled, or already finished). None
        for an unknown id. The row is claimed first (the poller's own compare-and-set), so a
        job being delivered at this instant is never also cancelled."""
        row = await self._own(platform, conversation_id, job_id)
        if row is None:
            return None
        worker = self._worker
        if row.state is ComputeJobState.PENDING:
            if worker is None:
                raise SubagentUnavailableError("no worker is attached")
            if await self._jobs.claim(row.id):
                try:
                    await worker.cancel(job_id=row.backend_job_id)
                except Exception:
                    # the row is ended below either way: a worker that would not stop must
                    # not leave a row the poller would still deliver
                    log.exception("subagent_cancel: the worker would not stop")
                await self._jobs.mark(
                    row.id, ComputeJobState.CANCELLED, completed_at=self._clock(),
                    error="it was cancelled", failure_reason="cancelled",
                )
                fresh = await self._jobs.get(row.id)
                return self._view(fresh or row, titles=()), True
        fresh = await self._jobs.get(row.id)
        return self._view(fresh or row, titles=()), False

    async def _own(self, platform: str, conversation_id: str, job_id: str) -> ComputeJob | None:
        try:
            uid = UUID(str(job_id).strip())
        except (ValueError, TypeError, AttributeError):
            return None
        row = await self._jobs.get(uid)
        if row is None or row.kind != SUBAGENT_KIND:
            return None
        if (row.platform, row.conversation_id) != (platform, conversation_id):
            return None
        return row

    def _live(self, row: ComputeJob) -> Any:
        """The worker's in-memory status for a job still running, or None."""
        if row.state is not ComputeJobState.PENDING or self._worker is None:
            return None
        live = self._worker.status(row.backend_job_id)
        return live if live is not None and live.running else None

    def _checkpoint_text(
        self, row: ComputeJob, live: Any, ids: tuple[str, ...], titles: Sequence[str],
    ) -> str:
        """The checkpoint and outputs as `subagent_read` shows them. A running job's come from
        the worker's memory (fresher than the row); a finished job's from the row. Outputs are
        the document ids and kept files code recorded, never anything the checkpoint says."""
        stored, kept = read_row(row.checkpoint, row.checkpoint_at)
        checkpoint = getattr(live, "checkpoint", None) or stored
        kept = tuple(getattr(live, "kept_files", ())) or kept
        padded = [*titles, *([""] * len(ids))]
        outputs = Outputs(
            documents=tuple((i, padded[n]) for n, i in enumerate(ids)), kept_files=kept,
            rounds=getattr(live, "rounds", None),
        )
        return render_checkpoint(
            checkpoint, outputs, title=job_title(row.payload, row.task), now=self._clock(),
            mode="read",
        )

    async def _titles(
        self, row: ComputeJob, ids: tuple[str, ...], person_id: str | None,
    ) -> tuple[tuple[str, str], ...]:
        """`(id, title)` for each document `person_id` may read. One they may not read, or whose
        read fails, is left out whole: no id, no title."""
        if self._documents is None or person_id is None or not ids:
            return ()
        readable: list[tuple[str, str]] = []
        for raw in ids:
            try:
                out = await self._documents.read(
                    platform=row.platform, conversation_id=row.conversation_id,
                    document_id=UUID(raw), person_id=person_id,
                )
                if out.status == "ok":
                    readable.append((raw, str(out.title)))
            except Exception:
                log.exception("subagent_read: document %s did not read back", raw)
        return tuple(readable)

    def _view(self, row: ComputeJob, *, titles: Sequence[str], full: bool = True) -> JobView:
        age = max(0, int((self._clock() - row.created_at).total_seconds()))
        state = _state(row)
        rounds: int | None = None
        last_tool: str | None = None
        if state == "running" and self._worker is not None:
            live = self._worker.status(row.backend_job_id)
            if live is not None and live.running:
                rounds, last_tool = live.rounds, live.last_tool
            else:
                # the worker has ended (or this process forgot it) and the poller has not
                # delivered yet
                state = "finishing"
        report = (row.result_report or "")[: REPORT_CHARS if full else LIST_REPORT_CHARS] or None
        return JobView(
            id=str(row.id), state=state, title=job_title(row.payload, row.task),
            description=row.task if full else row.task[:LIST_DESCRIPTION_CHARS],
            age_seconds=age,
            rounds=rounds, last_tool=last_tool, report=report,
            document_ids=row.result_document_ids,
            document_titles=tuple(titles),
            # the controlled vocabulary only: `error` is free text a worker or a backend wrote
            failure=row.failure_reason if state in ("failed", "cancelled") else None,
        )


def _state(row: ComputeJob) -> str:
    if row.state is ComputeJobState.PENDING:
        return "running"
    if row.state is ComputeJobState.FIRING:
        return "finishing"
    if row.state is ComputeJobState.DONE:
        return "done"
    if row.state is ComputeJobState.CANCELLED:
        return "cancelled"
    # `failed` with no failure reason and a report is a job that finished whose delivery did
    # not land; the work is real, so it is not reported as a failed job.
    if row.failure_reason is None and row.result_report:
        return "done"
    return "failed"


def _problem(brief: SubagentBrief) -> str:
    title = " ".join(brief.title.split())
    if not title:
        return "title is empty"
    if len(title) > TITLE_CHARS:
        return f"title is {len(title)} characters; keep it to {TITLE_CHARS} or fewer"
    if not brief.objective.strip():
        return "objective is empty"
    if not brief.deliverable.strip():
        return "deliverable is empty"
    for raw in brief.inputs:
        if workspace_path(raw) is None:
            return f"input {raw!r} is not a path inside the workspace"
    for raw in brief.document_ids:
        try:
            UUID(raw.strip())
        except (ValueError, AttributeError):
            return f"document_id {raw!r} is not a document id"
    for d in brief.decisions:
        if not (d.question.strip() and d.answer.strip() and d.by.strip()):
            return "each decision needs a question, an answer and who decided"
    return ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
