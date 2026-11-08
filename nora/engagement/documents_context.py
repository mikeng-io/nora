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
another room never appears, whoever made it. Within the room the store lists only what the
turn's requester may read (`may_read`: the creator's own, and everything in a room that names
them), and it applies that before each limit, so the block's N are N documents the asker may
see (Aikido, PR 203). In a `persons = "*"` room that is the creator's documents only, so another
member's creator-only ids are not enumerated (8765_44827). A turn with no requester (a
participation turn) renders no block at all.

**Never raises, and never a wrong id.** A store that is down, or slow past `timeout`,
is logged, counted (`nora_errors_total{stage="documents_context"}`) and returned as
an unavailable view. A turn never waits on it beyond the bound and never fails because of it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from nora.domain.documents import DocumentAuthority, DocumentStore, DocumentSummary, RoomKey
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
        shared_room: Callable[[str, str, str], bool] | None = None,
        autonomous_screen: Callable[[str], str | None] | None = None,
    ) -> None:
        self._autonomous_screen = autonomous_screen
        self._shared_room = shared_room
        self._store = store
        self._room_ids = room_ids
        self._rooms_of = rooms_of
        self._clock = clock
        self._metrics = metrics
        self._shown = shown
        self._put_away = put_away
        self._timeout = timeout

    async def read(
        self,
        *,
        platform: str,
        conversation_id: str,
        person_id: str | DocumentAuthority | None,
    ) -> DocumentsView | None:
        """The authorized room index, or None without a requester/capability.

        Successful empty and failed reads remain distinct.
        """
        if person_id is None:
            return None
        autonomous = isinstance(person_id, DocumentAuthority)
        if isinstance(person_id, DocumentAuthority):
            if self._shared_room is None:
                return DocumentsView(
                    lines=(),
                    now=self._clock(),
                    unavailable=True,
                    room=f"{platform}:{conversation_id}",
                )
            named = frozenset(
                (p, c)
                for p, c in person_id.rooms
                if self._shared_room is not None and self._shared_room(person_id.unit, p, c)
            )
            if (platform, conversation_id) not in named:
                return DocumentsView(
                    lines=(), now=self._clock(), room=f"{platform}:{conversation_id}"
                )
        else:
            named = self._rooms_of(person_id) if self._rooms_of is not None else frozenset()
        try:
            ids = self._room_ids(platform, conversation_id) | {conversation_id}
            # The store applies the visibility before each limit, so a limit counts only
            # documents this person may read (Aikido, PR 203).
            found, away = await asyncio.wait_for(
                asyncio.gather(
                    self._store.list_room(
                        platform=platform,
                        conversation_ids=ids,
                        limit=self._shown + 1,
                        person_id=person_id,
                        rooms=named,
                    ),
                    self._store.list_room(
                        platform=platform,
                        conversation_ids=ids,
                        limit=0 if autonomous else self._put_away,
                        person_id=person_id,
                        rooms=named,
                        states=("archived", "deleted"),
                    ),
                ),
                timeout=self._timeout,
            )
        except Exception:
            log.exception("documents context read failed")
            self._metrics.inc("nora_errors_total", stage="documents_context", platform=platform)
            return DocumentsView(
                lines=(), now=self._clock(), unavailable=True, room=f"{platform}:{conversation_id}"
            )
        if autonomous and self._autonomous_screen and any(
            self._autonomous_screen(d.title) for d in found
        ):
            return DocumentsView(lines=(), now=self._clock(), unavailable=True,
                                 room=f"{platform}:{conversation_id}")
        return DocumentsView(
            room=f"{platform}:{conversation_id}",
            lines=tuple(_line(d) for d in found[: self._shown]),
            not_listed=max(0, len(found) - self._shown),
            now=(
                datetime.fromtimestamp(person_id.as_of_ms / 1000, UTC)
                if isinstance(person_id, DocumentAuthority) and person_id.as_of_ms is not None
                else self._clock()
            ),
            put_away=tuple(_line(d) for d in away),  # bounded by the read's own limit
        )


def _line(d: DocumentSummary) -> DocumentLine:
    return DocumentLine(
        title=d.title,
        document_id=str(d.id),
        revision=d.revision,
        revised_at=d.updated_at,
        state=d.state,
    )
