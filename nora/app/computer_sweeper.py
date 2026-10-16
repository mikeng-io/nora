"""The computer session sweeper.

One pass (`ComputerSessionSweeper.sweep_once`) over every open session; the
loop around it is `nora.app.runtime.computer_sweep_loop`, `prune_loop`'s
shape, started by composition only while `[computer] enabled`.

The rules, per open session, read against the live machine (the `state`
endpoint) and the session's own `last_activity_at` (every computer call in
the room moves it):

- **idle**: no computer call for `idle_expiry_hours` and no tmux job in the
  room running (a pane runs a job when its command is not a shell OR its
  shell has any child, computerd's `pane_running`).
- **running past the limit**: a tmux job still runs, but no call for
  `max_running_days`. Expired, the job FLAGGED in the audit row and a
  counter, and never killed.
- **jobs ended**: the session was seen holding tmux sessions, none is
  left, and there has been no call for `idle_after_minutes`.

Otherwise it stays open. Fail-closed on what it cannot see: a room whose
state cannot be read, or whose tmux cannot be listed, is skipped this pass,
never read as «nothing running».

Each expiry is a compare-and-set on `last_activity_at` under the session's
lease, taken with `try_acquire` (the sweeper is a background actor): a call
in flight holds the lease and the sweeper defers; a call that lands between
the read and the write moves `last_activity_at` and the set fails. Each one
writes ONE `computer_events` row, actor `sweeper`.

**It never destroys anything.** An expiry is a database state change and one
audit row, nothing else: no tmux session is killed (a shell with a child
running reads as idle to a naive check, so killing is never safe), no file is
deleted (the runtime it holds offers neither), no session row is deleted (expiry is a status), no
audit row is changed (the table refuses it). It runs no command at all.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from nora.domain.computer import SandboxStatus, StateResult, anything_running
from nora.domain.computer_events import ComputerEvent, ComputerEventStore
from nora.domain.computer_leases import ComputerLeases, session_key
from nora.domain.computer_sessions import ComputerSession, ComputerSessionStore
from nora.domain.ports import SandboxRuntime
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.computer.sweeper")

#: The most open sessions one pass reads.
SWEEP_BATCH = 500


@dataclass(frozen=True)
class SweepRules:
    """`[computer.sessions]` as the sweeper reads it."""

    idle_expiry: timedelta = timedelta(hours=24)
    max_running: timedelta = timedelta(days=7)
    idle_after: timedelta = timedelta(minutes=30)


class ComputerSessionSweeper:
    def __init__(
        self, *, sessions: ComputerSessionStore, events: ComputerEventStore,
        runtime: SandboxRuntime, leases: ComputerLeases, rules: SweepRules,
        clock: Callable[[], datetime], metrics: Metrics,
    ) -> None:
        self._sessions = sessions
        self._events = events
        self._runtime = runtime
        self._leases = leases
        self._rules = rules
        self._clock = clock
        self._metrics = metrics

    async def sweep_once(self) -> int:
        """One pass; how many sessions it expired."""
        now = self._clock()
        states: dict[str, StateResult] = {}
        expired = 0
        for session in await self._sessions.open_sessions(limit=SWEEP_BATCH):
            if session.scope not in states:
                states[session.scope] = await self._runtime.state(scope=session.scope)
            state = states[session.scope]
            if (state.status is not SandboxStatus.OK or state.tmux is None
                    or state.processes is None):
                log.info("computer sweep skipped a room it cannot see (%s)",
                         state.reason or state.tmux_reason or state.processes_reason)
                continue
            if await self._sweep(session, state, now):
                expired += 1
        return expired

    def _reason(self, session: ComputerSession, state: StateResult, now: datetime) -> str | None:
        idle = now - session.last_activity_at
        tmux = state.tmux or ()
        # «Nothing it owns is running»: no tmux job AND no process of the
        # room's user (a `nohup … &` job has no tmux session).
        running = anything_running(state) is True
        if idle >= self._rules.idle_expiry:
            if not running:
                return "idle"
            if idle >= self._rules.max_running:
                return "running_past_limit"
            return None
        if (session.held_tmux and not tmux and not state.processes
                and idle >= self._rules.idle_after):
            return "jobs_ended"
        return None

    async def _sweep(self, session: ComputerSession, state: StateResult, now: datetime) -> bool:
        tmux = state.tmux or ()
        reason = self._reason(session, state, now)
        if tmux and reason is None:
            # What the session is holding now, so a later pass can tell
            # «its resources are gone» from «it never had any».
            await self._sessions.note_resources(
                session.id, tmux=[t.name for t in tmux], now=now,
            )
        if reason is None:
            return False
        lease = await self._leases.try_acquire(session_key(session.id))
        if lease is None:
            return False  # a call is in flight: it is not idle; next pass
        try:
            done = await self._sessions.expire(
                session.id, seen_last_activity_at=session.last_activity_at,
                reason=reason, now=now,
            )
            if done is None:
                return False  # continued, or expired, since the read
            # Flagged, never killed: a job still running when the session
            # expires is reported in the audit row for a person to look at.
            flagged = [t.name for t in tmux if t.running]
            if reason == "running_past_limit":
                log.warning("computer session %s expired with %d tmux job(s) still "
                            "running past max_running_days; left running",
                            session.id, len(flagged))
            self._metrics.inc("nora_computer_session_expiries_total", reason=reason)
            await self._audit(done, now=now, result={
                "transition": "expired", "reason": reason,
                "idle_seconds": int((now - session.last_activity_at).total_seconds()),
                "flagged_running_tmux": flagged,
                "flagged_running_processes": [
                    {"pid": p.pid, "command": p.command} for p in state.processes or ()
                ],
                "tmux_left": [t.name for t in tmux],
            })
            return True
        finally:
            self._leases.release(lease)

    async def _audit(
        self, session: ComputerSession, *, now: datetime, result: dict[str, object],
    ) -> None:
        try:
            await self._events.append(ComputerEvent(
                id=uuid.uuid4(), operation_id=uuid.uuid4(), phase="finished",
                created_at=now, started_at=now, finished_at=now, actor="sweeper",
                requester_person_id=None, platform=session.platform,
                conversation_id=session.conversation_id, conversation_type="",
                tag_id="", scope=session.scope, tool="computer_session", path="direct",
                trace_id="", span_id="", status="expired",
                result={"status": "expired", **result}, session_id=session.id,
            ))
        except Exception:
            log.exception("computer sweeper audit row failed (session %s)", session.id)
            self._metrics.inc("nora_computer_audit_failures_total",
                              tool="computer_session", stage="row")
