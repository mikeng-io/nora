"""The computer audit log port. Two rows per
call to a computer tool (`computer_run`, `computer_read`, `computer_write`,
`computer_edit`), on EVERY path, `unavailable` and `cancelled` included:
what ran, in which room, under which turn, and how it answered.

The two rows share an `operation_id`. The `started` row is written BEFORE
the call reaches the computer, and a tool refuses to run a call it could not
record; the `finished` row is written after, even when the turn is cancelled
mid-call. A `started` row with no `finished` row is the mark of a crash or a
restart mid-call (`ComputerEventStore.orphaned_starts`).

It is the operator's one required control over the computer (semi-trusted,
no resource limits applied). Any claim about what ran on the
computer re-derives from these rows, never from the model's account of it.

Written by Nora's tools, never by `computerd`: the VM is the less-trusted
side, so its own account of what it ran would be the producer attesting to
itself.

A row holds the FULL input (`request`) and points at the FULL output
(`output_ref`, blobs in the content-addressed `BlobStore`): the operator's
requirement is a complete trail, who, when, how and what, with Postgres as
the record of truth. A trigger (migration `0081_computer_events`) refuses
UPDATE, DELETE and TRUNCATE; it guards against bugs and mistaken statements,
not against the table's owner, who can drop it (see the migration).

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
#: `dreamer:<adaptor>` a Dreamer pass. `sweeper` is the session sweeper
#: (migration `0082`), which writes only the rows of the sessions it expires.
#: `subagent` is a background worker a tag turn launched (migration `0095`): it
#: runs under its own lease key and is never a live actor. `cognition` and
#: `operator` are reserved: no path reaches the computer tools as either.
ACTORS: frozenset[str] = frozenset({
    "tag", "fire", "cognition", "operator", "sweeper", "subagent",
})

#: The actors who wait for a turn's answer: a live turn. Every
#: other actor is background and never queues behind one.
LIVE_ACTORS: frozenset[str] = frozenset({"tag", "fire"})


def is_live_actor(actor: str) -> bool:
    return actor in LIVE_ACTORS
DREAMER_ACTOR_PREFIX = "dreamer:"

#: How the call reached the computer (`ComputerEvent.path`). `direct` is a
#: tool call from Nora's own loop; `subagent` is a call made by a background
#: worker acting on her behalf.
CALL_PATHS: frozenset[str] = frozenset({"direct", "subagent"})


#: The two rows of one operation (`ComputerEvent.phase`).
PHASES: frozenset[str] = frozenset({"started", "finished"})

#: The `status` of a `started` row, and of a `finished` row whose call was
#: cancelled (the turn's deadline) before it answered. A process shutdown cancels
#: the append itself, so it leaves an orphaned `started` row instead: the
#: crash marker `orphaned_starts` finds.
STARTED = "started"
CANCELLED = "cancelled"


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

    `operation_id` links the `started` and `finished` rows of one call;
    `phase` says which this is. A `started` row carries the full `request`
    and `finished_at=None`; a `finished` row carries `result`, `output_ref`
    and `finished_at`, and an empty `request`.

    WHO: `actor` (see `ACTORS`), `requester_person_id` (the tagger's person
    id when there is one), the room (`platform`, `conversation_id`,
    `conversation_type`) and the turn (`tag_id`, empty outside a tag).
    WHEN: `started_at` when the tool began (before any input upload),
    `finished_at` when the computer's answer came back or the call was
    cancelled; `created_at` is when the row was written. HOW: `tool`,
    `path` (`CALL_PATHS`), and the OTel `trace_id` and `span_id` the call
    ran under (empty when none: the trace is a linked
    debug view, this row is the record). `scope` is the workspace touched.
    WHAT: `request` is the FULL input as the model gave it; `result` is how
    it answered (status, exit code, reason, truncated, sizes, sha256s, and
    for a write or an edit `before_sha256`/`after_sha256`); `output_ref`
    points at the full output held in the blob store (key and sha256 per
    stream), so the row stays small and nothing is dropped.
    """

    id: UUID
    operation_id: UUID
    phase: str
    created_at: datetime
    started_at: datetime
    finished_at: datetime | None
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
    #: The computer session the call belongs to (`nora.domain.computer_sessions`,
    #: migration `0082`); None on legacy rows with no session, on a
    #: call whose session could not be opened, and on a call the computer
    #: was off for.
    session_id: UUID | None = None

    def __post_init__(self) -> None:
        if not valid_actor(self.actor):
            raise ValueError(f"unknown computer_events actor {self.actor!r}")
        if self.path not in CALL_PATHS:
            raise ValueError(f"unknown computer_events path {self.path!r}")
        if self.phase not in PHASES:
            raise ValueError(f"unknown computer_events phase {self.phase!r}")
        if (self.phase == "started") != (self.finished_at is None):
            raise ValueError("a started row has no finished_at; a finished row has one")


def normalise_computer_event(event: ComputerEvent) -> ComputerEvent:
    return replace(
        event,
        created_at=ensure_utc_event(event.created_at, field="created_at"),
        started_at=ensure_utc_event(event.started_at, field="started_at"),
        finished_at=(
            None if event.finished_at is None
            else ensure_utc_event(event.finished_at, field="finished_at")
        ),
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
        platform: str | None = None,
    ) -> list[ComputerEvent]:
        """The last `limit` events for `conversation_id` (and `platform`, when
        given: two networks can share a conversation id) with `created_at >=
        since`, newest first. Absolute `since`, the `ToolCallLogStore`
        shape, so a fake clock needs no special-casing."""
        ...

    async def for_scope(
        self, *, scope: str, since: datetime, limit: int,
    ) -> list[ComputerEvent]:
        """Recent audit rows for a bound unit workspace; origin stays unchanged."""
        ...

    async def for_session(self, *, session_id: UUID, limit: int) -> list[ComputerEvent]:
        """The newest `limit` rows of one computer session, newest first:
        what a session's progress is derived from (`derive_progress`)."""
        ...

    async def orphaned_starts(self, *, older_than: datetime, limit: int) -> list[ComputerEvent]:
        """`started` rows written before `older_than` that have no `finished`
        row: calls interrupted by a crash or a restart. Oldest first. The
        caller picks `older_than` past the longest a call can run, so a call
        still in flight is not reported."""
        ...
