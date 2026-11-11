from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from nora.domain.gatherings import (
    RSVP,
    Gathering,
    GatheringStatus,
    RSVPResponse,
    VenueSuggestions,
)
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


class EventsCollaborator(Protocol):
    """Structural type for `nora.app.gatherings.GatheringService` — a local Protocol
    rather than an import of the concrete class, because `nora.agent` sits
    BELOW `nora.app` in the "layered architecture" import-linter contract
    (`pyproject.toml`) and may not import it, even for a bare type
    annotation: grimp's `exclude_type_checking_imports` defaults to False and
    this repo does not set it, so a `TYPE_CHECKING` guard would not rescue
    it either. `nora.app.composition` builds the real `GatheringService` and
    injects it here (and into `ToolRegistry`); this module only needs its
    shape. `GatheringNotFoundError`/`AmbiguousOpenGatheringsError` live in
    `nora.domain.gatherings` for the identical reason — an exception TYPE has to
    be importable from every layer that catches it."""

    async def create_event(
        self,
        *,
        conversation_id: str,
        requester_person_id: str,
        title: str,
        when: datetime | None = None,
        venue_text: str | None = None,
        person_names: frozenset[str] | None = None,
        platform: str,
        unit_id: str = "",
        message_ids: Sequence[int] = (),
    ) -> Gathering: ...

    async def update_event(
        self,
        *,
        event_id: UUID,
        conversation_id: str,
        when: datetime | None = None,
        venue_text: str | None = None,
        person_names: frozenset[str] | None = None,
        platform: str,
        unit_id: str = "",
        base_revision_id: UUID | None = None,
        message_ids: Sequence[int] = (),
    ) -> Gathering: ...

    async def rsvp(
        self,
        *,
        event_id: UUID,
        conversation_id: str,
        person_id: str,
        response: RSVPResponse,
        platform: str = "",
        unit_id: str = "",
    ) -> RSVP: ...

    async def cancel_event(
        self,
        *,
        event_id: UUID,
        conversation_id: str,
        platform: str = "",
        unit_id: str = "",
    ) -> Gathering: ...

    async def set_event_status(
        self,
        *,
        event_id: UUID,
        conversation_id: str,
        status: GatheringStatus,
        base_revision_id: UUID | None = None,
        platform: str = "",
        unit_id: str = "",
        message_ids: Sequence[int] = (),
    ) -> Gathering: ...

    async def list_events(
        self,
        *,
        conversation_id: str,
        status: GatheringStatus | None = None,
        unit_id: str = "",
        platform: str = "",
    ) -> Sequence[Gathering]: ...

    async def search_events(
        self,
        *,
        unit_id: str,
        conversation_id: str,
        place: str | None = None,
        person: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        platform: str = "",
    ) -> Sequence[Gathering]: ...

    async def suggest_venue(
        self,
        *,
        event_id: UUID,
        conversation_id: str,
        roster_person_ids: Sequence[str],
        person_names: frozenset[str] | None = None,
        platform: str = "",
        unit_id: str = "",
    ) -> VenueSuggestions: ...


class NoteCollaborator(Protocol):
    """Structural type for the note store — `nora.infra.sql.notes.SqlNoteStore`
    in production, `nora.testing.note_store.InMemoryNoteStore` in tests.

    A local Protocol for `EventsCollaborator`'s reason, one layer further out:
    `nora.agent` may not import `nora.infra` under the layered import contract,
    and `nora.testing` is not a production dependency either — so this module
    can only name the SHAPE. There is no port in `nora.domain.ports` to import
    instead; the two implementations share
    `tests/contract/test_note_store_contract.py`, which is what actually holds
    them to one behaviour. This declares the verbs the tool layer calls and no
    more: `standing` (title resolution), `read`, `search`, `create`,
    `append_revision`, `merged_successor_by_title` (a merged note's title
    forwards), and `revision_chain`/`successor` (`remind`'s one-open-row
    check).

    `StaleNoteHeadError` lives in `nora.domain.notes` for the identical reason
    `GatheringNotFoundError` lives in `nora.domain.gatherings` — an exception TYPE has
    to be importable from every layer that catches it.
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

    # `remind`'s one-open-row check asks the two questions the
    # fire-time screen asks (`nora/app/proactive_screen.py`): has the tag path
    # moved this note since a waiting row was linked, and is a waiting row's
    # retired note one that was MERGED into this one.
    async def revision_chain(
        self,
        *,
        unit: str,
        note_id: str,
    ) -> tuple[NoteRevision, ...]: ...

    async def successor(self, *, unit: str, note_id: str) -> Note | None: ...
