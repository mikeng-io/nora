from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from nora.domain.notes import (
    Note,
    NoteRevision,
)
from nora.domain.record_query import RecordHit, RecordQuery
from nora.domain.rollup_read import RollupRead, RollupReadQuery
from nora.domain.self_presence import UnitRooms
from nora.domain.social_stream import RecordedItem
from nora.domain.standing import StandingHead


class RoomDirectory(Protocol):
    """The rooms of a turn's unit, as the room tools need them.

    `nora.engagement.self_presence.PresenceReader.rooms` is the one
    implementation: the same resolution `<presence>` renders from. A Protocol
    because `nora.agent` sits below `nora.engagement` and may not import it.
    """

    async def rooms(
        self,
        *,
        platform: str,
        conversation_id: str,
        direct: bool = False,
    ) -> UnitRooms: ...


class RecordQuerySource(Protocol):
    """The Record read `record_query` runs (`nora.infra.sql.record_query.
    SqlRecordQuery`, the one implementation). Returns the hits oldest first,
    whether more matched, and the ids that only near-match the text.
    `as_of_ms` folds edits, reactions and deletes at the caller's own instant
    — `now_fn()`'s — so a session replaying an earlier time reads the record
    as it then stood; `None` is the live fold."""

    async def query(
        self,
        q: RecordQuery,
        *,
        as_of_ms: int | None = None,
    ) -> tuple[tuple[RecordHit, ...], bool, frozenset[int]]: ...


class DocumentPublisher(Protocol):
    """What `document_publish` and `document_read` run against
    (`nora.app.documents_service.DocumentService`, the one implementation).
    `nora.agent` may not import `nora.app`, so the tools see it through this.
    Results are read by attribute: `status`, `document_id`, `revision`, `url`,
    `findings` (each `path`, `rule`, `message`), `note`, and on a read `title`
    and `spec`."""

    def catalog(self) -> str: ...

    async def publish(
        self,
        *,
        platform: str,
        conversation_id: str,
        person_id: str,
        raw: Any,
        written_by: str,
        document_id: UUID | None = None,
        base_revision: int | None = None,
        person_names: frozenset[str] | None = None,
        live_addresses: frozenset[str] | None = None,
    ) -> Any: ...

    async def read(
        self,
        *,
        platform: str,
        conversation_id: str,
        document_id: UUID | None = None,
        handle: str = "",
        person_id: str | None = None,
    ) -> Any: ...

    async def set_state(
        self,
        *,
        platform: str,
        conversation_id: str,
        person_id: str,
        document_id: UUID | None = None,
        state: str,
        handle: str = "",
    ) -> Any: ...

    @property
    def link_base(self) -> str:
        """The public origin documents are linked under (`[documents] public_url`), or `""`."""
        ...


class RollupReadSource(Protocol):
    """The Rollup read `rollup_read` runs (`nora.infra.sql.rollup_read.
    SqlRollupRead`, the one implementation): the CURRENT build's rows of one
    kind over an interval, never a build."""

    async def read(self, q: RollupReadQuery) -> RollupRead: ...


class SocialItemsSource(Protocol):
    """The social stream's read `social_read` runs (`nora.infra.sql.
    social_stream.SqlSocialStreamStore.items_of`, the one implementation): one
    person's readable items in an interval, newest first, under every id in
    `persons` (their current id and each retired one). Readable is the
    store's own rule: not opted out (`social = false`), not gone or hidden."""

    async def items_of(
        self,
        *,
        persons: Sequence[str],
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[RecordedItem]: ...


class StandingHeadSource(Protocol):
    """The Standing head `standing_read` shows, with its revision id
    (`nora.infra.sql.standing.SqlStandingStore.read_head`)."""

    async def read_head(
        self,
        *,
        platform: str,
        conversation_id: str,
    ) -> StandingHead | None: ...


class NoteCollaborator(Protocol):
    """Structural type for the note store — `nora.infra.sql.notes.SqlNoteStore`
    in production, `nora.testing.note_store.InMemoryNoteStore` in tests.

    A local Protocol keeps the layered import boundary:
    `nora.agent` may not import `nora.infra` under the layered import contract,
    and `nora.testing` is not a production dependency either — so this module
    can only name the SHAPE. There is no port in `nora.domain.ports` to import
    instead; the two implementations share
    `tests/contract/test_note_store_contract.py`, which is what actually holds
    them to one behaviour. A read by title resolves through Standing and
    follows a merged title to its successor.
    """

    async def standing(self, *, unit: str) -> tuple[Note, ...]: ...

    async def read(
        self,
        *,
        unit: str,
        note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...

    async def search(
        self,
        *,
        unit: str,
        query: str,
        limit: int,
    ) -> tuple[tuple[Note, str], ...]: ...

    async def create(
        self,
        *,
        unit: str,
        title: str,
        description: str,
        uncertainty: str,
        closing_condition: str,
        established_in: str,
        body: str,
        established_by: str,
        now_ms: int,
        anchor_date: int | None = None,
        anchor_entity_ids: tuple[str, ...] = (),
    ) -> Note: ...

    async def append_revision(
        self,
        *,
        unit: str,
        note_id: str,
        body: str,
        parent_revision_id: str,
        established_by: str,
        now_ms: int,
        evidence_room: str | None = None,
    ) -> NoteRevision: ...

    async def merged_successor_by_title(
        self,
        *,
        unit: str,
        title: str,
    ) -> Note | None: ...

    # `remind`'s one-open-row check asks the two questions the fire-time screen asks
    # (`nora/app/proactive_screen.py`): has the tag path moved this note since a waiting
    # row was linked, and is a waiting row's retired note one that was MERGED into this
    async def revision_chain(
        self,
        *,
        unit: str,
        note_id: str,
    ) -> tuple[NoteRevision, ...]: ...

    async def successor(self, *, unit: str, note_id: str) -> Note | None: ...
