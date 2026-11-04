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
class TaskLine:
    """One `computer_tasks` row as the block shows it ([computer.scoped]):
    the task's short id (what `task=` on `computer_browse`/`computer_capture`
    and `computer_cancel` take), its kind, its state — `queued`, `running`
    or a terminal word — when it was created, and how it ended (`note`)."""

    task_id: str
    kind: str
    status: str
    created: datetime
    note: str = ""


@dataclass(frozen=True)
class TabLine:
    """One `browser_tasks` row as the block shows it: which task owns the
    tab, its last observed url/title (untrusted text), whether it is open or
    expired, and when it was last touched. `expires_in` seconds left before
    the idle sweep retires it (negative: already due)."""

    task_id: str
    url: str
    title: str
    status: str
    via: str
    last_activity: datetime
    expires_in: float | None = None


@dataclass(frozen=True)
class ComputerView:
    """Everything one render needs. Sessions and runs are newest first and
    NOT yet capped: the renderer applies the caps, so it can say how many it
    left out. `tmux` is None when the machine could not be read or could not
    list tmux; `files` are already limited to `recent_file_days`. `tasks`
    and `tabs` are the scoped runtime's queue/tab state — `None` on the
    shared-VM shape (no `computer_tasks`/`browser_tasks` stores wired)."""

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
    tasks: tuple[TaskLine, ...] | None = None
    tabs: tuple[TabLine, ...] | None = None
    #: The VM instance the block's state and tabs were verified against —
    #: typed provenance: an `instance` change means every handle in this
    #: block was minted on a dead VM.
    instance: str | None = None
    #: When the machine state was actually read — its own observation time,
    #: never the turn's: a cached state stamps the read it serves, so the
    #: block can never show a fresh "checked just now" on a stale answer.
    state_at: datetime | None = None
    #: The reader's per-scope observation counter: it moves on every read
    #: this block was built from — two blocks carrying the same number saw
    #: the same observation.
    revision: int = 0
    machine_read: bool = True
