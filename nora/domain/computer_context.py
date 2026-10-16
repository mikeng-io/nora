"""What the `<computer>` block is built from.

**Not a model's fold.** A deterministic window over two sources, read fresh
at the start of every tag and fire turn: the live machine (the `state`
endpoint: recent files, tmux) and the database (`computer_sessions`, and
`computer_events` for recent commands and each session's progress). It is
rebuilt each turn, so it cannot go stale, and nothing in it is written by a
model. `nora.engagement.computer_context.ComputerContextReader` reads it;
`nora.language.computer_render.render_computer` renders it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from nora.domain.computer import ScopeProcess, StateFile, TmuxSession
from nora.domain.computer_sessions import ComputerSession, SessionProgress, ShownStatus


@dataclass(frozen=True)
class ComputerContextCaps:
    """`[computer.context]` plus the session windows the block reads with.
    `max_tokens` is the block BODY's ceiling, estimated conservatively."""

    active_days: int = 7
    recent_file_days: int = 7
    max_open_sessions: int = 5
    max_lapsed_sessions: int = 3
    max_tmux: int = 5
    max_processes: int = 5
    max_files: int = 8
    max_runs: int = 5
    max_links: int = 3
    max_tokens: int = 400
    read_timeout_seconds: float = 1.5
    state_cache_seconds: float = 30.0
    idle_after: timedelta = timedelta(minutes=30)
    lapsed_visible: timedelta = timedelta(hours=24)


@dataclass(frozen=True)
class SessionLine:
    """One session as the block shows it. `progress` is None when its audit
    rows could not be read."""

    session: ComputerSession
    shown: ShownStatus
    progress: SessionProgress | None


@dataclass(frozen=True)
class RunLine:
    """One recent `computer_run`: its command (cut), its exit code (None:
    it did not finish, or has not yet), and when it started."""

    command: str
    exit_code: int | None
    finished: bool
    at: datetime


@dataclass(frozen=True)
class LinkLine:
    """One portal link the room made with `computer_export(to="link")`, from
    its two audit rows: the link's `artifact_id` (what a revision passes to
    keep the same link), its `url`, the title and workspace `path` it was
    made from, whether this was a revision, and when."""

    artifact_id: str
    url: str
    title: str
    path: str
    revised: bool
    at: datetime


@dataclass(frozen=True)
class ComputerView:
    """Everything one render needs. Sessions and runs are newest first and
    NOT yet capped: the renderer applies the caps, so it can say how many it
    left out. `tmux` is None when the machine could not be read or could not
    list tmux; `files` are already limited to `recent_file_days`."""

    now: datetime
    caps: ComputerContextCaps
    open_sessions: tuple[SessionLine, ...] = ()
    lapsed_sessions: tuple[SessionLine, ...] = ()
    tmux: tuple[TmuxSession, ...] | None = None
    #: Every process the room's user owns, newest first; None: not read.
    processes: tuple[ScopeProcess, ...] | None = None
    processes_truncated: bool = False
    files: tuple[StateFile, ...] = ()
    runs: tuple[RunLine, ...] = ()
    links: tuple[LinkLine, ...] = ()
    machine_read: bool = True
