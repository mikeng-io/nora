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
from nora.domain.ports import EngagementRequest, MemoryStore, RecordStore
from nora.persons import Persons

log = logging.getLogger("nora.engagement.context")


class TranscriptInjection:
    """Satisfies `nora.domain.ports.ContextAssembler`."""

    def __init__(
        self,
        *,
        audit: RecordStore,
        memory: MemoryStore,
        persons: Persons,
        context_window: int,
        metrics: Any,
        ai_peer: str = "",
        self_card_conclusions: int = 0,
    ) -> None:
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._context_window = context_window
        self._metrics = metrics
        self._ai_peer = ai_peer
        self._self_card_conclusions = self_card_conclusions

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

        # One name space for the whole prompt (DEC-80) — see `Persons.name_transcript`.
        transcript = self._persons.name_transcript(transcript, platform=msg.platform)

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
            self_card=await self._recall_self(msg),
        )

    async def _recall_self(self, msg: Any) -> tuple[str, ...]:
        """What Honcho concluded about NORA, curated to THIS message.

        The `representation` verb with `target=None`, i.e. observer==observed —
        her own model of herself, built from what she has said in this
        workspace. NOT `peer_card`: the card renders the same history as
        IDENTITY claims, and hers currently asserts «Kind: AI chatbot/assistant»
        and an owner with «admin authority», both of which contradict
        `identity.md` and would win on injection because a learned claim reads
        as evidence next to an authored one.

        `search_query` is the inbound text, so the conclusions are the ones
        RELEVANT to what was just asked rather than the newest N — a promise
        about tomorrow morning surfaces when somebody mentions tomorrow, which
        is the only time it is worth its tokens.

        Fail-open and SILENT-open, deliberately, and this is the one place in
        the repo where that is right: an unreachable Honcho yields no block at
        all rather than a note saying she cannot remember. R-27's three states
        govern CLAIMS — this is not a claim she will make, it is background she
        may draw on, so «I could not check» has nothing to be reported about.
        The failure surfaces on `nora_errors_total{stage="self_card"}` instead,
        where it is an operator's problem rather than the group's.
        """
        if not self._ai_peer or self._self_card_conclusions <= 0:
            return ()
        try:
            recall = await self._memory.representation(
                peer_id=self._ai_peer,
                search_query=msg.text or "",
                max_conclusions=self._self_card_conclusions,
            )
        except Exception:
            log.exception("self-card recall failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="self_card", platform=msg.platform)
            return ()
        if not recall.reachable:
            self._metrics.inc(
                "nora_errors_total", stage="self_card", platform=msg.platform)
            return ()
        return recall.conclusions
