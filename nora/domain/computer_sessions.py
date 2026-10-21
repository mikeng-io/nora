"""Computer sessions: one task in progress on Nora's computer, across turns.

**A session is DERIVED BY CODE; no model writes one.** An optional update
tool would be skipped, because a model rarely performs an optional act. So:

- **Open.** A computer tool call in a room with no open session opens one,
  in the same tool path, before its `started` audit row. At most one newly opened session
  is open per unit (scope), which a partial unique index enforces
  (migration `0082`).
- **Continue.** Any computer call in that room while it is open continues
  it: `last_activity_at` and `updated_at` move, and the call's audit rows
  carry its `session_id`.
- **Objective.** The text of the message that started the turn, cut to
  `OBJECTIVE_CHARS`, plus the turn's tag id (`created_by_tag`) always.
- **Progress is never stored.** It is derived when read, from the session's
  own `computer_events` rows (`derive_progress`), so there is no summary to
  go stale and nothing the rows already hold is copied.
- **Status.** Stored: `open` or `expired`. Shown (`shown_status`): `active`
  while there was activity within the idle window or a tmux job in the room
  is running, `idle` otherwise, `lapsed` for a while after the sweeper
  expires it, then `expired`. A non-zero exit is shown in the progress,
  never as a status: nothing deterministic knows a task failed.
- **Expiry** is the sweeper's (`nora.app.runtime.computer_sweep_loop`), a
  compare-and-set on `last_activity_at`, so a call that lands between the
  sweeper's read and its write keeps the session open. The next call after
  an expiry opens a NEW session; there is no resume verb, and a lapsed
  session is only shown.
- **Nothing is ever deleted**: not a session row, not a file, not an audit
  row.

Every datetime crossing this port is UTC-aware.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from nora.domain.computer_events import ComputerEvent, ensure_utc_event
from nora.domain.prompt import ago

__all__ = [
    "EXPIRED",
    "EXPIRY_REASONS",
    "OPEN",
    "STORED_STATUSES",
    "ComputerSession",
    "ComputerSessionStore",
    "SessionProgress",
    "ShownStatus",
    "age",
    "cut",
    "derive_progress",
    "ensure_utc_event",
    "normalise_session",
    "progress_text",
    "shown_status",
]

#: The stored states. `open` is live; `expired` is the sweeper's, final.
OPEN = "open"
EXPIRED = "expired"
STORED_STATUSES: frozenset[str] = frozenset({OPEN, EXPIRED})

#: Why the sweeper expired a session (`ComputerSession.expired_reason`).
#: `idle`: no computer call for the idle expiry and no tmux job running.
#: `jobs_ended`: every tmux session it was seen holding is gone and the
#: room is idle. `running_past_limit`: a tmux job still runs, but nothing
#: has called the computer for `max_running_days`; the job is flagged in
#: the audit row and NOT killed.
EXPIRY_REASONS: frozenset[str] = frozenset({"idle", "jobs_ended", "running_past_limit"})

#: What a reader is shown (`shown_status`).
#: `running unknown`: open, no recent call, and whether a job still runs
#: could not be read; never shown as `idle` then (unknown is not none).
ShownStatus = Literal["active", "idle", "running unknown", "lapsed", "expired"]

#: The objective is the triggering message, cut to this many characters.
OBJECTIVE_CHARS = 80
#: A command in the progress line, cut to this many characters.
COMMAND_CHARS = 80
#: The most file paths a progress line names.
PROGRESS_FILES = 5

_WS = re.compile(r"\s+")


def cut(text: str, limit: int) -> str:
    """`text` on one line, at most `limit` characters, `…` where it was cut."""
    flat = _WS.sub(" ", text).strip()
    return flat if len(flat) <= limit else flat[: max(limit - 1, 0)].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ComputerSession:
    """One task on the computer, with its creation room as provenance.

    `stopped_at_detail` holds what the session was seen holding, written by
    the sweeper: `{"tmux": [names]}` today (a path and a browser URL are
    reserved for S3 and S4). `actor` is who opened it (the `computer_events`
    vocabulary); `created_by_tag` the tag id of the turn that opened it,
    empty outside a tag."""

    id: UUID
    platform: str
    conversation_id: str
    scope: str
    objective: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    actor: str
    created_by_tag: str
    expired_at: datetime | None = None
    expired_reason: str | None = None
    stopped_at_detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STORED_STATUSES:
            raise ValueError(f"unknown computer session status {self.status!r}")
        if (self.status == EXPIRED) != (self.expired_at is not None):
            raise ValueError("an expired session has expired_at; an open one has none")
        if self.expired_reason is not None and self.expired_reason not in EXPIRY_REASONS:
            raise ValueError(f"unknown expiry reason {self.expired_reason!r}")

    @property
    def workspace_scope(self) -> str:
        """Migrated sessions retain their original scope as provenance."""
        return str(self.stopped_at_detail.get("workspace_scope", self.scope))

    @property
    def resume_prefix(self) -> str:
        return str(self.stopped_at_detail.get("resume_prefix", ""))

    @property
    def held_tmux(self) -> tuple[str, ...]:
        raw = self.stopped_at_detail.get("tmux")
        return tuple(n for n in raw if isinstance(n, str)) if isinstance(raw, list) else ()


def normalise_session(session: ComputerSession) -> ComputerSession:
    return replace(
        session,
        created_at=ensure_utc_event(session.created_at, field="created_at"),
        updated_at=ensure_utc_event(session.updated_at, field="updated_at"),
        last_activity_at=ensure_utc_event(session.last_activity_at, field="last_activity_at"),
        expired_at=(
            None if session.expired_at is None
            else ensure_utc_event(session.expired_at, field="expired_at")
        ),
        stopped_at_detail=dict(session.stopped_at_detail),
    )


def shown_status(
    session: ComputerSession, *, now: datetime, idle_after: timedelta,
    lapsed_visible: timedelta, tmux_running: bool | None,
) -> ShownStatus:
    """What a reader is told the session is. `tmux_running` is whether a
    tmux job in the session's room is running now, None when the machine
    could not be read: an open session with no recent call is then
    `running unknown`, never `idle`."""
    if session.status == EXPIRED:
        expired_at = session.expired_at or session.updated_at
        return "lapsed" if now - expired_at <= lapsed_visible else "expired"
    if now - session.last_activity_at <= idle_after or tmux_running:
        return "active"
    return "running unknown" if tmux_running is None else "idle"


@dataclass(frozen=True)
class SessionProgress:
    """Where a session has got to, derived from its own audit rows.

    `runs` counts `computer_run` calls (`runs_capped`: the rows read were at
    their cap, so there may be more). `last_exit_code` is the newest run's,
    None when it did not finish. `files` are the paths written or edited,
    newest first. `last_activity_at` is the newest row's time."""

    runs: int = 0
    runs_capped: bool = False
    last_command: str = ""
    last_exit_code: int | None = None
    files: tuple[str, ...] = ()
    last_activity_at: datetime | None = None


def derive_progress(events: Sequence[ComputerEvent], *, capped: bool = False) -> SessionProgress:
    """A session's progress from its `computer_events` rows (any order).
    Tool calls only: a lease or a session transition row is not progress."""
    started = {e.operation_id: e for e in events if e.phase == "started"}
    finished = {e.operation_id: e for e in events if e.phase == "finished"}
    runs = sorted(
        (e for e in started.values() if e.tool == "computer_run"),
        key=lambda e: e.started_at, reverse=True,
    )
    last_command, last_exit = "", None
    if runs:
        newest = runs[0]
        last_command = cut(str(newest.request.get("command", "")), COMMAND_CHARS)
        done = finished.get(newest.operation_id)
        raw_exit = done.result.get("exit_code") if done is not None else None
        last_exit = raw_exit if isinstance(raw_exit, int) and not isinstance(raw_exit, bool) \
            else None
    files: list[str] = []
    for e in sorted(started.values(), key=lambda e: e.started_at, reverse=True):
        if e.tool not in ("computer_write", "computer_edit"):
            continue
        done = finished.get(e.operation_id)
        path = e.request.get("path")
        if done is None or done.status != "ok" or not isinstance(path, str):
            continue
        if path not in files:
            files.append(path)
        if len(files) >= PROGRESS_FILES:
            break
    tool_rows = [e for e in events if e.tool.startswith("computer_")
                 and e.tool not in ("computer_session", "computer_lease")]
    last = max((e.created_at for e in tool_rows), default=None)
    return SessionProgress(
        runs=len(runs), runs_capped=capped, last_command=last_command,
        last_exit_code=last_exit, files=tuple(files), last_activity_at=last,
    )


def age(then: datetime, now: datetime) -> str:
    """How long before `now` `then` was, as a person says it («12 min ago»)."""
    return ago(int((now - then).total_seconds() * 1000))


def progress_text(progress: SessionProgress, *, now: datetime) -> str:
    """A session's progress on one line: runs and the last one's exit, the
    last command, the files changed, and the last activity's age. A non-zero
    exit is said as a failed run; it is never a status."""
    parts: list[str] = []
    if progress.runs:
        count = f"{progress.runs}{'+' if progress.runs_capped else ''} run"
        count += "" if progress.runs == 1 else "s"
        if progress.last_exit_code is None:
            parts.append(f"{count}, the last did not finish")
        elif progress.last_exit_code != 0:
            parts.append(f"{count}, last run failed, exit {progress.last_exit_code}")
        else:
            parts.append(f"{count}, last exit 0")
        if progress.last_command:
            parts.append(f"last command `{progress.last_command}`")
    else:
        parts.append("no commands run")
    if progress.files:
        parts.append("changed " + ", ".join(progress.files))
    if progress.last_activity_at is not None:
        parts.append(f"last active {age(progress.last_activity_at, now)}")
    return "; ".join(parts)


@runtime_checkable
class ComputerSessionStore(Protocol):
    """`computer_sessions` (migration `0082`). Implementations:
    `nora.infra.sql.computer_sessions.SqlComputerSessionStore` and
    `nora.testing.FakeComputerSessionStore`, both held to
    `tests/contract/test_computer_session_store_contract.py`. MAY raise on a
    storage failure; the caller decides what that costs."""

    async def continue_or_open(
        self, *, platform: str, conversation_id: str, scope: str, objective: str,
        actor: str, tag_id: str, now: datetime, open_new: bool = True,
    ) -> tuple[ComputerSession, bool] | None:
        """The room's open session, its `last_activity_at` and `updated_at`
        moved to `now`; or, when there is none and `open_new`, a new one
        opened with `objective`. True when it was opened by this call. None
        when there is none and `open_new` is false: a background actor only
        continues a session a live turn opened (design §15.4). Atomic: two
        concurrent calls in one unit get ONE session between them. Migrated
        room tasks remain separate; reuse the most recently active open task."""
        ...

    async def open_sessions(self, *, limit: int) -> list[ComputerSession]:
        """Every open session, the longest idle first: the sweeper's read."""
        ...

    async def for_scope(
        self, *, scope: str, limit: int,
    ) -> list[ComputerSession]:
        """The unit workspace's sessions, including migrated room sessions,
        open and expired, the most recently active first."""
        ...

    async def note_resources(
        self, session_id: UUID, *, tmux: Sequence[str], now: datetime,
    ) -> None:
        """Add tmux session names to what an OPEN session was seen holding
        (`stopped_at_detail["tmux"]`). Leaves `last_activity_at` alone: being
        seen is not activity. A no-op for an expired session."""
        ...

    async def expire(
        self, session_id: UUID, *, seen_last_activity_at: datetime, reason: str,
        now: datetime,
    ) -> ComputerSession | None:
        """Compare-and-set to `expired`: only when the session is still open
        AND its `last_activity_at` is still `seen_last_activity_at`. The
        expired row, or None when anything moved first (a call continued it,
        or another sweep expired it). Never deletes."""
        ...
