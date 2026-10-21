"""`ComputerContextReader`: the `<computer>` block's one read,
shared by the tag path
(`TranscriptInjection`) and the fire path (`FirePath`).

**The gate comes first and costs one database read.** A room renders the
block only when it used the computer within `active_days` (a `computer_events`
row by anyone but the sweeper) OR has an open or lapsed session. Every other
room gets `None` and nothing is asked of the VM: the block's cost lands only
on rooms that use the computer. The reader exists only while `[computer]
enabled`, so an off deployment renders nothing anywhere.

**The machine read never holds a reply.** It has its own budget
(`read_timeout_seconds`, 1.5 s by default); past it, or when the computer
answers `unavailable`, the block renders from the database alone with a line
saying the machine could not be read, and the miss is counted
(`nora_computer_context_machine_unread_total{reason}`). A good read is kept
per room for `state_cache_seconds`, so consecutive turns in a busy room do
not each ask the VM. The per-session progress reads run concurrently.

**Never raises.** A failure is logged, counted (`nora_errors_total{stage=
"computer_context"}`) and read as no block: a turn never fails because the
computer's summary could not be built.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from nora.domain.computer import SandboxStatus, StateResult, anything_running, computer_unit_scope
from nora.domain.computer_context import (
    ComputerContextCaps,
    ComputerView,
    LinkLine,
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
        unit_for: Callable[[str, str], str | None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._unit_for = unit_for
        self._sessions = sessions
        self._events = events
        self._runtime = runtime
        self._caps = caps
        self._clock = clock
        self._metrics = metrics
        self._monotonic = monotonic
        # scope -> (read at, a good read). One process, rooms in the tens,
        # entries overwritten per room: bounded by the rooms that use it.
        self._cache: dict[str, tuple[float, StateResult]] = {}

    def _inc(self, name: str, **labels: object) -> None:
        inc = getattr(self._metrics, "inc", None)
        if inc is not None:
            inc(name, **labels)

    async def _state(self, scope: str) -> StateResult | None:
        """The room's machine state within the read budget, from the cache
        when fresh; None when it could not be read in time or at all."""
        hit = self._cache.get(scope)
        if hit is not None and self._monotonic() - hit[0] < self._caps.state_cache_seconds:
            return hit[1]
        try:
            state = await asyncio.wait_for(
                self._runtime.state(scope=scope), self._caps.read_timeout_seconds,
            )
        except TimeoutError:
            self._inc("nora_computer_context_machine_unread_total", reason="timeout")
            return None
        if state.status is not SandboxStatus.OK:
            self._inc("nora_computer_context_machine_unread_total", reason="unavailable")
            return None
        self._cache[scope] = (self._monotonic(), state)
        return state

    async def read(self, *, platform: str, conversation_id: str) -> ComputerView | None:
        try:
            return await self._read(platform, conversation_id)
        except Exception:
            log.exception("computer context read failed; rendering no block")
            self._inc("nora_errors_total", stage="computer_context", platform=platform)
            return None

    async def _read(self, platform: str, conversation_id: str) -> ComputerView | None:
        unit = self._unit_for(platform, conversation_id) if self._unit_for else None
        if not unit:
            return None
        scope = computer_unit_scope(unit)
        now = self._clock()
        caps = self._caps
        rows = await self._events.for_scope(
            scope=scope, since=now - timedelta(days=caps.active_days), limit=EVENTS_READ,
        )
        used = any(r.actor != "sweeper" for r in rows)
        sessions = await self._sessions.for_scope(
            scope=scope, limit=SESSIONS_READ,
        )
        open_rows = [s for s in sessions if s.status != EXPIRED]
        lapsed_rows = [
            s for s in sessions
            if s.status == EXPIRED and s.expired_at is not None
            and now - s.expired_at <= caps.lapsed_visible
        ]
        if not used and not open_rows and not lapsed_rows:
            return None
        state = await self._state(scope)
        machine_read = state is not None
        tmux = state.tmux if state is not None else None
        processes = state.processes if state is not None else None
        # A tmux job or ANY process the room's user owns (a `nohup … &` job
        # has no tmux session). None: unknown, never «none running».
        running = anything_running(state) if state is not None else None
        cutoff = now - timedelta(days=caps.recent_file_days)
        files = tuple(f for f in state.files if f.mtime >= cutoff) if state is not None else ()

        async def line(s: ComputerSession) -> SessionLine:
            shown = shown_status(s, now=now, idle_after=caps.idle_after,
                                 lapsed_visible=caps.lapsed_visible,
                                 tmux_running=running if s.status != EXPIRED else False)
            try:
                mine = await self._events.for_session(session_id=s.id,
                                                      limit=SESSION_EVENTS_READ)
                progress = derive_progress(mine, capped=len(mine) >= SESSION_EVENTS_READ)
            except Exception:
                log.exception("computer session progress read failed")
                progress = None
            return SessionLine(session=s, shown=shown, progress=progress)

        # Progress is read only for the sessions the caps will show, all at once.
        shown_open = open_rows[: caps.max_open_sessions]
        shown_lapsed = lapsed_rows[: caps.max_lapsed_sessions]
        lines = await asyncio.gather(*(line(s) for s in (*shown_open, *shown_lapsed)))
        open_lines = (*lines[: len(shown_open)],
                      *(SessionLine(s, "active", None)
                        for s in open_rows[caps.max_open_sessions:]))
        lapsed_lines = (*lines[len(shown_open):],
                        *(SessionLine(s, "lapsed", None)
                          for s in lapsed_rows[caps.max_lapsed_sessions:]))
        return ComputerView(
            now=now, caps=caps, open_sessions=tuple(open_lines),
            lapsed_sessions=tuple(lapsed_lines),
            tmux=tmux, processes=processes,
            processes_truncated=state.processes_truncated if state is not None else False,
            files=files, runs=_runs(rows), links=_links(rows),
            machine_read=machine_read,
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


def _links(rows: list[ComputerEvent]) -> tuple[LinkLine, ...]:
    """The room's portal links from `computer_export`'s rows, newest first, one
    per artifact (its newest export): only an export that answered `ok`
    with a link, so a refused or failed one never shows as a link."""
    started = {r.operation_id: r for r in rows if r.phase == "started"}
    out: list[LinkLine] = []
    seen: set[str] = set()
    for r in rows:  # newest first already
        if r.tool != "computer_export" or r.phase != "finished" or r.status != "ok":
            continue
        artifact_id, url = r.result.get("artifact_id"), r.result.get("url")
        if not isinstance(artifact_id, str) or not isinstance(url, str) or artifact_id in seen:
            continue
        seen.add(artifact_id)
        asked = started.get(r.operation_id)
        path = asked.request.get("path") if asked is not None else None
        out.append(LinkLine(
            artifact_id=artifact_id, url=url, title=str(r.result.get("title") or ""),
            path=path if isinstance(path, str) else "",
            revised=r.result.get("revised") is True, at=r.finished_at or r.created_at,
        ))
    return tuple(out)
