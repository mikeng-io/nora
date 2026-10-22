"""`DocumentsContextReader`: the `<documents>` block's one read,
shared by the tag path (`TranscriptInjection`) and the fire path (`FirePath`).

She can only revise a document whose id she can see. A revision request ("make it
8pm") arrives in a later turn, where a lookup she must remember to call is skipped
(an optional act is almost never chosen unprompted); a line already in view is not.

**Put away, too.** The room's archived and deleted documents (at most
`PUT_AWAY_SHOWN`) come back as `put_away`, each with its state, because nothing else a
later turn sees carries their ids, and "bring it back" needs one. It is the room's own
list, read for this room only, so a deleted document is shown to the room it was made in
and to nobody else.

**This room's documents only, and only those the asker may read.** The room is matched by
every conversation id it answers to, as `DocumentService` matches it, and a document made in
another room never appears, whoever made it. Within the room the list is filtered by
`may_read` for the turn's requester: the creator's own, and everything in a room that names
them. In a `persons = "*"` room that is the creator's documents only, so another member's
creator-only ids are not enumerated (8765_44827). A turn with no requester (a participation
turn) renders no block at all.

**Never raises, and never a wrong id.** A store that is down, or slow past `timeout`,
is logged, counted (`nora_errors_total{stage="documents_context"}`) and read as no
block. A turn never waits on it beyond the bound and never fails because of it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from nora.domain.documents import DocumentStore, DocumentSummary, RoomKey, may_read
from nora.domain.documents_context import (
    DOCUMENTS_SHOWN,
    PUT_AWAY_SHOWN,
    DocumentLine,
    DocumentsView,
)
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.documents.context")


class DocumentsContextReader:
    def __init__(
        self,
        *,
        store: DocumentStore,
        room_ids: Callable[[str, str], frozenset[str]],
        clock: Callable[[], datetime],
        metrics: Metrics,
        shown: int = DOCUMENTS_SHOWN,
        put_away: int = PUT_AWAY_SHOWN,
        timeout: float = 1.0,
        rooms_of: Callable[[str], frozenset[RoomKey]] | None = None,
    ) -> None:
        self._store = store
        self._room_ids = room_ids
        self._rooms_of = rooms_of
        self._clock = clock
        self._metrics = metrics
        self._shown = shown
        self._put_away = put_away
        self._timeout = timeout

    async def read(
        self, *, platform: str, conversation_id: str, person_id: str | None,
    ) -> DocumentsView | None:
        """This room's newest documents `person_id` may read, or `None` (none yet, none they
        may read, no requester, or the read failed)."""
        if person_id is None:
            return None
        named = self._rooms_of(person_id) if self._rooms_of is not None else frozenset()
        try:
            ids = self._room_ids(platform, conversation_id) | {conversation_id}
            found, away = await asyncio.wait_for(
                asyncio.gather(
                    self._store.list_room(
                        platform=platform, conversation_ids=ids, limit=self._shown
                    ),
                    self._store.list_room(
                        platform=platform, conversation_ids=ids, limit=self._put_away,
                        states=("archived", "deleted"),
                    ),
                ),
                timeout=self._timeout,
            )
        except Exception:
            log.exception("documents context read failed; rendering no block")
            self._metrics.inc("nora_errors_total", stage="documents_context", platform=platform)
            return None
        found = [d for d in found if may_read(d, person_id, named)]
        away = [d for d in away if may_read(d, person_id, named)]
        if not found and not away:
            return None
        return DocumentsView(
            lines=tuple(_line(d) for d in found[: self._shown]),
            now=self._clock(),
            put_away=tuple(_line(d) for d in away),  # bounded by the read's own limit
        )


def _line(d: DocumentSummary) -> DocumentLine:
    return DocumentLine(
        title=d.title, document_id=str(d.id), revision=d.revision, revised_at=d.updated_at,
        state=d.state,
    )
