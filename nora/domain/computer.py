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
    status: SandboxStatus
    size: int | None = None
    sha256: str | None = None
    reason: str | None = None
