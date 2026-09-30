"""`ComputerContextReader`: the `<computer>` block's one read
(design/sandbox-runtime §15.3), shared by the tag path
(`TranscriptInjection`) and the fire path (`FirePath`).

**The gate comes first and costs one database read.** A room renders the
block only when it used the computer within `active_days` (a `computer_events`
row by anyone but the sweeper) OR has an open or lapsed session. Every other
room gets `None` and nothing is asked of the VM: the block's cost lands only
on rooms that use the computer. The reader exists only while `[computer]
enabled`, so an off deployment renders nothing anywhere.

**Never raises.** A failure is logged, counted (`nora_errors_total{stage=
"computer_context"}`) and read as no block: a turn never fails because the
computer's summary could not be built. A machine that cannot be read still
renders the database half, with a line saying the live state is missing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from nora.domain.computer import SandboxStatus, computer_scope
from nora.domain.computer_context import (
    ComputerContextCaps,
    ComputerView,
    RunLine,
    SessionLine,
)
from nora.domain.computer_events import ComputerEvent, ComputerEventStore
from nora.domain.computer_sessions import (
    COMMAND_CHARS,
    EXPIRED,
    ComputerSession,
    ComputerSessionStore,
    cut,
    derive_progress,
    shown_status,
)
from nora.domain.ports import SandboxRuntime

log = logging.getLogger("nora.computer.context")

#: The room's recent audit rows read for the gate and the recent runs.
EVENTS_READ = 200
#: A room's sessions read (open and expired, newest first).
SESSIONS_READ = 20
#: One session's rows read for its progress.
SESSION_EVENTS_READ = 500


class ComputerContextReader:
    def __init__(
        self, *, sessions: ComputerSessionStore, events: ComputerEventStore,
        runtime: SandboxRuntime, caps: ComputerContextCaps,
        clock: Callable[[], datetime], metrics: object,
    ) -> None:
        self._sessions = sessions
        self._events = events
        self._runtime = runtime
        self._caps = caps
        self._clock = clock
        self._metrics = metrics

    async def read(self, *, platform: str, conversation_id: str) -> ComputerView | None:
        try:
            return await self._read(platform, conversation_id)
        except Exception:
            log.exception("computer context read failed; rendering no block")
            inc = getattr(self._metrics, "inc", None)
            if inc is not None:
                inc("nora_errors_total", stage="computer_context", platform=platform)
            return None

    async def _read(self, platform: str, conversation_id: str) -> ComputerView | None:
        now = self._clock()
        caps = self._caps
        rows = await self._events.recent(
            conversation_id=conversation_id, since=now - timedelta(days=caps.active_days),
            limit=EVENTS_READ,
        )
        used = any(r.actor != "sweeper" for r in rows)
        sessions = await self._sessions.for_room(
            platform=platform, conversation_id=conversation_id, limit=SESSIONS_READ,
        )
        open_rows = [s for s in sessions if s.status != EXPIRED]
        lapsed_rows = [
            s for s in sessions
            if s.status == EXPIRED and s.expired_at is not None
            and now - s.expired_at <= caps.lapsed_visible
        ]
        if not used and not open_rows and not lapsed_rows:
            return None
        state = await self._runtime.state(scope=computer_scope(platform, conversation_id))
        machine_read = state.status is SandboxStatus.OK
        tmux = state.tmux if machine_read else None
        running = bool(tmux) and any(t.running for t in tmux or ())
        cutoff = now - timedelta(days=caps.recent_file_days)
        files = tuple(f for f in state.files if f.mtime >= cutoff) if machine_read else ()

        async def line(s: ComputerSession) -> SessionLine:
            shown = shown_status(s, now=now, idle_after=caps.idle_after,
                                 lapsed_visible=caps.lapsed_visible,
                                 tmux_running=running and s.status != EXPIRED)
            try:
                mine = await self._events.for_session(session_id=s.id,
                                                      limit=SESSION_EVENTS_READ)
                progress = derive_progress(mine, capped=len(mine) >= SESSION_EVENTS_READ)
            except Exception:
                log.exception("computer session progress read failed")
                progress = None
            return SessionLine(session=s, shown=shown, progress=progress)

        # Progress is read only for the sessions the caps will show.
        open_lines = tuple([await line(s) for s in open_rows[: caps.max_open_sessions]]
                           + [SessionLine(s, "active", None)
                              for s in open_rows[caps.max_open_sessions:]])
        lapsed_lines = tuple(
            [await line(s) for s in lapsed_rows[: caps.max_lapsed_sessions]]
            + [SessionLine(s, "lapsed", None) for s in lapsed_rows[caps.max_lapsed_sessions:]]
        )
        return ComputerView(
            now=now, caps=caps, open_sessions=open_lines, lapsed_sessions=lapsed_lines,
            tmux=tmux, files=files, runs=_runs(rows), machine_read=machine_read,
        )


def _runs(rows: list[ComputerEvent]) -> tuple[RunLine, ...]:
    """The room's `computer_run` calls, newest first, from their two rows."""
    finished = {r.operation_id: r for r in rows if r.phase == "finished"}
    out: list[RunLine] = []
    for r in rows:  # newest first already
        if r.tool != "computer_run" or r.phase != "started":
            continue
        done = finished.get(r.operation_id)
        raw = done.result.get("exit_code") if done is not None else None
        exit_code = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
        out.append(RunLine(
            command=cut(str(r.request.get("command", "")), COMMAND_CHARS),
            exit_code=exit_code, finished=done is not None, at=r.started_at,
        ))
    return tuple(out)
