"""The document store port (ADR 0020).

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

LIFECYCLE is a third append-only table, `document_state`: one row per change of state,
and a document's state is its LATEST row (none means `active`). Nothing is updated and
nothing is deleted, so a restore is one more row (`active`) and every revision a deleted
document had is still in `document_revision`. What each state does is the read side's:

- `active`: listed, opened, in the room's `<documents>` block, revisable.
- `archived`: off the default list and the block; opens by its link, listed only when
  the caller asks for archived documents, and refused a revision until it is restored.
- `deleted`: off every list and the block, and every read a person makes answers it
  exactly like an unknown id. Only `get` (the publish service's own read, which is
  room-scoped) still finds it, so a room can restore it through Nora.

Every datetime crossing this port is UTC-aware.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, get_args, runtime_checkable
from uuid import UUID

#: A room as the policy keys it: `(platform, conversation_id)`.
RoomKey = tuple[str, str]

#: A document's lifecycle state (the module docstring says what each one does). The
#: `document_state` table's CHECK must name exactly these
#: (`tests/contract/test_document_store_contract.py` reads it back).
DocumentState = Literal["active", "archived", "deleted"]
DOCUMENT_STATES: tuple[str, ...] = get_args(DocumentState)
#: What a person's list can be filtered to. `deleted` is never listed.
ListedState = Literal["active", "archived"]

#: A document's short handle: the first 12 hex digits of its id, no dashes. It is what a
#: link to a document carries, because a reply may not carry a UUID (egress blocks any
#: UUID at every exit) and this is not one. It is resolved among the documents the
#: VIEWER may read only, so 48 bits is an address, never a secret (the page is signed in).
DOCUMENT_HANDLE_CHARS = 12
_HANDLE = re.compile(r"[0-9a-f]{12}")


#: The longest search text a listing accepts. A caller refuses a longer one; the store
#: never sees it, so a query is a few words and never a way to make it scan on a huge needle.
DOCUMENT_QUERY_MAX = 200


def normalize_query(text: str) -> str:
    """A search text as the store matches it: trimmed, inner whitespace collapsed to one
    space. Empty means "no search". Callers check `DOCUMENT_QUERY_MAX` first."""
    return " ".join(text.split())


def handle_of(document_id: UUID) -> str:
    """The short handle of `document_id`."""
    return document_id.hex[:DOCUMENT_HANDLE_CHARS]


def is_handle(text: str) -> bool:
    """Whether `text` is exactly a document handle (lowercase hex, 12 characters). The one
    check a handle passes before it may reach a query."""
    return _HANDLE.fullmatch(text) is not None


def may_read(summary: DocumentSummary, person_id: str | None, named: frozenset[RoomKey]) -> bool:
    """Whether `person_id` may read a document, given the rooms whose explicit `persons` list
    names them (`rooms_of`). The creator always may; anyone else only where the document's room
    names them. Room membership alone is no grant: a `persons = "*"` room names nobody, so only
    its creator reads what was made there (8765_44827). `None` reads nothing."""
    if person_id is None:
        return False
    return summary.created_by == person_id or (summary.platform, summary.conversation_id) in named


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
    state: DocumentState = "active"
    #: Who wrote the LATEST state row ("" when there is none: the document was never put away).
    state_by: str = ""


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
    """One entry of a document's history. `media` is every picture that revision names,
    so the picture route can serve a picture an EARLIER revision shows (the portal's
    history view)."""

    revision: int
    title: str
    sha256: str
    written_by: str
    recorded_at: datetime
    media: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredRevision:
    """One revision's full snapshot, as it was written: never the current one unless it
    IS the current one. What the portal's history view shows and what a restore copies."""

    revision: int
    title: str
    spec: dict[str, Any]
    text: str
    sha256: str
    media: tuple[str, ...]
    written_by: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class RevisionOutcome:
    """`ok` carries the new current document; `conflict` means another revision
    took that number first (re-read and try again); `not_found` means no such id;
    `not_active` means the document is archived or deleted, and nothing was written
    (restore it first)."""

    status: Literal["ok", "conflict", "not_found", "not_active"]
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
        """Write revision `base_revision + 1`, only while the document is `active`."""
        ...

    async def set_state(
        self,
        *,
        document_id: UUID,
        state: DocumentState,
        set_by: str,
    ) -> bool:
        """Append a `document_state` row. `False` (and nothing written) when there is no
        such document. Who may do this is the caller's check, made before the call."""
        ...

    async def get(self, *, document_id: UUID) -> StoredDocument | None:
        """The current document in ANY state, with no visibility check: for the service
        that publishes into a room, never for a reader."""
        ...

    async def history(self, *, document_id: UUID) -> tuple[RevisionRecord, ...]:
        """Every revision, oldest first."""
        ...

    async def revision(self, *, document_id: UUID, revision: int) -> StoredRevision | None:
        """Revision `revision` of a document in ANY state, or `None` when there is no such
        revision. NO visibility check: the caller gates on `get_visible` first."""
        ...

    async def list_visible(
        self,
        *,
        person_id: str,
        rooms: frozenset[RoomKey],
        limit: int,
        offset: int,
        state: ListedState = "active",
        query: str = "",
    ) -> VisiblePage:
        """The documents `person_id` may read IN `state`, most recently updated first.

        A non-empty `query` keeps only those whose CURRENT revision's title or search text
        contains it, ignoring case (a plain substring; `normalize_query` is applied). The
        visibility and the state rule are the same ones the unfiltered list applies, in the
        same statement, so a search can never reach a document a list would not show: a
        deleted document, an archived one outside the Archived filter, and a document the
        person may not read are never matches. `total` counts the matches."""
        ...

    async def list_room(
        self,
        *,
        platform: str,
        conversation_ids: frozenset[str],
        limit: int,
        states: tuple[DocumentState, ...] = ("active",),
    ) -> tuple[DocumentSummary, ...]:
        """The documents IN `states` (by default the active ones) made in ONE room
        (`platform` and any of the room's conversation ids), most recently revised first, at
        most `limit`. It is the room's own list, not a person's: no visibility check, because
        everyone the room names already reads these. It loads no spec and no text: the prompt
        block reads this on every turn."""
        ...

    async def get_visible(
        self, *, document_id: UUID, person_id: str, rooms: frozenset[RoomKey]
    ) -> StoredDocument | None:
        """The current document if `person_id` may read it and it is not deleted, else
        `None`. An archived document is returned: it still opens by its link."""
        ...

    async def find_by_handle(self, *, handle: str, limit: int = 10) -> tuple[StoredDocument, ...]:
        """Every document in ANY state whose id starts with `handle`, at most `limit`, with
        NO visibility check: for the publish service only (a new id's clash check, and
        resolving a handle within the room a tool runs in), never for a reader. Empty when
        `handle` is not a handle."""
        ...

    async def get_visible_by_handle(
        self, *, handle: str, person_id: str, rooms: frozenset[RoomKey]
    ) -> StoredDocument | None:
        """The current document whose id starts with `handle` (`is_handle`), chosen among
        the documents `person_id` may read ONLY (never a deleted one): visibility first, then
        the prefix. `None`
        when none matches, when MORE THAN ONE does (never picked), and when `handle` is not
        a handle, so the three cannot be told apart and a prefix never reveals a document
        the person may not read."""
        ...

    async def aclose(self) -> None: ...
