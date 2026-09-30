"""An audited read of a room's workspace for Nora's own code (S3a).

Another tool that needs a file from the computer (the documents path's
`document_publish` reading a spec, ADR 0020) must not call
`SandboxRuntime.fetch` itself: that would skip the three things every
computer call carries. This does them, once, for any caller:

- **the scope is derived here**, with `computer_scope(platform,
  conversation_id)`, from the caller's room, never from a model argument;
- **the lease is taken** the way the computer tools take it
  (design/sandbox-runtime §15.4), on the SAME key by the same function
  (`lease_key`): the room's open session's key when it has one, else the
  scope's; a live actor waits its bound, a background one never waits. The
  read continues an open session, as `computer_read` does, and never opens
  one (a read is not a task);
- **two `computer_events` rows** are written, `started` BEFORE the computer
  is asked and `finished` after, on every path, with `tool` set to the
  calling operation (`document_publish`) and `actor` who is asking (`tag`,
  `fire`, `dreamer:<adaptor>`). A read whose `started` row cannot be written
  is not made.

The bytes are checked on Nora's side like `computer_export`'s: the size
against `max_bytes` and the sha256 computed here, a disagreeing claim from
the VM being `unavailable`. It NEVER raises: every failure is a
`BytesResult` whose status says what happened (`denied` for a path outside
the workspace or a file over the cap, `nothing_found`, `unavailable`).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ..domain.computer import BytesResult, SandboxStatus, computer_scope
from ..domain.computer_events import (
    CANCELLED,
    STARTED,
    ComputerEvent,
    ComputerEventStore,
    is_live_actor,
    valid_actor,
)
from ..domain.computer_leases import ComputerLeases, lease_key
from ..domain.computer_sessions import ComputerSessionStore
from ..domain.ports import SandboxRuntime
from ..obs.logging import current_tag_id
from ..obs.metrics import Metrics
from ..obs.tracing import current_span_ids

log = logging.getLogger("nora.computer")

#: Strong references to in-flight `finished` appends: a shielded task the
#: caller stops awaiting is otherwise only weakly held by the loop.
_PENDING: set[asyncio.Future[None]] = set()

__all__ = ["WorkspaceReader"]


def _now() -> datetime:
    return datetime.now(UTC)


class WorkspaceReader:
    """`read_workspace_bytes`, bound to the process's computer collaborators:
    the runtime (None while `[computer]` is off), the audit store, the ONE
    `ComputerLeases` the tools share, a clock and the metrics."""

    def __init__(
        self, *, runtime: SandboxRuntime | None, events: ComputerEventStore | None,
        leases: ComputerLeases | None, metrics: Metrics,
        sessions: ComputerSessionStore | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._runtime = runtime
        self._sessions = sessions
        self._events = events
        self._leases = leases
        self._metrics = metrics
        self._clock = clock

    async def read_workspace_bytes(
        self, *, platform: str, conversation_id: str, path: str, max_bytes: int,
        actor: str, operation: str, conversation_type: str = "group",
        requester_person_id: str | None = None,
    ) -> BytesResult:
        """The bytes of `path` in this room's workspace, at most `max_bytes`,
        audited under `operation` on behalf of `actor`. Never raises."""
        try:
            return await self._read(
                platform=platform, conversation_id=conversation_id, path=path,
                max_bytes=max_bytes, actor=actor, operation=operation,
                conversation_type=conversation_type,
                requester_person_id=requester_person_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the never-raise barrier
            log.exception("read_workspace_bytes failed unexpectedly (operation=%s)", operation)
            return BytesResult(SandboxStatus.UNAVAILABLE,
                               reason=f"the read failed ({type(exc).__name__})")

    async def _read(
        self, *, platform: str, conversation_id: str, path: str, max_bytes: int,
        actor: str, operation: str, conversation_type: str,
        requester_person_id: str | None,
    ) -> BytesResult:
        if not valid_actor(actor):
            return BytesResult(SandboxStatus.DENIED, reason=f"unknown actor {actor!r}")
        if max_bytes <= 0:
            return BytesResult(SandboxStatus.DENIED, reason="max_bytes must be positive")
        scope = computer_scope(platform, conversation_id)
        session_id = await self._session(platform, conversation_id, scope, actor, operation)
        started = self._clock()
        operation_id = uuid.uuid4()
        request = {"path": path, "max_bytes": max_bytes}

        def row(phase: str, status: str, result: dict[str, Any] | None = None) -> ComputerEvent:
            trace_id, span_id = current_span_ids()
            finished = None if phase == STARTED else self._clock()
            return ComputerEvent(
                id=uuid.uuid4(), operation_id=operation_id, phase=phase,
                created_at=max(self._clock(), finished or started), started_at=started,
                finished_at=finished, actor=actor, requester_person_id=requester_person_id,
                platform=platform, conversation_id=conversation_id,
                conversation_type=conversation_type, tag_id=current_tag_id(),
                scope=scope, tool=operation, path="direct", trace_id=trace_id,
                span_id=span_id, status=status,
                request=request if phase == STARTED else {},
                result={"status": status, **(result or {})} if phase != STARTED else {},
                session_id=session_id,
            )

        if self._events is None:
            return BytesResult(SandboxStatus.UNAVAILABLE,
                               reason="the read could not be recorded, so it was not made")
        try:
            await self._events.append(row(STARTED, STARTED))
        except Exception:
            log.exception("read_workspace_bytes: started row failed; refusing")
            self._metrics.inc("nora_computer_audit_failures_total", tool=operation, stage="row")
            return BytesResult(SandboxStatus.UNAVAILABLE,
                               reason="the read could not be recorded, so it was not made")
        status, outcome = CANCELLED, {"reason": "cancelled before the computer answered"}
        try:
            got = await self._leased_fetch(lease_key(scope, session_id), scope, path, max_bytes,
                                           live=is_live_actor(actor))
            status = got.status.value
            outcome = {"reason": got.reason, "size": len(got.data), "sha256": got.sha256}
            return got
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status, outcome = "unavailable", {"reason": type(exc).__name__}
            raise
        finally:
            await self._finish(row("finished", status, outcome), operation)

    async def _session(
        self, platform: str, conversation_id: str, scope: str, actor: str, operation: str,
    ) -> UUID | None:
        """The room's open session, continued (never opened), or None: the
        tools' own rule, so the lease key below is theirs. A failure is
        logged and counted and the read runs under the scope's key, as a
        tool call does when its session cannot be read."""
        if self._sessions is None:
            return None
        try:
            found = await self._sessions.continue_or_open(
                platform=platform, conversation_id=conversation_id, scope=scope,
                objective="", actor=actor, tag_id=current_tag_id(), now=self._clock(),
                open_new=False,
            )
        except Exception:
            log.exception("read_workspace_bytes: session continue failed")
            self._metrics.inc("nora_computer_audit_failures_total", tool=operation,
                              stage="session")
            return None
        return found[0].id if found is not None else None

    async def _leased_fetch(
        self, key: str, scope: str, path: str, max_bytes: int, *, live: bool,
    ) -> BytesResult:
        if self._runtime is None:
            return BytesResult(SandboxStatus.UNAVAILABLE, reason="the computer is off here")
        lease = None
        if self._leases is not None:
            lease = await self._leases.acquire(key, live=live)
            if lease is None:
                return BytesResult(SandboxStatus.UNAVAILABLE, reason="busy")
        try:
            got = await self._runtime.fetch(scope=scope, path=path, max_bytes=max_bytes)
        finally:
            if lease is not None and self._leases is not None:
                self._leases.release(lease)
        if got.status is not SandboxStatus.OK:
            return got
        # Nora's side decides: the size and the digest of what arrived.
        if len(got.data) > max_bytes:
            return BytesResult(SandboxStatus.DENIED, reason="file is over the cap")
        digest = hashlib.sha256(got.data).hexdigest()
        if got.sha256 is not None and got.sha256 != digest:
            return BytesResult(SandboxStatus.UNAVAILABLE,
                               reason="the bytes received do not match the digest claimed")
        return BytesResult(SandboxStatus.OK, data=got.data, sha256=digest)

    async def _finish(self, event: ComputerEvent, operation: str) -> None:
        """The `finished` row, shielded from the caller's cancellation; a
        failure is logged and counted, never raised (the read happened)."""
        if self._events is None:
            return
        try:
            task = asyncio.ensure_future(self._events.append(event))
            _PENDING.add(task)
            task.add_done_callback(_PENDING.discard)
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("read_workspace_bytes: finished row failed")
            self._metrics.inc("nora_computer_audit_failures_total", tool=operation, stage="row")
