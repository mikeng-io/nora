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
from typing import Any, Protocol

from nora.domain.facts import (
    Fact,
    FactExtent,
    FactsView,
    mentions,
    normalise_name,
)
from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
    RoomExchange,
    RoomLanguage,
    RoomPortrait,
    TagContext,
    TranscriptRow,
)
from nora.domain.notes import Note
from nora.domain.ports import (
    EngagementRequest,
    FactReader,
    MemoryStore,
    RecordStore,
    RoomExchangeReader,
    RoomLanguageReader,
    RoomPortraitReader,
    StandingStore,
)
from nora.domain.standing import Standing
from nora.persons import Persons
from nora.policy import Policy

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

# Dreamer Plan 2's own version of the same relation, for the same reason —
# see `nora.config.Config._exchange_render_cap_stays_below_the_store_limit`.
DEFAULT_ROOM_EXCHANGE_STORE_LIMIT = 10


class StandingNotesReader(Protocol):
    """The one note-tier verb this assembler needs — `SqlNoteStore.standing`
    (2026-08-21 note-tier design §5.1), narrowed to a Protocol for the same
    reason `RoomPortraitReader` exists: `TranscriptInjection` takes
    Protocol-typed collaborators only, so it never imports `nora.infra`.

    Declared HERE rather than beside `FactReader` in `nora.domain.ports`
    because today the prefill is the tier's only reader; when `note_read` /
    `note_search` land in `nora.agent.tools` the tier's full read half belongs
    in `ports` next to its fact-tier sibling, and this Protocol becomes an
    alias of it.

    **Never raises**, the promise every critical-path read in this module makes
    (`FactReader.standing_facts`' own wording). `SqlNoteStore.standing` does not
    yet make it — it is a bare `SELECT` — which is why `_notes` guards rather
    than trusts, exactly as `_peer_card` does for a port that DOES promise it.
    """

    async def standing(self, *, workspace: str) -> tuple[Note, ...]:
        """Every note in `workspace` that has not been retired. Uncapped by
        design (§5.1): a note missing from the index is invisible to her, and
        invisible is indistinguishable from absent."""
        ...


def _workspace_of(policy: Policy | None, msg: NormalizedMessage) -> str:
    """The workspace a room belongs to (`Conversation.workspace`, DEC-105).

    ONE resolver for both workspace-keyed tiers. The note tier and the fact
    tier are keyed on the same thing, and two tiers each carrying their own
    idea of what a room's workspace is would disagree the first time a
    deployment ran two social units — which is precisely the case the key
    exists for.

    Falls back to DEC-105's own default rather than refusing: neither tier is
    an admission gate, and by the time a tag reaches the assembler ingest has
    already applied the allowlist (DEC-18), so a room absent here is one the
    ingest path already let through under the default.
    """
    convo = (
        policy.conversation(msg.platform, msg.conversation_id)
        if policy is not None
        else None
    )
    return convo.workspace if convo is not None else "nora"


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
        exchange: RoomExchangeReader | None = None,
        room_exchange_store_limit: int = DEFAULT_ROOM_EXCHANGE_STORE_LIMIT,
        portrait: RoomPortraitReader | None = None,
        notes: StandingNotesReader | None = None,
        policy: Policy | None = None,
        facts: FactReader | None = None,
        # 19 §6's two caps, with its reasoning. ROOM is the working set —
        # more than eight and the block competes with the transcript it sits
        # beside. SENDER is the narrowest slice and the most often empty, and
        # four is enough to ground «you told us…».
        max_room_facts: int = 8,
        max_sender_facts: int = 4,
        standing: StandingStore | None = None,
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
        self._exchange = exchange
        self._room_exchange_store_limit = room_exchange_store_limit
        self._portrait = portrait
        # `notes` is `None` while `[notes] enabled = false` — the note tier
        # ships dormant, the same "not yet wired" shape `language`/`exchange`/
        # `portrait` above use, and the same one the fact tier will use.
        self._notes_store = notes
        # policy.toml, ONLY so `_notes` can name the workspace a room belongs
        # to (`Conversation.workspace`, DEC-105). Nothing else in this class
        # reads it, and nothing here decides admission — by the time a tag
        # reaches the assembler ingest has already applied the allowlist
        # (DEC-18). Passing the Policy rather than a resolved workspace string
        # is what keeps this correct once a deployment runs two social units:
        # the workspace is a property of the ROOM, resolved per message.
        self._policy = policy
        # The fact tier (2026-08-20 fact-tier design). `None` while
        # `[facts] enabled = false`, the same "not yet wired" shape its four
        # neighbours use. It resolves its workspace through `self._policy`, the
        # SAME mechanism the note tier uses — two tiers keyed on
        # `Conversation.workspace` must not each carry their own idea of what a
        # room's workspace is.
        self._facts_reader = facts
        self._max_room_facts = max_room_facts
        self._max_sender_facts = max_sender_facts
        # Standing (design/dreamer-agent-loop/26-standing.md). `None` while
        # `[standing] enabled = false`, the same "not yet wired" shape its five
        # neighbours use.
        #
        # **It is the whole `StandingStore`, not a read-only narrowing**, and
        # that is the one place this collaborator differs from every other
        # reader above. `nora.domain.ports.StandingStore` has two verbs and the
        # debrief stage needs both, so narrowing it here to a `read`-only
        # Protocol would mean composition constructing the store twice or
        # threading two names for one object — and the fold's non-negotiable is
        # precisely that the READ and the WRITE are the same row seen twice
        # (26 §4). One port, one instance, two callers.
        #
        # It is keyed `(platform, conversation_id)` and needs no `Policy`,
        # unlike `notes`/`facts`: a position is held in a room, not shared
        # across a workspace's rooms, so there is no workspace to resolve.
        self._standing_store = standing

    async def assemble(self, *, request: EngagementRequest) -> TagContext:
        msg = request.message
        # Resolved BEFORE the gather because two of the eight reads are keyed on
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
        # here: one exception would otherwise cancel its six siblings and take
        # the turn down with it. That is the same property `MemoryStore`'s
        # "never load-bearing" rule already claims — this just stops relying on
        # the adapter to honour it. `_room_language` (Task 10), `_room_exchange`
        # (Dreamer Plan 2) and `_room_portrait` (Dreamer Plan 3) all hold the
        # same property.
        # NESTED gather, not one flat call: `asyncio.gather`'s stub only has
        # distinctly-typed overloads up to six positional awaitables — a
        # SEVENTH (`_room_portrait`, Dreamer Plan 3) falls through to the
        # homogeneous `Future[list[_T]]` overload, where mypy joins all seven
        # return types down to `object`. Nesting keeps every branch within
        # the typed overloads AND keeps all eight leaf reads scheduled in the
        # same wave: the outer `gather` wraps each inner `gather(...)`
        # coroutine into a `Task` immediately, so the inner ones' own
        # children start concurrently too — nothing here becomes sequential.
        #
        # The NINTH (`_standing`) joins the FIRST branch, which had four, rather
        # than the second, which now has five: both stay inside the six the
        # `asyncio.gather` overloads type distinctly, and the first branch is
        # where it belongs on grounds other than arity — `_standing` is keyed on
        # `(platform, conversation_id)` like `_recent` beside it, where all four
        # reads in the second branch are keyed on a workspace or a scope.
        #
        # The EIGHTH (`_notes`, the note tier) joins the second branch rather
        # than adding a third: both branches stay at four, well inside the six
        # the overloads type, and the outer `gather` stays at two. It sits with
        # the other prefill-block reads even though it is the one scoped by
        # workspace and not by room — grouping is about arity here, and where a
        # read is scheduled says nothing about what it is keyed on.
        (
            (transcript, card, self_card, tagger_card, standing),
            (room_language, room_exchange, room_portrait, notes, facts),
        ) = await asyncio.gather(
            asyncio.gather(
                self._recent(msg),
                self._peer_card(ref, msg),
                self._recall_self(msg),
                self._recall_tagger(ref, msg),
                self._standing(msg),
            ),
            asyncio.gather(
                self._room_language(msg),
                self._room_exchange(msg),
                self._room_portrait(msg),
                self._notes(msg),
                self._standing_facts_unused(),
            ),
        )

        # One name space for the whole prompt (DEC-80) — see `Persons.name_transcript`.
        transcript = self._persons.name_transcript(transcript, platform=msg.platform)

        # **The fact block is read AFTER the transcript, and that is a
        # deliberate latency decision** (19 §6, operator 2026-08-28). Its room
        # slice is "facts whose subject is an entity this conversation has
        # touched", and the only honest source for what the conversation has
        # touched is the transcript — which the gather above reads in PARALLEL
        # with everything else, so a fact read inside it cannot see one.
        #
        # The two alternatives were both refused with reasons. Keying the slice
        # on the tagging message alone needs no extra read and is empty most
        # turns, which makes a block claiming to be about what the room is
        # discussing actually about one message. Filtering a wider recency read
        # afterwards costs nothing and silently misses any fact outside that
        # window — a subset presented as the whole, which is the exact defect
        # the extent line exists to stop, rebuilt one level down.
        #
        # So it is one extra sequential round trip: a single indexed read, on a
        # path whose latency histogram starts at 0.1s. That is a PRIOR and not
        # a measurement, which is why it is measured on the real endpoint after
        # this lands rather than asserted here.
        facts = await self._facts(msg, transcript=transcript, tagger=ref)

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
            room_exchange=room_exchange,
            room_portrait=room_portrait,
            notes=notes,
            facts=facts,
            standing=standing,
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
        principle this catch is unreachable — but it is now one of five siblings
        in a `gather`, where an unreachable exception stops being a thinner
        prompt and becomes four cancelled reads and a dead turn.
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

    async def _room_exchange(self, msg: NormalizedMessage) -> RoomExchange:
        """Dreamer Plan 2's own sibling of `_room_language` — same reasoning,
        same guard: `exchange=None` (not yet wired) and the store's own
        `RoomExchangeReader.room_exchange` never-raises guarantee both mean
        this catch should be unreachable, but it is now one of SIX siblings
        in the `gather`, where an uncaught exception cancels every other
        read."""
        if self._exchange is None:
            return RoomExchange(scope_id=msg.conversation_id)
        try:
            return await self._exchange.room_exchange(
                scope_id=msg.conversation_id, limit=self._room_exchange_store_limit,
            )
        except Exception:
            log.exception("room-exchange read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="room_exchange", platform=msg.platform)
            return RoomExchange(scope_id=msg.conversation_id)

    async def _standing_facts_unused(self) -> None:
        """A placeholder keeping the second gather at five members while the
        fact read moves out of it. Five is what the `asyncio.gather` overloads
        type against; dropping to four would work and would also make the two
        branches asymmetric for a reason nobody reading them could see."""
        return None

    async def _facts(
        self, msg: NormalizedMessage, *,
        transcript: tuple[TranscriptRow, ...],
        tagger: PersonRef | None,
    ) -> FactsView:
        """The `<facts>` block's three slices (19 §6).

        **One block, three labelled slices** — room (8), sender (4), and an
        extent line that is counts only. Three sibling blocks would read as
        three unrelated systems rather than one store seen from three angles.

        **The one block whose content is not derived from the room it renders
        into**, which is why the workspace lookup is here rather than a
        conversation id passed straight through. Within one workspace this
        crosses no boundary R-31 guards — DEC-105/R-49's own reasoning, that
        rooms sharing a workspace are one social unit. That argument is
        borrowed, and it is only as strong as the workspace configuration is
        honest.

        **Never raises**, the same guard its siblings carry and for a reason
        that survives the move out of the gather: a thinner reply beats no
        reply, and every read below already degrades to nothing on its own.

        **The extent is read even when both slices are empty.** That is the
        whole point of it: a block that vanishes when nothing matched tells her
        nothing exists, and one saying she holds forty and is being shown none
        tells her the verb is worth reaching for.
        """
        reader = self._facts_reader
        if reader is None:
            return FactsView()
        workspace = _workspace_of(self._policy, msg)
        try:
            subjects = await self._subjects_in_play(
                reader, workspace=workspace, transcript=transcript, msg=msg,
            )
            room = await reader.standing_facts(
                workspace=workspace,
                subject_ids=subjects,
                limit=self._max_room_facts,
            )
            sender: tuple[Fact, ...] = ()
            if tagger is not None:
                sender = await reader.facts_asserted_by(
                    workspace=workspace,
                    person_id=tagger.person_id,
                    limit=self._max_sender_facts,
                )
            standing = await reader.standing_count(
                workspace=workspace,
            )
        except Exception:
            log.exception("fact read failed; continuing without the block")
            self._metrics.inc(
                "nora_errors_total", stage="facts", platform=msg.platform)
            return FactsView()

        shown = {f.id for f in room} | {f.id for f in sender}
        return FactsView(
            room=room, sender=sender,
            extent=FactExtent(standing=standing, shown=len(shown)),
            asserters=await self._asserters_for(reader, room + sender),
        )

    async def _subjects_in_play(
        self, reader: FactReader, *, workspace: str,
        transcript: tuple[TranscriptRow, ...], msg: NormalizedMessage,
    ) -> tuple[str, ...]:
        """The subjects this conversation has TOUCHED — 19 §6's room slice.

        Matched by normalised containment against the transcript and the
        tagging message, the same matcher recognition uses and for the same
        reasons: the job is «does this text talk about X», which is a substring
        question, and a similarity threshold nobody can calibrate would look
        like evidence. It is also collation-independent, which matters on a
        database about to be reindexed under a different libc's ordering.

        `standing_subjects` is bounded by the workspace, and the cost of the
        scan is what `nora_facts_subject_scan` reports — the RESULT here is a
        list of subjects, and a correct list is compatible with any cost.
        """
        haystack = normalise_name(
            " ".join([msg.body or "", *(r.body or "" for r in transcript)])
        )
        if not haystack:
            return ()
        subjects = await reader.standing_subjects(workspace=workspace)
        return tuple(
            subject for subject in subjects
            if mentions(haystack, normalise_name(subject))
        )

    async def _asserters_for(
        self, reader: FactReader, facts: tuple[Fact, ...],
    ) -> dict[str, tuple[str, ...]]:
        """Who said each `stated` fact — 30 §2.3, and the read that makes a
        prank legible rather than authoritative.

        Only for `stated` rows: every other warrant renders what backs it
        without a person, so asking would be one query per fact for a string
        nothing shows.
        """
        out: dict[str, tuple[str, ...]] = {}
        for fact in facts:
            if fact.warrant_kind != "stated":
                continue
            rows = await reader.asserters(fact_id=fact.id)
            if rows:
                out[fact.id] = tuple(dict.fromkeys(a.person_id for a in rows))
        return out

    async def _room_portrait(self, msg: NormalizedMessage) -> RoomPortrait:
        """Dreamer Plan 3's own sibling of `_room_language`/`_room_exchange`
        — same reasoning, same guard. No store-limit parameter: unlike its
        two siblings, `RoomPortraitReader.room_portrait` takes no `limit` —
        a portrait is one row per scope by construction, not a top-N read
        (see `nora.language.portrait_synthesizer`'s module docstring)."""
        if self._portrait is None:
            return RoomPortrait(scope_id=msg.conversation_id)
        try:
            return await self._portrait.room_portrait(scope_id=msg.conversation_id)
        except Exception:
            log.exception("room-portrait read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="room_portrait", platform=msg.platform)
            return RoomPortrait(scope_id=msg.conversation_id)

    async def _standing(self, msg: NormalizedMessage) -> Standing | None:
        """Her own position in THIS conversation, or `None`
        (`design/dreamer-agent-loop/26-standing.md` §4).

        **Keyed `(platform, conversation_id)`** — S4, and unlike `_notes`/
        `_facts` beside it there is no workspace to resolve and no `Policy` read
        here at all. A position is held in a room. Two rooms in one workspace
        share what the workspace KNOWS (facts) and what it has not settled
        (notes); they do not share where a conversation had got to, and keying
        this on the workspace would have one room's position rewritten by
        another room's turn.

        Three states, and they are deliberately not three: **no store** is
        `[standing] enabled = false`, the dormant default, not an error and not
        counted; **no row** is a conversation with no position yet, which
        `render_standing` renders as no block at all rather than as an empty
        opinion. Both answer `None`, and that is correct — the prompt cannot
        express the difference and nothing downstream needs it.

        The THIRD state is a failed read, and it is counted, because it is not
        the same thing at all: a position that exists and could not be fetched
        is `unavailable` reported as `nothing_found` if it is silently folded
        into the two above (R-27). Fail-SOFT on the value, like every sibling —
        a thinner prompt beats no reply — but never a silent one.
        """
        if self._standing_store is None:
            return None
        try:
            return await self._standing_store.read(
                platform=msg.platform, conversation_id=msg.conversation_id,
            )
        except Exception:
            log.exception("standing read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="standing", platform=msg.platform)
            return None

    async def _notes(self, msg: NormalizedMessage) -> tuple[Note, ...]:
        """The standing note index (2026-08-21 note-tier design §5.1).

        **KEYED ON THE WORKSPACE, NOT THE ROOM — the one read in this class
        that is.** Every sibling above passes `msg.conversation_id`; this one
        passes `Conversation.workspace`, and the difference is deliberate
        rather than an oversight to be tidied up later. §3/DEC-105: rooms
        sharing a workspace are ONE social unit — the boundary `send_to`
        already relays across — so a note raised in the group's Discord channel
        belongs in its Signal room's index too, and `policy.toml`'s workspace
        key is what keeps a different social unit's notes out. `established_in`
        (the room a note was born in) is a routing key for promotion, never a
        filter on who sees it.

        Two ways this answers `()` rather than reading, and they are different
        states wearing the same face:

        - **No store** — `[notes] enabled = false`, the dormant default. Not an
          error, not counted; the same "not yet wired" shape as `language`/
          `exchange`/`portrait` above.
        - **No `Conversation` for this room** — a store is wired but there is
          no workspace to scope it by. That is a WIRING GAP (engagement and
          ingest disagreeing about which `Policy` they hold — see
          `EngagementPipeline`'s own `convo is not None` note), so it defaults
          safely AND is counted: guessing a workspace would render another
          unit's notes into this room, and staying silent about it is how a
          block goes missing for the life of a deployment (`_peer_card`'s
          `nora_peer_card_empty_total` was bought with exactly that lesson).

        Fail-SOFT on the read itself, like every sibling: a thinner prompt
        beats no reply. `StandingNotesReader` is documented never to raise, but
        `SqlNoteStore.standing` is a bare `SELECT` with no guard of its own, so
        here the catch is a REACHABLE path rather than belt-and-braces — an
        unreachable database would otherwise cancel this read's three siblings
        in the `gather` and take down a turn that would have worked.
        """
        if self._notes_store is None:
            return ()
        convo = (
            self._policy.conversation(msg.platform, msg.conversation_id)
            if self._policy is not None
            else None
        )
        if convo is None:
            log.warning(
                "no policy conversation for %s/%s — the note index cannot be "
                "scoped to a workspace; continuing without it",
                msg.platform, msg.conversation_id,
            )
            self._metrics.inc(
                "nora_errors_total", stage="notes", platform=msg.platform)
            return ()
        try:
            standing = await self._notes_store.standing(workspace=convo.workspace)
        except Exception:
            log.exception("note-index read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="notes", platform=msg.platform)
            return ()
        # `config.toml`'s [notes] section names this metric as "the tell": with
        # no curator, nothing retires a note, so the reading that matters is
        # whether the index grows faster than it earns.
        #
        # It is published HERE and not from a new `gather` leaf in `assemble`,
        # because this read already happens and `assemble`'s nested gather has
        # distinctly-typed overloads only to six positional awaitables — a
        # seventh joins every return type down to `object` under mypy.
        #
        # And it is published on the SUCCESS path only. Both returns above are
        # `()` as well, and a gauge written there would export `0` for «the
        # tier is off» and for «I could not read the notebook» alike — the
        # `unavailable`-as-`nothing_found` collapse R-27 exists to prevent, in
        # the one series meant to answer «is the notebook empty?». The wiring
        # gap already has `nora_errors_total{stage="notes"}`; absence here is
        # what says the reading was not taken.
        self._metrics.gauge(
            "nora_note_index_entries", len(standing), workspace=convo.workspace)
        return standing
