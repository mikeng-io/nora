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
from typing import Any

from nora.domain.computer import SandboxStatus, StateResult, anything_running, computer_unit_scope
from nora.domain.computer_context import (
    ComputerContextCaps,
    ComputerView,
    LinkLine,
    RunLine,
    SessionLine,
    TabLine,
    TaskLine,
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
from nora.domain.computer_tasks import ComputerTask
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
        # The canonical scope resolver the whole scoped feature shares
        # ([computer.scoped]): `(platform, conversation_id, conversation_type)`
        # → the workspace key — a group to its unit, a DM to its `(person,
        # anchor unit)` scope. When set it WINS over `unit_for`, so the block
        # a DM room renders is its own scope's, never a group unit's.
        scope_for: Callable[[str, str, str], str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        # The scoped runtime's queue and tab stores ([computer.scoped]):
        # when wired, the block carries queued/running tasks and task-owned
        # tabs. `None` keeps the shared-VM block exactly as before.
        tasks: Any = None, tabs: Any = None,
        browser_idle_after: timedelta | None = None,
    ) -> None:
        self._unit_for = unit_for
        self._scope_for = scope_for
        self._sessions = sessions
        self._events = events
        self._runtime = runtime
        self._caps = caps
        self._clock = clock
        self._metrics = metrics
        self._monotonic = monotonic
        self._tasks = tasks
        self._tabs = tabs
        self._browser_idle_after = browser_idle_after
        # scope -> (read at monotonic, a good read, the read's own time).
        # One process, rooms in the tens, entries overwritten per room:
        # bounded by the rooms that use it.
        self._cache: dict[str, tuple[float, StateResult, datetime]] = {}
        #: scope -> observation number: how many blocks this scope's reads
        #: have built — the block's revision mark.
        self._revisions: dict[str, int] = {}

    def _inc(self, name: str, **labels: object) -> None:
        inc = getattr(self._metrics, "inc", None)
        if inc is not None:
            inc(name, **labels)

    async def _state(self, scope: str) -> tuple[StateResult, datetime] | None:
        """The room's machine state AND the time it was read, within the
        read budget, from the cache when fresh; None when it could not be
        read in time or at all. A cached hit stamps the read it serves,
        never this turn's clock."""
        hit = self._cache.get(scope)
        if hit is not None and self._monotonic() - hit[0] < self._caps.state_cache_seconds:
            return hit[1], hit[2]
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
        read_at = self._clock()
        self._cache[scope] = (self._monotonic(), state, read_at)
        return state, read_at

    def invalidate(self, scope: str) -> None:
        """Drop a cached machine read: a mutating segment landed on `scope`,
        so the next turn's block re-reads, not serves what it had."""
        self._cache.pop(scope, None)

    async def read(self, *, platform: str, conversation_id: str,
                   conversation_type: str = "group") -> ComputerView | None:
        try:
            return await self._read(platform, conversation_id, conversation_type)
        except Exception:
            log.exception("computer context read failed; rendering no block")
            self._inc("nora_errors_total", stage="computer_context", platform=platform)
            return None

    def _scope(self, platform: str, conversation_id: str,
               conversation_type: str) -> str:
        if self._scope_for is not None:
            try:
                return self._scope_for(platform, conversation_id, conversation_type) or ""
            except Exception:
                log.exception("computer scope resolution failed")
                return ""
        unit = self._unit_for(platform, conversation_id) if self._unit_for else None
        return computer_unit_scope(unit) if unit else ""

    async def _read(self, platform: str, conversation_id: str,
                    conversation_type: str) -> ComputerView | None:
        scope = self._scope(platform, conversation_id, conversation_type)
        if not scope:
            return None
        now = self._clock()
        caps = self._caps
        rows = await self._events.for_scope(
            scope=scope, since=now - timedelta(days=caps.active_days), limit=EVENTS_READ,
        )
        used = any(r.actor != "sweeper" for r in rows)
        task_rows: list[ComputerTask] = []
        if self._tasks is not None:
            try:
                task_rows = await self._tasks.open_for_scope(scope, limit=8)
            except Exception:
                log.exception("computer tasks read failed")
                task_rows = []
        # Housekeeping is not the room's work: a `maintenance` segment (the
        # browser lifecycle sweep claiming the scope's owner) is neither a
        # task the model can act on nor proof of activity, so it never
        # renders and never alone keeps the block alive.
        task_rows = [t for t in task_rows if t.kind != "maintenance"]
        sessions = await self._sessions.for_scope(
            scope=scope, limit=SESSIONS_READ,
        )
        open_rows = [s for s in sessions if s.status != EXPIRED]
        lapsed_rows = [
            s for s in sessions
            if s.status == EXPIRED and s.expired_at is not None
            and now - s.expired_at <= caps.lapsed_visible
        ]
        # A live queue row IS activity: a room with a running segment sees
        # the block even before its first audit event lands.
        if not used and not task_rows and not open_rows and not lapsed_rows:
            return None
        got = await self._state(scope)
        state, state_at = got if got is not None else (None, None)
        machine_read = got is not None
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
        tasks: tuple[TaskLine, ...] | None = None
        tabs: tuple[TabLine, ...] | None = None
        if self._tasks is not None:
            try:
                tasks = tuple(TaskLine(
                    # The FULL id: `task=` on computer_browse/capture/cancel
                    # parses the whole UUID — a cut handle answers not-a-task.
                    task_id=str(t.id), kind=t.kind, status=t.status,
                    created=t.created_at,
                    note=cut(str(t.result.get("note") or t.result.get("reason") or ""),
                             60)) for t in task_rows)
            except Exception:
                log.exception("computer tasks read failed")
                tasks = None
        instance: str | None = None
        instance_read = getattr(self._runtime, "instance", None)
        if instance_read is not None:
            try:
                instance = await instance_read(scope)
            except Exception:  # noqa: BLE001 - an unreadable identity is absent, never raised
                instance = None
        if self._tabs is not None:
            try:
                tab_rows = await self._tabs.for_scope(scope, limit=8)
                idle = self._browser_idle_after
                tabs = tuple(TabLine(
                    task_id=str(t.task_id), url=cut(t.last_url, 80),
                    title=cut(t.last_title, 60),
                    # A row bound to a DEAD instance is expired whatever the
                    # sweep has had time to write — the block renders the
                    # live instance's tabs only.
                    status=("expired" if t.status == "open" and instance is not None
                            and t.instance != instance else t.status),
                    via=t.via,
                    last_activity=t.last_activity_at,
                    expires_in=((t.last_activity_at + idle - now).total_seconds()
                                if idle is not None and t.status == "open"
                                and (instance is None or t.instance == instance)
                                else None),
                ) for t in tab_rows)
            except Exception:
                log.exception("browser tabs read failed")
                tabs = None
        self._revisions[scope] = self._revisions.get(scope, 0) + 1
        return ComputerView(
            now=now, caps=caps, open_sessions=tuple(open_lines),
            lapsed_sessions=tuple(lapsed_lines),
            tmux=tmux, processes=processes,
            processes_truncated=state.processes_truncated if state is not None else False,
            files=files, runs=_runs(rows), links=_links(rows),
            tasks=tasks, tabs=tabs, instance=instance, state_at=state_at,
            revision=self._revisions[scope],
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
