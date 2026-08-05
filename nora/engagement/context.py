"""`TranscriptInjection` — assembles the `TagContext` a tag reply needs
(spec C.8). Replaces `Handler._collect_context` (`nora/handler.py:99-117`);
this module preserves its two failure-shaped behaviours exactly:

- A transcript read failure logs, counts `nora_errors_total{stage="context"}`,
  and continues with an EMPTY transcript rather than raising — a thinner
  reply beats no reply (R-5-adjacent).
- An unmapped sender (`Persons.resolve` returns `None`) yields `tagger=None`
  and an empty peer card, with NO Honcho call at all: `memory.peer_card` is
  only ever called for a sender we can name.

Takes an `EngagementRequest`, not a bare message, because `decision` is the
only route by which a future classifier's `rationale` reaches the prefill
(design/02 §True Ambient Hooks) — `assemble` must accept it today even though
nothing reads it yet, so that seam does not require another signature change
later.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from nora.domain.models import TagContext, TranscriptRow
from nora.domain.ports import AuditStore, EngagementRequest, MemoryStore
from nora.persons import Persons

log = logging.getLogger("nora.engagement.context")


class TranscriptInjection:
    """Satisfies `nora.domain.ports.ContextAssembler`."""

    def __init__(
        self,
        *,
        audit: AuditStore,
        memory: MemoryStore,
        persons: Persons,
        context_window: int,
        metrics: Any,
    ) -> None:
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._context_window = context_window
        self._metrics = metrics

    async def assemble(self, *, request: EngagementRequest) -> TagContext:
        msg = request.message
        transcript: tuple[TranscriptRow, ...] = ()
        try:
            transcript = tuple(await self._audit.recent(
                conversation_id=msg.conversation_id,
                limit=self._context_window,
            ))
        except Exception:
            log.exception("context collection failed; continuing with an empty transcript")
            self._metrics.inc("nora_errors_total", stage="context", platform=msg.platform)

        ref = self._persons.resolve(msg.platform, msg.sender_id)
        card: tuple[str, ...] = ()
        if ref is not None:
            card = tuple(await self._memory.peer_card(peer_id=ref.honcho_peer_id))

        return TagContext(
            tag_id=request.tag_id,
            message=msg,
            tagger=ref,
            transcript=transcript,
            peer_card=card,
            started_at=time.perf_counter(),
        )
