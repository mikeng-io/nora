from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from nora.domain.record_query import RecordHit, RecordQuery
from nora.domain.rollup_read import RollupRead, RollupReadQuery
from nora.domain.self_presence import UnitRooms
from nora.domain.social_stream import RecordedItem


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
