"""The document store port (ADR 0020, design/artifact/00-design.md §5).

A document is published by Nora into a room and read back by people in the portal.
Two tables sit behind this port and **neither is ever updated in place**:
`documents` is the identity (who, which room, when, written once) and
`document_revision` is one full snapshot per revision. The CURRENT document is the
highest revision, so a revision cannot overwrite an earlier one and a listing is a
read of the newest rows, never of a cache that could drift. Two writers revising
from the same base collide on `(document_id, revision)`: one wins, the other gets
`conflict` and must re-read, which is the same compare-and-swap the note and event
tiers use, without a head pointer to keep in step.

VISIBILITY lives in the read methods, not in the caller (`list_visible`,
`get_visible`): a person sees a document they created, and every document made in
a room that names them. The caller derives `rooms` from the current policy at the
moment of the read, so removing a person from a room revokes their access; nothing
is snapshotted here. A `persons = "*"` room names nobody, so only its creator sees
what was made there. Everything else is `None` (the same answer as "no such
document": never a distinct "forbidden", so an id does not confirm that a document
exists).

Every datetime crossing this port is UTC-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

#: A room as the policy keys it: `(platform, conversation_id)`.
RoomKey = tuple[str, str]


class NaiveDocumentDatetimeError(ValueError):
    """A datetime reached the document store without a timezone."""


class DocumentExistsError(Exception):
    """`create` was given an id that is already a document."""


def ensure_utc_document(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDocumentDatetimeError(f"{field} must be timezone-aware; got naive {value!r}.")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RevisionIn:
    """One revision as the publish service hands it over: already checked. `spec` is
    the checker's normalised document, `sha256` the hash of its canonical form,
    `text` the search text, `media` the full sha256 of every picture it names."""

    title: str
    spec: dict[str, Any]
    text: str
    sha256: str
    media: tuple[str, ...]
    written_by: str


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    """What a list shows. `created_at` is when the document first existed;
    `updated_at` is when its current revision was written."""

    id: UUID
    platform: str
    conversation_id: str
    created_by: str
    title: str
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """A document at its current revision."""

    summary: DocumentSummary
    spec: dict[str, Any]
    text: str
    sha256: str
    media: tuple[str, ...]
    written_by: str


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    """One entry of a document's history."""

    revision: int
    title: str
    sha256: str
    written_by: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class RevisionOutcome:
    """`ok` carries the new current document; `conflict` means another revision
    took that number first (re-read and try again); `not_found` means no such id."""

    status: Literal["ok", "conflict", "not_found"]
    document: StoredDocument | None = None


@dataclass(frozen=True, slots=True)
class VisiblePage:
    items: tuple[DocumentSummary, ...]
    total: int


@runtime_checkable
class DocumentStore(Protocol):
    async def create(
        self,
        *,
        document_id: UUID,
        platform: str,
        conversation_id: str,
        created_by: str,
        revision: RevisionIn,
    ) -> StoredDocument:
        """Write revision 1. Raises `DocumentExistsError` for a taken id."""
        ...

    async def add_revision(
        self, *, document_id: UUID, base_revision: int, revision: RevisionIn
    ) -> RevisionOutcome:
        """Write revision `base_revision + 1`."""
        ...

    async def get(self, *, document_id: UUID) -> StoredDocument | None:
        """The current document, with no visibility check: for the service that
        publishes into a room, never for a reader."""
        ...

    async def history(self, *, document_id: UUID) -> tuple[RevisionRecord, ...]:
        """Every revision, oldest first."""
        ...

    async def list_visible(
        self, *, person_id: str, rooms: frozenset[RoomKey], limit: int, offset: int
    ) -> VisiblePage:
        """The documents `person_id` may read, most recently updated first."""
        ...

    async def list_room(
        self, *, platform: str, conversation_ids: frozenset[str], limit: int
    ) -> tuple[DocumentSummary, ...]:
        """The documents made in ONE room (`platform` and any of the room's conversation ids),
        most recently revised first, at most `limit`. It is the room's own list, not a person's:
        no visibility check, because everyone the room names already reads these. It loads no
        spec and no text: the prompt block reads this on every turn."""
        ...

    async def get_visible(
        self, *, document_id: UUID, person_id: str, rooms: frozenset[RoomKey]
    ) -> StoredDocument | None:
        """The current document if `person_id` may read it, else `None`."""
        ...

    async def aclose(self) -> None: ...
