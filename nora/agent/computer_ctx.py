"""The computer tools' shared per-turn state."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import datetime
from functools import partial, wraps
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from nora.agent.catalog.surface import _AUDIT_TASKS
from nora.agent.toolkit.results import _audit_safe, _record
from nora.domain.computer import (
    ComputerToolSettings,
    computer_dm_scope,
    computer_unit_scope,
)
from nora.domain.computer_events import (
    DREAMER_ACTOR_PREFIX,
    STARTED,
    ComputerEvent,
    is_live_actor,
)
from nora.domain.computer_leases import LIVE_WAIT_SECONDS, Lease, lease_key
from nora.domain.computer_sessions import (
    OBJECTIVE_CHARS,
    cut,
)
from nora.domain.models import (
    strip_mention_placeholders,
)
from nora.obs.logging import current_tag_id
from nora.obs.tracing import current_span_ids

log = logging.getLogger("nora.tools")


def _cctx__p1(c):
    c.computer_bounds = c.computer_settings or ComputerToolSettings()
    c.computer_platform = c.room.platform if c.room is not None else ""
    if c.room is None:
        c.bound_computer_scope = ""
    elif c.conversation_type == "group":
        c.computer_unit = c._unit()
        c.bound_computer_scope = computer_unit_scope(c.computer_unit) if c.computer_unit else ""
    else:
        c._dm_scope_id = (
            c.computer_dm_scope_for(c.computer_platform, c.bound_conversation_id)
            if c.computer_dm_scope_for is not None
            else None
        )
        c.bound_computer_scope = computer_dm_scope(c._dm_scope_id) if c._dm_scope_id else ""

    def _computer_dirty() -> None:
        """A mutating segment landed: the context block's cache must re-read
        the workspace/browser view, not serve the turn what it had."""
        if c.computer_context_invalidate is not None and c.bound_computer_scope:
            c.computer_context_invalidate(c.bound_computer_scope)

    c._computer_dirty = _computer_dirty
    c.computer_limits = dict(c.computer_bounds.limits) or None
    c.computer_actor = (
        "subagent"
        if c.subagent_job
        else f"{DREAMER_ACTOR_PREFIX}{c.dream_adaptor}"
        if c.dream_adaptor
        else ("fire")
        if c.fire_turn
        else "tag"
    )
    c.computer_ready = (
        bool(c.bound_computer_scope)
        and c.computer is not None
        and (c.computer_events is not None)
        and (c.blob_store is not None)
    )

    def _computer_requester() -> str | None:
        tagger = (
            c.room.tagger
            if c.room is not None and (not c.fire_turn) and (not c.dream_adaptor)
            else None
        )
        return tagger.person_id if tagger is not None else None

    c._computer_requester = _computer_requester

    async def _computer_blob(tool: str, text: str) -> dict[str, object]:
        """Put one full input or output in the blob store; return its ref.

        The ref always carries the size and sha256 of the text; `key` is added
        and `stored` is true when the bytes are stored. A failed put is logged
        and counted (`stage="blob"`) and the ref says `stored: false`: the row
        still records that the text existed and what it hashed to."""
        data = text.encode("utf-8", errors="surrogatepass")
        ref: dict[str, object] = {
            "bytes": len(data),
            "chars": len(text),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if not data:
            return ref
        if c.blob_store is None:
            if c.computer_events is not None:
                c.metrics.inc("nora_computer_audit_failures_total", tool=tool, stage="blob")
            ref["stored"] = False
            return ref
        try:
            ref["key"] = await c.blob_store.put(data=data)
            ref["stored"] = True
        except Exception:
            log.exception("computer audit blob put failed (tool=%s); answering anyway", tool)
            c.metrics.inc("nora_computer_audit_failures_total", tool=tool, stage="blob")
            ref["stored"] = False
        return ref

    c._computer_blob = _computer_blob

    def _computer_row(
        tool: str,
        operation_id: UUID,
        phase: str,
        status: str,
        *,
        started: datetime,
        finished: datetime | None,
        request: Mapping[str, object] | None = None,
        result: Mapping[str, object] | None = None,
        output_ref: Mapping[str, object] | None = None,
        session_id: UUID | None = None,
    ) -> ComputerEvent:
        trace_id, span_id = current_span_ids()
        return ComputerEvent(
            id=uuid.uuid4(),
            operation_id=operation_id,
            phase=phase,
            created_at=max(c.now_fn(), finished or started),
            started_at=started,
            finished_at=finished,
            actor=c.computer_actor,
            requester_person_id=c._computer_requester(),
            platform=c.computer_platform,
            conversation_id=c.bound_conversation_id,
            conversation_type=c.conversation_type,
            tag_id=current_tag_id(),
            scope=c.bound_computer_scope,
            tool=tool,
            path=("subagent") if c.subagent_job else "direct",
            trace_id=trace_id,
            span_id=span_id,
            status=status,
            request=_audit_safe(request or {}),
            result=_audit_safe({"status": status, **(result or {})}) if phase == "finished" else {},
            output_ref=_audit_safe(output_ref or {}),
            session_id=session_id,
        )

    c._computer_row = _computer_row
    return (False, None, None)


def _cctx__p2(c):

    async def _computer_begin(
        tool: str,
        operation_id: UUID,
        *,
        started: datetime,
        request: Mapping[str, object],
        session_id: UUID | None = None,
    ) -> bool:
        """Append the operation's `started` row, BEFORE the call reaches the
        computer. False when it could not be written: the caller then refuses
        the call, because an operation without a record must not happen. The
        failure is logged and counted (`stage="row"`)."""
        if c.computer_events is None:
            return False
        try:
            await c.computer_events.append(
                c._computer_row(
                    tool,
                    operation_id,
                    "started",
                    STARTED,
                    started=started,
                    finished=None,
                    request=request,
                    session_id=session_id,
                )
            )
        except Exception:
            log.exception("computer_events started append failed (tool=%s); refusing", tool)
            c.metrics.inc("nora_computer_audit_failures_total", tool=tool, stage="row")
            _record(c.metrics, tool, "unavailable")
            return False
        return True

    c._computer_begin = _computer_begin

    async def _computer_finish(
        tool: str,
        operation_id: UUID,
        status: str,
        *,
        started: datetime,
        finished: datetime | None,
        result: Mapping[str, object],
        output_ref: Mapping[str, object] | None = None,
        session_id: UUID | None = None,
    ) -> None:
        """Count the call and append its `finished` row.

        Called from each tool's `finally`, so it runs on every path out of a
        call that began, cancellation included. The append runs as its own
        task behind `asyncio.shield`: a second cancellation of the turn while
        it is in flight cannot interrupt the write, only the wait for it. A
        failed append is logged and counted (`stage="row"`) and the tool
        STILL answers: the call already ran, and losing the reply because a
        log write failed would make the record cost the thing it watches.

        The log line carries counts only (`_log_call`'s rule): the command and
        the path are in the audit row, never in the log file.
        """
        _record(c.metrics, tool, status)
        await c._computer_append_finished(
            c._computer_row(
                tool,
                operation_id,
                "finished",
                status,
                started=started,
                finished=finished or c.now_fn(),
                result=result,
                output_ref=output_ref,
                session_id=session_id,
            ),
            tool=tool,
        )

    c._computer_finish = _computer_finish

    async def _computer_append_finished(row: ComputerEvent, *, tool: str) -> None:
        """Append one `finished` row, shielded from the turn's cancellation
        (see `_computer_finish`). A failure is logged and counted, never
        raised: the call it records has already happened."""
        if c.computer_events is None:
            return
        try:
            task = asyncio.ensure_future(c.computer_events.append(row))
            _AUDIT_TASKS.add(task)
            task.add_done_callback(_AUDIT_TASKS.discard)
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "computer_events finished append failed (tool=%s); answering anyway", tool
            )
            c.metrics.inc("nora_computer_audit_failures_total", tool=tool, stage="row")

    c._computer_append_finished = _computer_append_finished

    async def _computer_off(
        tool: str, operation_id: UUID, *, started: datetime, request: Mapping[str, object]
    ) -> None:
        """Both rows of a call that never reached a computer (none is wired,
        or the audit is incomplete): recorded when the row store is wired."""
        if await c._computer_begin(tool, operation_id, started=started, request=request):
            await c._computer_finish(
                tool,
                operation_id,
                "unavailable",
                started=started,
                finished=started,
                result={("reason"): "off"},
            )
        elif c.computer_events is None:
            _record(c.metrics, tool, "unavailable")

    c._computer_off = _computer_off
    c.computer_live = is_live_actor(c.computer_actor)
    c.computer_objective = cut(strip_mention_placeholders(c.question or ""), OBJECTIVE_CHARS)
    return (False, None, None)


def _cctx__p3(c):

    async def _computer_session(tool: str) -> UUID | None:
        """Continue this room's open computer session, or open one; its id,
        or None when there is no session store or it failed. Runs BEFORE the
        call's `started` row, which carries the id. A failure is logged and
        counted (`stage="session"`) and the call still runs: the session is
        bookkeeping over the audit, and the audit row is what must exist.
        An opening writes its own transition row.

        A sub-agent job keeps no session: sessions are a live turn's task record
        (`<computer>`, the sweeper), and the worker's own record is its job row and
        its audit rows, so it neither continues nor bumps a tag turn's."""
        if c.computer_sessions is None or c.subagent_job:
            return None
        now = c.now_fn()
        try:
            found = await c.computer_sessions.continue_or_open(
                platform=c.computer_platform,
                conversation_id=c.bound_conversation_id,
                scope=c.bound_computer_scope,
                objective=c.computer_objective,
                actor=c.computer_actor,
                tag_id=current_tag_id(),
                now=now,
                open_new=c.computer_live,
            )
        except Exception:
            log.exception("computer session continue_or_open failed (tool=%s)", tool)
            c.metrics.inc("nora_computer_audit_failures_total", tool=tool, stage="session")
            return None
        if found is None:
            return None
        session, opened = found
        if opened:
            await c._computer_append_finished(
                c._computer_row(
                    "computer_session",
                    uuid.uuid4(),
                    ("finished"),
                    "opened",
                    started=now,
                    finished=now,
                    session_id=session.id,
                    result={
                        "transition": "opened",
                        "objective": session.objective,
                        "by_tool": tool,
                    },
                ),
                tool=tool,
            )
        return session.id

    c._computer_session = _computer_session
    return (False, None, None)


def _cctx__p4(c):

    @contextlib.asynccontextmanager
    async def _computer_lease(
        tool: str, operation_id: UUID, session_id: UUID | None
    ) -> AsyncIterator[str | None]:
        """The lease on this unit's workspace around a call, independent
        of session identity. A live turn waits
        its bound; a background pass never waits. Yields None when held (or
        when no lease table is wired), else `"live"` or `"background"`: the
        call was refused, one `busy` row is written, and the caller answers
        the matching busy note without calling the computer. A held lease
        gets a `started` row on acquire and a `finished` (`released`) row on
        exit, whatever the call did."""
        if c.computer_leases is None:
            yield None
            return
        key = (
            f"job:{c.subagent_job}"
            if c.subagent_job
            else lease_key(c.bound_computer_scope, session_id)
        )
        asked = c.now_fn()
        waits = c.computer_live or bool(c.subagent_job)
        lease: Lease | None = await c.computer_leases.acquire(
            key,
            live=waits,
            wait_seconds=float(c.computer_bounds.max_run_timeout_seconds)
            if c.subagent_job
            else None,
        )
        request = {"key": key, "for_tool": tool, "for_operation": str(operation_id)}
        if lease is None:
            _record(c.metrics, tool, "unavailable")
            await c._computer_append_finished(
                c._computer_row(
                    "computer_lease",
                    uuid.uuid4(),
                    ("finished"),
                    "busy",
                    started=asked,
                    finished=c.now_fn(),
                    session_id=session_id,
                    result={**request, "deferred": not waits},
                ),
                tool=tool,
            )
            yield ("live" if waits else "background")
            return
        lease_op, acquired = (uuid.uuid4(), c.now_fn())
        try:
            if c.computer_events is not None:
                await c.computer_events.append(
                    c._computer_row(
                        "computer_lease",
                        lease_op,
                        "started",
                        STARTED,
                        started=acquired,
                        finished=None,
                        session_id=session_id,
                        request={**request, ("waited_ms"): lease.waited_ms},
                    )
                )
        except Exception:
            log.exception("computer lease row failed (tool=%s); running anyway", tool)
            c.metrics.inc("nora_computer_audit_failures_total", tool=tool, stage="lease")
        try:
            yield None
        finally:
            held_ms = c.computer_leases.release(lease)
            await c._computer_append_finished(
                c._computer_row(
                    "computer_lease",
                    lease_op,
                    "finished",
                    ("released"),
                    started=acquired,
                    finished=c.now_fn(),
                    session_id=session_id,
                    result={"held_ms": held_ms},
                ),
                tool=tool,
            )

    c._computer_lease = _computer_lease
    c._segment_deadline: dict[str, float] = {
        "native": float(c.computer_bounds.max_run_timeout_seconds) + 60.0,
        ("browse"): 660.0,
        "capture": 180.0,
        "worker": 960.0,
    }
    return (False, None, None)


def _cctx__p5(c):
    c._queued_run = c._computer_queued("native", LIVE_WAIT_SECONDS, dirty=True)
    c._queued_browse = c._computer_queued("browse", LIVE_WAIT_SECONDS, dirty=True)
    c._queued_capture = c._computer_queued("capture", LIVE_WAIT_SECONDS, dirty=True)
    return (False, None, None)


def _cctx__computer_queued__p1(c):

    def decorate(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict:
            if c.computer_admission is None or not c.bound_computer_scope:
                return await fn(*args, **kwargs)
            origin = {
                "platform": c.computer_platform,
                "conversation_id": c.bound_conversation_id,
                ("conversation_type"): c.conversation_type,
                "actor": c.computer_actor,
                "requester_person_id": c._computer_requester(),
                "tag_id": current_tag_id(),
                "job": "",
                "tool": fn.__name__,
                "grant": sorted(c.grant) if c.grant is not None else None,
                "fire": bool(c.fire_turn),
                "relay": bool(c.relay_turn),
                "worker": c.subagent_job or "",
            }
            submitted = await c.computer_admission.submit(
                scope=c.bound_computer_scope,
                kind=c.kind,
                payload={("tool"): fn.__name__, "args": _audit_safe(dict(kwargs))},
                origin=origin,
                wait_seconds=c.wait_seconds,
                deadline_at=c.computer_admission.deadline_after(
                    c._segment_deadline.get(c.kind, 960.0)
                ),
            )
            if submitted.inherited:
                reply = await fn(*args, **kwargs)
                if c.dirty:
                    c._computer_dirty()
                return reply
            if submitted.result is not None:
                row = submitted.result
                if not await c.computer_admission.delivered(row.id):
                    if c.dirty:
                        c._computer_dirty()
                    return {
                        "status": "unavailable",
                        "task": str(row.id),
                        "note": (
                            "That computer task finished, but its answer could not be committed "
                            "for "
                            "delivery — it may still arrive on its own."
                        ),
                    }
                reply = dict(row.result or {})
                reply.setdefault("status", row.status)
                reply.setdefault(
                    "note",
                    ("That computer task ended before it could answer; its record says why."),
                )
                if c.dirty:
                    c._computer_dirty()
                return reply
            if submitted.granted:
                reply = await c.computer_admission.run_segment(submitted.task, fn(*args, **kwargs))
                if c.dirty:
                    c._computer_dirty()
                return dict(reply)
            return {
                "status": "queued",
                "task": str(submitted.task.id),
                "note": (
                    "Your computer is busy right now; this is queued and I'll tell you what it did "
                    "when it finishes. To stop it, computer_cancel with this task id."
                ),
            }

        return wrapper

    c.decorate = decorate
    return (True, None, c.decorate)
    return (False, None, None)


def _cctx__computer_queued(c, kind, wait_seconds, dirty):
    """Wrap a computer tool in the scope's FIFO admission.

    No admission wired (or no scope resolved): the tool runs
    synchronously, exactly as before. Wired: the call is a durable
    `computer_tasks` row — head-of-queue runs now through
    `run_segment` (a registered, cancellable executor that writes the
    terminal row itself), otherwise the call waits `wait_seconds` for a
    terminal row and otherwise answers `queued`: the RUNNER then
    re-builds the call from the durable `payload` through `dispatch`
    (fresh policy admission on the rebuilt request, never a captured
    closure) and delivers the result to this room through the
    compute-job path. A worker's nested call inherits its parent's
    `current_segment` and runs inline, never behind itself. `dirty`
    kinds invalidate the scope's cached context view when they land."""
    c.kind = kind
    c.wait_seconds = wait_seconds
    c.dirty = dirty
    k_, _, v_ = _cctx__computer_queued__p1(c)
    if k_:
        return v_


def build_computer_ctx(deps):
    c = SimpleNamespace(
        blob_store=deps.blob_store,
        computer=deps.computer,
        computer_admission=deps.computer_admission,
        computer_context_invalidate=deps.computer_context_invalidate,
        computer_dm_scope_for=deps.computer_dm_scope_for,
        computer_events=deps.computer_events,
        computer_leases=deps.computer_leases,
        computer_sessions=deps.computer_sessions,
        computer_settings=deps.computer_settings,
        conversation_type=deps.conversation_type,
        dream_adaptor=deps.dream_adaptor,
        fire_turn=deps.fire_turn,
        grant=deps.grant,
        metrics=deps.metrics,
        question=deps.question,
        relay_turn=deps.relay_turn,
        room=deps.room,
        subagent_job=deps.subagent_job,
        _unit=deps._unit,
        bound_conversation_id=deps.bound_conversation_id,
        now_fn=deps.now_fn,
    )
    k_, f_, v_ = _cctx__p1(c)
    if k_:
        return (True, None, v_)
    if f_ == "break":
        return (False, "break", None)
    if f_ == "continue":
        return (False, "continue", None)
    k_, f_, v_ = _cctx__p2(c)
    if k_:
        return (True, None, v_)
    if f_ == "break":
        return (False, "break", None)
    if f_ == "continue":
        return (False, "continue", None)
    k_, f_, v_ = _cctx__p3(c)
    if k_:
        return (True, None, v_)
    if f_ == "break":
        return (False, "break", None)
    if f_ == "continue":
        return (False, "continue", None)
    k_, f_, v_ = _cctx__p4(c)
    if k_:
        return (True, None, v_)
    if f_ == "break":
        return (False, "break", None)
    if f_ == "continue":
        return (False, "continue", None)
    c._computer_queued = partial(_cctx__computer_queued, c)
    k_, f_, v_ = _cctx__p5(c)
    if k_:
        return (True, None, v_)
    if f_ == "break":
        return (False, "break", None)
    if f_ == "continue":
        return (False, "continue", None)
    return SimpleNamespace(
        _computer_begin=c._computer_begin,
        _computer_blob=c._computer_blob,
        _computer_finish=c._computer_finish,
        _computer_lease=c._computer_lease,
        _computer_off=c._computer_off,
        _computer_row=c._computer_row,
        _computer_session=c._computer_session,
        _queued_browse=c._queued_browse,
        _queued_capture=c._queued_capture,
        _queued_run=c._queued_run,
        bound_computer_scope=c.bound_computer_scope,
        computer_actor=c.computer_actor,
        computer_bounds=c.computer_bounds,
        computer_limits=c.computer_limits,
        computer_platform=c.computer_platform,
        computer_ready=c.computer_ready,
    )
