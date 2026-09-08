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

from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
    TagContext,
    TranscriptRow,
)
from nora.domain.notes import Note
from nora.domain.ports import (
    EngagementRequest,
    MemoryStore,
    RecordStore,
    StandingStore,
)
from nora.domain.standing import Standing
from nora.persons import Persons
from nora.policy import Policy

log = logging.getLogger(__name__)


class StandingNotesReader(Protocol):
    """The one note-tier verb this assembler needs — `SqlNoteStore.standing`
    (2026-08-21 note-tier design §5.1), narrowed to a Protocol for the same
    reason every other collaborator here is one: `TranscriptInjection` takes
    Protocol-typed collaborators only, so it never imports `nora.infra`.

    Declared HERE rather than in `nora.domain.ports` because the prefill is
    the only reader that needs THIS verb: `note_read`/`note_search` reach the
    tier through `nora.agent.tools` and ask it different questions. The
    condition under which this Protocol would move to `ports` and become an
    alias — a second reader wanting `standing` itself — has not arrived. (It
    was written against the fact tier's own read half as the precedent; that
    tier was deleted on 2026-09-07, and the argument stands on its own.)

    **Never raises**, the promise every critical-path read in this module
    makes. `SqlNoteStore.standing` does not
    yet make it — it is a bare `SELECT` — which is why `_notes` guards rather
    than trusts, exactly as `_peer_card` does for a port that DOES promise it.
    """

    async def standing(self, *, workspace: str) -> tuple[Note, ...]:
        """Every note in `workspace` that has not been retired. Uncapped by
        design (§5.1): a note missing from the index is invisible to her, and
        invisible is indistinguishable from absent."""
        ...


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
        notes: StandingNotesReader | None = None,
        policy: Policy | None = None,
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
        # `notes` is `None` while `[notes] enabled = false` — the note tier
        # ships dormant, the same "not yet configured" shape `ai_peer=""`
        # already uses to disable the self/tagger card reads below, kept OFF
        # rather than raising so existing call sites keep working unchanged.
        self._notes_store = notes
        # policy.toml, ONLY so `_notes` can name the workspace a room belongs
        # to (`Conversation.workspace`, DEC-105). Nothing else in this class
        # reads it, and nothing here decides admission — by the time a tag
        # reaches the assembler ingest has already applied the allowlist
        # (DEC-18). Passing the Policy rather than a resolved workspace string
        # is what keeps this correct once a deployment runs two social units:
        # the workspace is a property of the ROOM, resolved per message.
        self._policy = policy
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
        # unlike `notes`: a position is held in a room, not shared
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
        # the adapter to honour it. Every read left in the gather holds the
        # same property.
        # ONE FLAT gather of six. It was a NESTED gather while there were
        # eight and then nine leaf reads — `asyncio.gather`'s stub only types
        # six positional awaitables distinctly, and a seventh falls through to
        # the homogeneous overload where mypy joins every return type down to
        # `object`. The room-language, room-exchange and room-portrait reads
        # went with the language tier on 2026-09-07 and the facts read with the
        # knowledge tier, which put the count back inside the typed overloads,
        # so the nesting had nothing left to buy.
        #
        # `_notes` is the one read scoped by WORKSPACE rather than by room, and
        # it is gathered beside the rest anyway — where a read is scheduled says
        # nothing about what it is keyed on.
        (
            transcript, card, self_card, tagger_card, standing, notes,
        ) = await asyncio.gather(
            self._recent(msg),
            self._peer_card(ref, msg),
            self._recall_self(msg),
            self._recall_tagger(ref, msg),
            self._standing(msg),
            self._notes(msg),
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
            notes=notes,
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

    async def _standing(self, msg: NormalizedMessage) -> Standing | None:
        """Her own position in THIS conversation, or `None`
        (`design/dreamer-agent-loop/26-standing.md` §4).

        **Keyed `(platform, conversation_id)`** — S4, and unlike `_notes`
        beside it there is no workspace to resolve and no `Policy` read
        here at all. A position is held in a room. Two rooms in one workspace
        share what the workspace has not settled (notes); they do not share
        where a conversation had got to, and keying
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
          error, not counted. It is the last read in this assembler that can
          be dormant: `language`/`exchange`/`portrait` were the other three
          and went with their tier on 2026-09-07 (see the gather's own note
          above).
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
