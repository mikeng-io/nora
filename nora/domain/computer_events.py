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

A row holds the FULL input (`request`) and points at the FULL output
(`output_ref`, blobs in the content-addressed `BlobStore`): the operator's
requirement is a complete trail, who, when, how and what, with Postgres as
the record of truth. The table is append-only in the database itself: a
trigger (migration `0080_computer_events`) refuses UPDATE and DELETE.

Every datetime crossing this port is UTC-aware, the rule
`nora.domain.tool_call_log` states, kept as a separate local helper here for
the same reason that module gives: the two ports are independent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

#: Who made a call (`ComputerEvent.actor`). `tag` is a turn somebody tagged
#: her into, `fire` a fire turn (a reminder, participation, a proactive act),
#: `dreamer:<adaptor>` a Dreamer pass. `cognition` and `operator` are reserved:
#: no path reaches the computer tools as either today.
ACTORS: frozenset[str] = frozenset({"tag", "fire", "cognition", "operator"})
DREAMER_ACTOR_PREFIX = "dreamer:"

#: How the call reached the computer (`ComputerEvent.path`). `direct` is a
#: tool call from Nora's own loop; `subagent` is reserved for S4 (a browsing
#: sub-agent acting on her behalf).
CALL_PATHS: frozenset[str] = frozenset({"direct", "subagent"})


def valid_actor(actor: str) -> bool:
    return actor in ACTORS or (
        actor.startswith(DREAMER_ACTOR_PREFIX) and len(actor) > len(DREAMER_ACTOR_PREFIX)
    )


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
    """One computer tool call: who, when, how and what.

    WHO: `actor` (see `ACTORS`), `requester_person_id` (the tagger's person
    id when there is one), the room (`platform`, `conversation_id`,
    `conversation_type`) and the turn (`tag_id`, empty outside a tag).
    WHEN: `started_at` just before the call reached the runtime,
    `finished_at` when its answer came back; `created_at` is when the row
    was written. HOW: `tool`, `path` (`CALL_PATHS`), and the OTel `trace_id`
    and `span_id` the call ran under (empty when none: the trace is a linked
    debug view, this row is the record). `scope` is the workspace touched.
    WHAT: `request` is the FULL input as the model gave it; `result` is how
    it answered (status, exit code, reason, truncated, sizes, sha256s, and
    for a write or an edit `before_sha256`/`after_sha256`); `output_ref`
    points at the full output held in the blob store (key and sha256 per
    stream), so the row stays small and nothing is dropped.
    """

    id: UUID
    created_at: datetime
    started_at: datetime
    finished_at: datetime
    actor: str
    requester_person_id: str | None
    platform: str
    conversation_id: str
    conversation_type: str
    tag_id: str
    scope: str
    tool: str
    path: str
    trace_id: str
    span_id: str
    status: str
    request: Mapping[str, Any] = field(default_factory=dict)
    result: Mapping[str, Any] = field(default_factory=dict)
    output_ref: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not valid_actor(self.actor):
            raise ValueError(f"unknown computer_events actor {self.actor!r}")
        if self.path not in CALL_PATHS:
            raise ValueError(f"unknown computer_events path {self.path!r}")


def normalise_computer_event(event: ComputerEvent) -> ComputerEvent:
    return replace(
        event,
        created_at=ensure_utc_event(event.created_at, field="created_at"),
        started_at=ensure_utc_event(event.started_at, field="started_at"),
        finished_at=ensure_utc_event(event.finished_at, field="finished_at"),
        request=dict(event.request), result=dict(event.result),
        output_ref=dict(event.output_ref),
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
