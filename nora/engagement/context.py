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

import asyncio
import logging
import time
from typing import Any

from nora.domain.models import NormalizedMessage, PersonRef, RoomLanguage, TagContext, TranscriptRow
from nora.domain.ports import EngagementRequest, MemoryStore, RecordStore, RoomLanguageReader
from nora.persons import Persons

log = logging.getLogger("nora.engagement.context")

# How many rows `room_language(...)` asks the store for. Deliberately LARGER
# than `nora.agent.prefill`'s render cap (`DEFAULT_ROOM_LANGUAGE_MAX_ENTRIES`)
# — see `nora.language.render`'s own docstring: the store filters on
# `recognition_score` alone, then the renderer additionally drops any entry
# with no quotable example and cannot reach further down to replace it,
# because it was only ever handed as many rows as the store returned. Asking
# the store for the SAME number as the render cap therefore silently
# under-fills the block whenever any row lacks an example. A constructor
# default rather than an inline literal so Task 11 has somewhere to wire a
# config value without another signature change.
DEFAULT_ROOM_LANGUAGE_STORE_LIMIT = 15


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
        tagger_card_conclusions: int = 0,
        language: RoomLanguageReader | None = None,
        room_language_store_limit: int = DEFAULT_ROOM_LANGUAGE_STORE_LIMIT,
    ) -> None:
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._context_window = context_window
        self._metrics = metrics
        self._ai_peer = ai_peer
        self._self_card_conclusions = self_card_conclusions
        self._tagger_card_conclusions = tagger_card_conclusions
        # `language` is `None` at every call site until Task 11 wires a real
        # `RoomLanguageReader` into composition/offline — the same "not yet
        # configured" shape `ai_peer=""` already uses to disable the self/
        # tagger card reads below, kept OFF rather than raising so those
        # existing call sites keep working unchanged.
        self._language = language
        self._room_language_store_limit = room_language_store_limit

    async def assemble(self, *, request: EngagementRequest) -> TagContext:
        msg = request.message
        # Resolved BEFORE the gather because two of the four reads are keyed on
        # it, and it is a pure dict lookup — no reason for it to be a step.
        ref = self._persons.resolve(msg.platform, msg.sender_id)

        # CONCURRENT, and the reason is arithmetic rather than taste: these were
        # sequential until 2026-08-15, so the transcript read and (then) three
        # Honcho round trips stacked end to end at the FRONT of every tag, before
        # the first model call. One `representation` measured ~200ms live, so
        # adding `<peer_history>` serially would have put that on the critical
        # path of every turn; gathered, it hides behind the reads that were
        # already there and the turn gets FASTER than before the block existed.
        #
        # Every helper is written not to raise, which is what makes `gather` safe
        # here: one exception would otherwise cancel its three siblings and take
        # the turn down with it. That is the same property `MemoryStore`'s
        # "never load-bearing" rule already claims — this just stops relying on
        # the adapter to honour it. `_room_language` is the fifth sibling
        # (Task 10) and holds the same property.
        transcript, card, self_card, tagger_card, room_language = await asyncio.gather(
            self._recent(msg),
            self._peer_card(ref, msg),
            self._recall_self(msg),
            self._recall_tagger(ref, msg),
            self._room_language(msg),
        )

        # One name space for the whole prompt (DEC-80) — see `Persons.name_transcript`.
        transcript = self._persons.name_transcript(transcript, platform=msg.platform)

        return TagContext(
            tag_id=request.tag_id,
            message=msg,
            tagger=ref,
            transcript=transcript,
            peer_card=card,
            started_at=time.perf_counter(),
            self_card=self_card,
            tagger_card=tagger_card,
            room_language=room_language,
        )

    async def _recent(self, msg: NormalizedMessage) -> tuple[TranscriptRow, ...]:
        """The transcript, or an empty one — a thinner reply beats no reply."""
        try:
            return tuple(await self._audit.recent(
                conversation_id=msg.conversation_id,
                limit=self._context_window,
            ))
        except Exception:
            log.exception("context collection failed; continuing with an empty transcript")
            self._metrics.inc("nora_errors_total", stage="context", platform=msg.platform)
            return ()

    async def _peer_card(
        self, ref: PersonRef | None, msg: NormalizedMessage
    ) -> tuple[str, ...]:
        """Their durable card. NO Honcho call at all for an unmapped sender.

        Guarded, unlike the inline call this replaced. `MemoryStore.peer_card`
        answers `[]` rather than raising for both of its failure modes, so in
        principle this catch is unreachable — but it is now one of four siblings
        in a `gather`, where an unreachable exception stops being a thinner
        prompt and becomes three cancelled reads and a dead turn.
        """
        if ref is None:
            return ()
        try:
            card = tuple(await self._memory.peer_card(peer_id=ref.honcho_peer_id))
            if not card:
                # THE ONE SIGNAL THIS READ HAS. `peer_card` answers `[]` for a
                # dead endpoint, a timeout, an unknown peer and a genuinely
                # empty card alike (`ports.py:500`), so the block simply is not
                # rendered and nothing anywhere says why — which is exactly how
                # it stayed dead for the life of a deployment once already.
                #
                # Found again 2026-08-15 by an operator reading a trace: the
                # block was absent for a tagger whose card read 12 items
                # minutes later, with no transport failure logged. A COUNTER
                # cannot say which of the four it was — only `Recollection`'s
                # two-axis shape can, and giving `peer_card` that is a port
                # change — but it does make «her card stopped appearing»
                # something an operator can see instead of notice.
                self._metrics.inc(
                    "nora_peer_card_empty_total", platform=msg.platform)
            return card
        except Exception:
            log.exception("peer-card read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="peer_card", platform=msg.platform)
            return ()

    async def _recall_tagger(
        self, ref: PersonRef | None, msg: NormalizedMessage
    ) -> tuple[str, ...]:
        """What Honcho concluded about THE TAGGER, curated to this message.

        The `about` tool's exact call — observer Nora, observed them — run up
        front for the one person guaranteed to be relevant: the one talking.
        It is deliberately a DUPLICATE of a tool that already works, bought
        because a model that has to decide to look often doesn't, and because
        the round trip it saves costs a model call in each direction.

        NO new disclosure, and this is the part worth being sure about rather
        than assuming: `about` is already gated by the room roster
        (`RoomView.find`), and the tagger is in the room by construction — they
        just spoke in it. So this surfaces nothing she could not already have
        asked for about this person, in this room, on this turn.

        Fail-open and silent for `_recall_self`'s reason, which applies with
        more force here: this is background she may draw on, never the ground
        for a claim. `about` remains the path for anything she will actually
        assert, and it reports `unavailable` honestly.
        """
        if ref is None or not self._ai_peer or self._tagger_card_conclusions <= 0:
            return ()
        try:
            recall = await self._memory.representation(
                peer_id=self._ai_peer,
                target=ref.honcho_peer_id,
                search_query=msg.body or "",
                max_conclusions=self._tagger_card_conclusions,
            )
        except Exception:
            log.exception("tagger-card recall failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="tagger_card", platform=msg.platform)
            return ()
        if not recall.reachable:
            self._metrics.inc(
                "nora_errors_total", stage="tagger_card", platform=msg.platform)
            return ()
        return recall.conclusions

    async def _recall_self(self, msg: NormalizedMessage) -> tuple[str, ...]:
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
                search_query=msg.body or "",
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

    async def _room_language(self, msg: NormalizedMessage) -> RoomLanguage:
        """What the Dreamer has distilled about THIS ROOM's own slang.

        `scope_id` is the conversation id — the same key `SqlLanguageStore`
        writes under (`nora.infra.sql.language`'s `lag`/`seconds_since_last_message`
        both key `WHERE conversation_id = scope_id`), so a room's own history is
        what comes back, never another room's.

        `language=None` (nothing wired yet — Task 11's job) and the store
        asking for MORE rows than the render will use are both handled below;
        see the module-level `DEFAULT_ROOM_LANGUAGE_STORE_LIMIT` for the second.

        `RoomLanguageReader.room_language` is documented never to raise, and
        `SqlLanguageStore` already guards broadly, so in principle this catch is
        unreachable — but it is now one of five siblings in a `gather`, where an
        unreachable exception stops being a thinner prompt and becomes four
        cancelled reads and a dead turn. Same property `_peer_card` guards.
        """
        if self._language is None:
            return RoomLanguage(scope_id=msg.conversation_id)
        try:
            return await self._language.room_language(
                scope_id=msg.conversation_id, limit=self._room_language_store_limit,
            )
        except Exception:
            log.exception("room-language read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="room_language", platform=msg.platform)
            return RoomLanguage(scope_id=msg.conversation_id)
