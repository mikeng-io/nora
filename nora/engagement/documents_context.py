"""`DocumentsContextReader`: the `<documents>` block's one read (design/artifact §29),
shared by the tag path (`TranscriptInjection`) and the fire path (`FirePath`).

She can only revise a document whose id she can see. A revision request ("make it
8pm") arrives in a later turn, where a lookup she must remember to call is skipped
(an optional act is almost never chosen unprompted); a line already in view is not.

**This room's documents only.** The room is matched by every conversation id it answers
to, as `DocumentService` matches it, and a document made in another room never appears,
whoever made it. It is the room's own list, not a person's: everyone the room names
already reads these.

**Never raises, and never a wrong id.** A store that is down, or slow past `timeout`,
is logged, counted (`nora_errors_total{stage="documents_context"}`) and read as no
block. A turn never waits on it beyond the bound and never fails because of it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from nora.domain.documents import DocumentStore
from nora.domain.documents_context import DOCUMENTS_SHOWN, DocumentLine, DocumentsView
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
        timeout: float = 1.0,
    ) -> None:
        self._store = store
        self._room_ids = room_ids
        self._clock = clock
        self._metrics = metrics
        self._shown = shown
        self._timeout = timeout

    async def read(self, *, platform: str, conversation_id: str) -> DocumentsView | None:
        """This room's newest documents, or `None` (none yet, or the read failed)."""
        try:
            ids = self._room_ids(platform, conversation_id) | {conversation_id}
            found = await asyncio.wait_for(
                self._store.list_room(
                    platform=platform, conversation_ids=ids, limit=self._shown
                ),
                timeout=self._timeout,
            )
        except Exception:
            log.exception("documents context read failed; rendering no block")
            self._metrics.inc("nora_errors_total", stage="documents_context", platform=platform)
            return None
        if not found:
            return None
        return DocumentsView(
            lines=tuple(
                DocumentLine(
                    title=d.title,
                    document_id=str(d.id),
                    revision=d.revision,
                    revised_at=d.updated_at,
                )
                for d in found[: self._shown]
            ),
            now=self._clock(),
        )
