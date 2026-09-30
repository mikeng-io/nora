"""Nora's computer: the result types of `SandboxRuntime` (design/sandbox-runtime
§3) and the one rule that turns a room into a workspace.

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


class SandboxStatus(StrEnum):
    OK = "ok"
    NOTHING_FOUND = "nothing_found"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


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
    never taken from the VM's claim (design/sandbox-runtime §6)."""

    status: SandboxStatus
    data: bytes = b""
    sha256: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class OpResult:
    """`sha256` is what the file holds after the write; `previous_sha256`
    what it held just before (None: there was no regular file there). The
    pair is the audit's before and after (design/sandbox-runtime §12)."""

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
class StateResult:
    """What the computer holds for one scope, read fresh (design/sandbox-runtime
    §15.1): the newest files, newest first, and the tmux sessions.

    `tmux` is None when the sessions could not be listed (`tmux_reason` says
    why). A reader must take None as UNKNOWN, never as «nothing is running»:
    the sweeper skips a scope it cannot see rather than expire a session a
    live job still holds. `files_truncated` says the walk stopped at its cap,
    so a newer file may be missing."""

    status: SandboxStatus
    files: tuple[StateFile, ...] = ()
    files_truncated: bool = False
    tmux: tuple[TmuxSession, ...] | None = None
    tmux_reason: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ComputerToolSettings:
    """The computer tools' bounds, as a DOMAIN value: `nora.agent` may not
    import `nora.config`, so the composition root converts `[computer]` into
    this once (the `ReminderCaps` shape).

    `limits` is the reserved per-command resource slot as `(name, value)`
    pairs, only the ones the operator set; empty (the shipped default) means
    nothing is sent. computerd ignores it today (operator 2026-09-30: the
    computer is semi-trusted).
    """

    run_timeout_seconds: int = 120
    max_run_timeout_seconds: int = 900
    max_output_chars: int = 8000
    max_read_chars: int = 20000
    limits: tuple[tuple[str, int], ...] = ()
