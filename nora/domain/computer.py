"""Nora's computer: the result types of `SandboxRuntime` and the
one rule that turns a room into a workspace.

Every result carries a `status` in R-27's vocabulary plus `denied`:

- `ok`: done. A command that exited non-zero is still `ok`; its `exit_code`
  says how it went.
- `nothing_found`: the thing asked about is not there (a file, a match).
- `denied`: refused as asked (a path outside the workspace, an ambiguous
  edit, an oversized body, a bad token).
- `unavailable`: could not be done now (the computer is down, a timeout, an
  OS error). Never an empty success.

`reason` explains anything that is not `ok`, in words a model can act on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class SandboxStatus(StrEnum):
    OK = "ok"
    NOTHING_FOUND = "nothing_found"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


#: The operations a `SandboxRuntime` makes against computerd, the `op` label of
#: `nora_computer_calls_total`. One name per port method, so a counter reads in
#: the port's own words. `ComputerdRuntime` records each call under exactly one
#: of these, and `tests/unit/test_computer_metrics.py` checks that every method
#: it has is named here: a new op that is not listed would count under a label
#: the eager series never created, which is the lazy-label trap.
COMPUTER_OPS: tuple[str, ...] = (
    "run", "read", "write", "edit", "files", "fetch", "put", "state",
)

#: `nora_computer_audit_failures_total`'s `stage` label: where the audit write
#: failed (see that counter's help text). Read by the eager creation of the
#: series and checked against every call site by
#: `tests/architecture/test_computer_metrics_are_primed.py`.
COMPUTER_AUDIT_STAGES: tuple[str, ...] = (
    "blob", "row", "session", "lease", "export", "media",
)


class ComputerCallObserver(Protocol):
    """What `ComputerdRuntime` and the computer probe tell the outside about
    computerd, without importing the metrics layer (`nora.infra` sits below
    `nora.obs`).

    `answered` is the one distinction that matters for an outage: computerd
    replied with an HTTP 200 and a body (it is there, and Nora can use it, even
    if the answer is `unavailable` or `denied`), versus nothing usable came back
    (refused, timed out, a non-200 such as a 401 after a token rotation). A call
    that answered with `unavailable` is a thing computerd said; one that did
    not answer is Nora not being able to reach her computer at all.
    """

    def call(self, op: str, status: str, answered: bool) -> None: ...

    def probe(self, answered: bool) -> None: ...


def computer_scope(platform: str, conversation_id: str) -> str:
    """The workspace key for one room: a hash, so no platform id ever reaches
    the VM, and chosen by Nora's code from the turn, never by the model."""
    digest = hashlib.sha256(f"{platform}:{conversation_id}".encode()).hexdigest()
    return digest[:32]


@dataclass(frozen=True)
class RunResult:
    status: SandboxStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    duration_ms: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class FileResult:
    status: SandboxStatus
    content: str = ""
    binary: bool = False
    size: int | None = None
    sha256: str | None = None
    truncated: bool = False
    lines_total: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int


@dataclass(frozen=True)
class ListResult:
    status: SandboxStatus
    entries: tuple[FileEntry, ...] = field(default_factory=tuple)
    truncated: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class BytesResult:
    """`sha256` is computed by the CALLER's side on the bytes it received,
    never taken from the VM's claim."""

    status: SandboxStatus
    data: bytes = b""
    sha256: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BrowseOutcome:
    """What one delegated browser task came back with.
    `status` is the R-27 shape the tool reports: `ok` (done, with
    an `answer`), `nothing_found` (the pages were read and the thing asked
    for is not there), `unavailable` (the browser could not be used, the
    budget ran out, or a person must take over: `reason` says which, and
    `needs_human` is set for the last). `steps` counts the browser actions
    taken; `url` is where the page ended."""

    status: str
    answer: str = ""
    reason: str = ""
    url: str = ""
    steps: int = 0
    needs_human: bool = False


@dataclass(frozen=True)
class OpResult:
    """`sha256` is what the file holds after the write; `previous_sha256`
    what it held just before (None: there was no regular file there). The
    pair is the audit's before and after."""

    status: SandboxStatus
    size: int | None = None
    sha256: str | None = None
    reason: str | None = None
    previous_sha256: str | None = None


@dataclass(frozen=True)
class StateFile:
    """One recently changed file in a scope: `mtime` is UTC-aware."""

    path: str
    size: int
    mtime: datetime


@dataclass(frozen=True)
class TmuxSession:
    """One tmux session in a scope. `running` is true when a pane has a
    foreground command that is not a shell at its prompt: a job, as opposed
    to a shell left open. `created` is UTC-aware."""

    name: str
    created: datetime
    attached: bool
    running: bool


@dataclass(frozen=True)
class ScopeProcess:
    """One process owned by a scope's user, read from `/proc` by computerd:
    a job started any way at all (`nohup … &`, a server, a tmux pane's
    child). `command` is cut to 80 characters; `started` is UTC-aware and
    `elapsed_seconds` is how long it had run when read."""

    pid: int
    command: str
    started: datetime
    elapsed_seconds: int


@dataclass(frozen=True)
class StateResult:
    """What the computer holds for one scope, read fresh:
    the newest files, newest first, and the tmux sessions.

    `tmux` is None when the sessions could not be listed (`tmux_reason` says
    why). A reader must take None as UNKNOWN, never as «nothing is running»:
    the sweeper skips a scope it cannot see rather than expire a session a
    live job still holds. `files_truncated` says the walk stopped at its cap,
    so a newer file may be missing. `processes` is the same reading for
    processes (see `anything_running`)."""

    status: SandboxStatus
    files: tuple[StateFile, ...] = ()
    files_truncated: bool = False
    tmux: tuple[TmuxSession, ...] | None = None
    tmux_reason: str | None = None
    reason: str | None = None
    #: Every process the scope's user owns, newest first, at most 20
    #: (`processes_truncated` when there were more). None when they could not
    #: be read (`processes_reason`): unknown, never «none running».
    processes: tuple[ScopeProcess, ...] | None = None
    processes_truncated: bool = False
    processes_reason: str | None = None


def anything_running(state: StateResult) -> bool | None:
    """Whether the scope is running a job: a tmux pane with one, or ANY
    process its user owns (a `nohup … &` job has no tmux session at all).
    True as soon as either source shows one; None when neither shows one and
    either could not be read (unknown is never «nothing running»)."""
    tmux_running = state.tmux is not None and any(t.running for t in state.tmux)
    if tmux_running or state.processes:
        return True
    if state.tmux is None or state.processes is None:
        return None
    return False


@dataclass(frozen=True)
class ComputerToolSettings:
    """The computer tools' bounds, as a DOMAIN value: `nora.agent` may not
    import `nora.config`, so the composition root converts `[computer]` into
    this once (the `ReminderCaps` shape).

    `limits` is the reserved per-command resource slot as `(name, value)`
    pairs, only the ones the operator set; empty (the shipped default) means
    nothing is sent. computerd ignores it today (the computer is semi-trusted).
    """

    run_timeout_seconds: int = 120
    max_run_timeout_seconds: int = 900
    max_output_chars: int = 8000
    max_read_chars: int = 20000
    # `computer_export` / `computer_import`: the largest file either moves.
    export_max_bytes: int = 25 * 1024 * 1024
    import_max_bytes: int = 25 * 1024 * 1024
    limits: tuple[tuple[str, int], ...] = ()
    # Sessions (`[computer.sessions]`): an open session with no computer call
    # for `idle_after_minutes` and no tmux job running is shown `idle`; an
    # expired one is shown `lapsed` for `lapsed_visible_hours`.
    idle_after_minutes: int = 30
    lapsed_visible_hours: int = 24
