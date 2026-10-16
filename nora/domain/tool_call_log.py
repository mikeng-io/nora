"""The tool-recall log port. One row per
successful call to a `cacheable=True` tool (`nora.agent.tools.NativeTool`),
scoped to one conversation — read back only by `recall_lookups`, itself
scoped to the same conversation. Never cross-conversation, ever.

Every datetime crossing this port is UTC-aware, the same rule
`nora.domain.compute`'s module docstring states for `ComputeJobStore` — a
separate local rule/helper here, not imported from that module, since the
two ports are independent even though the rule is identical.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


class NaiveToolCallLogDatetimeError(ValueError):
    """A datetime reached `ToolCallLogStore` without a timezone."""


def ensure_utc_log(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveToolCallLogDatetimeError(
            f"{field} must be timezone-aware; got naive {value!r}."
        )
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ToolCallLogEntry:
    """One cacheable tool's successful call, logged for later reuse.

    `tag_id` is the transcript anchor this call happened under — given a
    row, you can find which turn produced it, bounded by however long that
    turn's own row survives `messages`'s own pruning.
    `tool_input`/`tool_result` are the tool's own arguments and return
    value; `recall_lookups` hands them back to the model exactly
    as stored, never re-summarised.
    """

    id: UUID
    conversation_id: str
    conversation_type: str
    tag_id: str
    tool_name: str
    tool_input: dict[str, Any]
    tool_result: dict[str, Any]
    created_at: datetime


def normalise_tool_call_log_entry(entry: ToolCallLogEntry) -> ToolCallLogEntry:
    return replace(entry, created_at=ensure_utc_log(entry.created_at, field="created_at"))


@runtime_checkable
class ToolCallLogStore(Protocol):
    async def append(self, entry: ToolCallLogEntry) -> None:
        """Insert-only. Never called for a vetoed call or a call whose own
        `status` was not `"ok"` (R-48) — that discipline lives in the
        WRITER (`nora.agent.guards.ToolCallLog`), not here; this
        port stores exactly what it's given."""
        ...

    async def recent(
        self, *, conversation_id: str, since: datetime, limit: int,
    ) -> list[ToolCallLogEntry]:
        """The last `limit` entries for `conversation_id` with `created_at
        >= since`, newest first. `since` is the caller's job to compute
        (`recall_lookups` passes `now() - 7 days`) — this port
        takes an absolute timestamp, never a relative window, so a fake
        clock in a test needs no special-casing here."""
        ...
