"""The computer audit log port (design/sandbox-runtime §12). One row per call
to a computer tool (`computer_run`, `computer_read`, `computer_write`,
`computer_edit`, `computer_list`), on EVERY return path, `unavailable`
included: what ran, in which room, under which turn, and how it answered.

It is the operator's one required control over the computer (2026-09-30:
semi-trusted, no resource limits applied). Any claim about what ran on the
computer re-derives from these rows, never from the model's account of it.

Written by Nora's tools, never by `computerd`: the VM is the less-trusted
side, so its own account of what it ran would be the producer attesting to
itself.

What a row does NOT hold: a command's full output. `detail` keeps the
command text in full (it IS the audit), the output's length, and the first
`AUDIT_OUTPUT_HEAD_CHARS` characters of stdout and stderr.

Every datetime crossing this port is UTC-aware, the rule
`nora.domain.tool_call_log` states, kept as a separate local helper here for
the same reason that module gives: the two ports are independent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

#: How much of a command's stdout and of its stderr a row keeps. Enough to see
#: what a command did at a glance, never the whole output (which can be a
#: page of the web or a file's contents).
AUDIT_OUTPUT_HEAD_CHARS = 500


class NaiveComputerEventDatetimeError(ValueError):
    """A datetime reached `ComputerEventStore` without a timezone."""


def ensure_utc_event(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveComputerEventDatetimeError(
            f"{field} must be timezone-aware; got naive {value!r}."
        )
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ComputerEvent:
    """One computer tool call.

    `scope` is `nora.domain.computer.computer_scope(platform,
    conversation_id)`, the workspace the call touched, stored beside the room
    it was derived from so a row names both. `tag_id` is the turn; empty when
    the call ran outside a tag (no turn was bound). `exit_code` and
    `duration_ms` are `computer_run`'s and are `None` for every other tool
    and for a run that never started. `detail` is the tool's own facts: the
    command or path, lengths, `truncated`, `reason`, a write's sha256.
    """

    id: UUID
    created_at: datetime
    platform: str
    conversation_id: str
    conversation_type: str
    tag_id: str
    scope: str
    tool: str
    status: str
    exit_code: int | None = None
    duration_ms: int | None = None
    detail: Mapping[str, Any] | None = None


def normalise_computer_event(event: ComputerEvent) -> ComputerEvent:
    return replace(
        event, created_at=ensure_utc_event(event.created_at, field="created_at"),
        detail=dict(event.detail or {}),
    )


@runtime_checkable
class ComputerEventStore(Protocol):
    async def append(self, event: ComputerEvent) -> None:
        """Insert-only. The WRITER (the computer tools in
        `nora.agent.tools`) owns the one-row-per-call discipline and never
        lets a failed append cost the turn; this port stores what it is
        given, and MAY raise on a storage failure."""
        ...

    async def recent(
        self, *, conversation_id: str, since: datetime, limit: int,
    ) -> list[ComputerEvent]:
        """The last `limit` events for `conversation_id` with `created_at >=
        since`, newest first. Absolute `since`, the `ToolCallLogStore`
        shape, so a fake clock needs no special-casing."""
        ...
