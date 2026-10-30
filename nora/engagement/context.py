"""`TranscriptInjection` — assembles the `TagContext` a tag reply needs.
Replaces `Handler._collect_context`; this module preserves its two
failure-shaped behaviours:

- A transcript read failure logs, counts `nora_errors_total{stage="context"}`,
  and continues with an EMPTY transcript rather than raising — a thinner
  reply beats no reply (R-5-adjacent).
- An unmapped sender (`Persons.resolve` returns `None`) yields `tagger=None`
  and an empty peer card, with NO Honcho call at all: `memory.peer_card` is
  only ever called for a sender we can name.

Takes an `EngagementRequest`, not a bare message, because `decision` is the
only route by which a classifier's `rationale` can reach the prefill; `assemble`
accepts it even though nothing reads it yet, so that seam needs no signature
change later.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple, Protocol

from nora.agent.clock import local_now
from nora.agent.egress import scan_identifiers
from nora.agent.lineage import LineagePolicy, profile_lines_for
from nora.agent.memory_search import (
    PREFETCH_TOKENS,
    MemorySearch,
)
from nora.agent.room import Room
from nora.agent.standing_ledger import read_ledger
from nora.domain.comprehend import ComprehendItem, Preview, preview_seeds
from nora.domain.computer_context import ComputerView
from nora.domain.culture import (
    CultureRevision,
    character_key,
    nora_character_key,
    unit_culture_key,
)
from nora.domain.documents_context import DocumentsView
from nora.domain.external_files import render_file_links
from nora.domain.lineage import RenderRoom
from nora.domain.media_join import MediaJoin
from nora.domain.memo import Memo, MemoStore
from nora.domain.memory_graph import Subgraph, WorkItem
from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
    TagContext,
    TranscriptRow,
)
from nora.domain.notes import Note
from nora.domain.ports import (
    CultureReader,
    EngagementRequest,
    MemoryStore,
    RecordStore,
    RosterSource,
    StandingStore,
)
from nora.domain.profile import ProfileView
from nora.domain.quote_window import QuoteWindow
from nora.domain.rollup import room_key
from nora.domain.self_presence import Presence, platform_name
from nora.domain.standing import Standing
from nora.domain.standing_ledger import LedgerEntry, StandingLedger
from nora.domain.subagents_context import SubagentsView
from nora.engagement.character_who import tag_character_people
from nora.engagement.computer_context import ComputerContextReader
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.self_presence import PresenceReader
from nora.engagement.subagents_context import SubagentsContextReader
from nora.language.comprehend_render import preview_text
from nora.language.computer_render import estimate_tokens
from nora.language.culture_render import read_character, read_culture
from nora.language.lesson_render import ActiveLessons, read_lessons
from nora.language.profile_render import ProfileHeads, authored_identity, read_profile
from nora.obs.tracing import traced_read
from nora.persons import Persons
from nora.policy import Policy

log = logging.getLogger(__name__)


class StandingNotesReader(Protocol):
    """The one note-tier verb this assembler needs — `SqlNoteStore.standing`,
    narrowed to a Protocol for the same reason every other collaborator here is
    one: `TranscriptInjection` takes Protocol-typed collaborators only, so it
    never imports `nora.infra`.

    Declared HERE rather than in `nora.domain.ports` because the prefill is
    the only reader that needs THIS verb: `note_read`/`note_search` reach the
    tier through `nora.agent.tools` and ask it different questions. It would
    move to `ports` as an alias if a second reader wanted `standing` itself.

    **Never raises**, the promise every critical-path read in this module
    makes. `SqlNoteStore.standing` does not yet make it — it is a bare
    `SELECT` — which is why `_notes` guards rather than trusts, exactly as
    `_peer_card` does for a port that DOES promise it.
    """

    async def standing(self, *, unit: str) -> tuple[Note, ...]:
        """Every note in `unit` that has not been retired. Uncapped by
        design: a note missing from the index is invisible to her, and
        invisible is indistinguishable from absent."""
        ...


class _Prefetch(NamedTuple):
    """One scoped graph read and its frozen render inputs."""

    target: RenderRoom
    found: dict[str, Any]
    graph: Subgraph | None
    now: datetime


#: The pre-fetch's bound: the embedder's own query timeout (10 s,
#: `nora.infra.embedder.QUERY_TIMEOUT_SECONDS`) and a few seconds for the
#: walk's reads, which the engine does not bound. Past it the turn goes on
#: without the block, counted, as a slow culture read does.
#: ponytail: a fixed bound read from no trace yet; size it from the
#: `store.memory_index.prefetch` span's latency once the flag is on.
PREFETCH_TIMEOUT_SECONDS = 12.0

#: What the shown transcript already carries, so the pre-fetch never spends
#: its budget showing it again: this room's messages and their attachments.
#: ponytail: dropped after the search's own cut (`RESULT_MAX`, the pins), so
#: a shown row can take a slot there; pass the window into the search if a
#: block comes up short for it.
_TRANSCRIPT_KINDS = frozenset({"message", "media"})


class TranscriptInjection:
    """Satisfies `nora.domain.ports.ContextAssembler`."""

    def __init__(
        self,
        *,
        audit: RecordStore,
        memory: MemoryStore,
        persons: Persons,
        context_window: int,
        prompt_window: int | None = None,
        metrics: Any,
        quote_window: QuoteWindow | None = None,
        ai_peer: str = "",
        tagger_card_conclusions: int = 0,
        notes: StandingNotesReader | None = None,
        policy: Policy | None = None,
        standing: StandingStore | None = None,
        ledger: StandingLedger | None = None,
        media_join: MediaJoin | None = None,
        presence: PresenceReader | None = None,
        culture: CultureReader | None = None,
        lessons: ActiveLessons | None = None,
        lineage: LineagePolicy | None = None,
        computer: ComputerContextReader | None = None,
        documents: DocumentsContextReader | None = None,
        subagents: SubagentsContextReader | None = None,
        memos: MemoStore | None = None,
        memory_search: MemorySearch | None = None,
        clock: Callable[[], datetime] | None = None,
        profile: ProfileHeads | None = None,
        rosters: Mapping[str, RosterSource] | None = None,
        self_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._rosters = rosters or {}
        self._self_ids = self_ids
        self._audit = audit
        # `<recalled>`, the pre-fetch: the SAME retrieval service the tool
        # registry's `memory_search` holds, `None` on the Honcho surface
        # (`[memory.index] read = false`) and in every harness that wires
        # none, which renders no block.
        self._memory_search = memory_search
        # The graph's one clock (`build_dependencies`' `clock`), so a harness
        # at a fixed instant ranks and dates the pre-fetch at that instant.
        # `None` is the wall clock.
        self._clock = clock or local_now
        # `<memos>` (DEC-306): the unit's kept memos, unit-wide like `notes`.
        # `None` renders no block.
        self._memos_store = memos
        self._memory = memory
        self._persons = persons
        self._context_window = context_window
        # `None` renders everything that was read (every harness that predates
        # `[store] prompt_window`); a number renders at most that many rows,
        # never reaching back less far than the quoted parent.
        self._prompt_window = prompt_window
        self._quote_window = quote_window
        self._metrics = metrics
        self._ai_peer = ai_peer
        self._tagger_card_conclusions = tagger_card_conclusions
        # `notes` is `None` while `[notes] enabled = false`: the note tier is
        # dormant, the same "not configured" shape `ai_peer=""` uses to disable
        # the self/tagger card reads below.
        self._notes_store = notes
        # policy.toml, ONLY so `_notes` can name the unit a room belongs
        # to (`Conversation.unit`, DEC-105). Nothing else in this class
        # reads it, and nothing here decides admission — by the time a tag
        # reaches the assembler ingest has already applied the allowlist
        # (DEC-18). Passing the Policy rather than a resolved unit string
        # keeps this correct when a deployment runs two social units: the unit
        # is a property of the ROOM, resolved per message.
        self._policy = policy
        # Standing. `None` while `[standing] enabled = false`.
        #
        # **It is the whole `StandingStore`, not a read-only narrowing**, which
        # differs from every other reader above. `nora.domain.ports.StandingStore`
        # has two verbs and the debrief stage needs both, so narrowing it here
        # would mean constructing the store twice or threading two names for one
        # object — and the fold requires that the READ and the WRITE are the
        # same row seen twice. One port, one instance, two callers.
        #
        # It is keyed `(platform, conversation_id)` and needs no `Policy`,
        # unlike `notes`: a position is held in a room, not shared
        # across a unit's rooms, so there is no unit to resolve.
        self._standing_store = standing
        self._ledger_store = ledger
        # THE MEDIA JOIN. `messages.body` carries `[image a3f2c1]` and nothing
        # more; the comprehension lives in `media_objects`, and the join is
        # where the two meet — in the ASSEMBLER and not in
        # `nora/agent/prefill.py`, which is a pure renderer over `TagContext`
        # and must not gain a store. `None` is the inert state and renders
        # exactly what is stored. The object is shared with the other transcript
        # builders; see `nora.domain.media_join`.
        self._media_join = media_join
        # WHERE SHE IS: the room, the unit's other rooms with live transport
        # state, her handles. `None` is a harness with no reader, and the
        # prefill then renders the network alone.
        self._presence_reader = presence
        # THE ROOM'S OWN ACCOUNT (DEC-207). `None` while `[culture] enabled =
        # false`. Read-only by TYPE: a `CultureReader` has `head` and nothing
        # else, so no tag turn can write a culture row. Keyed on the room's
        # `primary_id`, resolved through `policy` — see `_culture`.
        self._culture_reader = culture
        # `<lessons>`: `None` while `[lessons] render` is off.
        self._lessons_reader = lessons
        # WHO THIS ROOM IS, for lineage. A pure value built at the composition
        # root; `None` is `[lineage] mode = "off"` for a harness that wires none.
        # No read: the room's facts are policy, and the live roster the
        # `presence` predicate needs is not read here (a predicate that needs the
        # roster denies without it).
        self._lineage = lineage
        # `<computer>`. `None` while `[computer]` is off; the reader itself
        # gates each room and never raises, so it is safe inside the gather.
        self._computer_reader = computer
        # `<documents>`. `None` while `[documents]` is off; the reader never
        # raises, so it is safe inside the gather.
        self._documents_reader = documents
        # `<subagents>`. `None` while `[subagent]` is off; the reader never
        # raises, so it is safe inside the gather. Tag path only: the fire path
        # holds no such reader.
        self._subagents_reader = subagents
        # THE SWITCH (45 §5): the Profile accounts' reader, wired by
        # composition only when `[memory.profile] read` is on. Not `None` means this
        # turn renders `<profile>` blocks and the seven reads they replace —
        # `_peer_card`, `_recall_tagger`, `_culture`, `_unit_culture`,
        # `_self_account` and `_character` — are not made at all: a switched
        # turn never pays for both surfaces, and a failed profile read is
        # `nora_errors_total` (R-27), never a quiet fall back to the old block.
        self._profile_reader = profile

    async def assemble(self, *, request: EngagementRequest) -> TagContext:
        msg = request.message
        # Resolved BEFORE the gather because reads in it are keyed on it, and it
        # is a pure dict lookup — no reason for it to be a step.
        ref = self._persons.resolve(msg.platform, msg.sender_id)

        # CONCURRENT: the reads are independent, so gathering hides each behind
        # the slowest instead of stacking their round trips (transcript, Honcho)
        # end to end at the FRONT of every tag, before the first model call.
        #
        # Every helper is written not to raise, which is what makes `gather` safe
        # here: one exception would otherwise cancel its siblings and take the
        # turn down with it. That is the same property `MemoryStore`'s "never
        # load-bearing" rule claims; this does not rely on the adapter to
        # honour it.
        #
        # The gather is nested because `asyncio.gather`'s stub types only six
        # positional awaitables distinctly; a seventh falls through to the
        # homogeneous overload where mypy joins every return type down to
        # `object`. Each inner gather stays within the typed overloads.
        #
        # `_notes` is the one read scoped by UNIT rather than by room, and it is
        # gathered beside the rest anyway — where a read is scheduled says
        # nothing about what it is keyed on.
        #
        # `<presence>` is on the front of the turn like the rest because adapter
        # health must be read now, not remembered.
        async def transcript_and_prefetch() -> Any:
            transcript, character, profiles = await self._recent_with_character(msg, ref)
            prefetch = await self._prefetch(msg, ref, transcript)
            return transcript, character, profiles, prefetch

        (
            ((transcript, character, profiles, prefetch),
             card, tagger_card, standing, notes, ledger),
            presence,
            culture,
            (unit_culture, self_account, lessons),
            (computer, documents, subagents, memos),
        ) = await asyncio.gather(
            asyncio.gather(
                transcript_and_prefetch(),
                self._peer_card(ref, msg),
                self._recall_tagger(ref, msg),
                self._standing(msg),
                self._notes(msg),
                self._ledger(msg, ref),
            ),
            self._presence(msg),
            self._culture(msg),
            asyncio.gather(
                self._unit_culture(msg), self._self_account(msg), self._lessons(msg),
            ),
            asyncio.gather(
                self._computer(msg),
                self._documents(msg, ref),
                self._subagents(msg, ref),
                self._memos(msg),
            ),
        )

        # One name space for the whole prompt (DEC-80) — see `Persons.name_transcript`.
        transcript = self._persons.name_transcript(transcript, platform=msg.platform)
        # THEN the media join, after the transcript read and before the context
        # is frozen. It is a second round trip, so it is NOT in the gather above:
        # it needs the rows the gather returns. ONE bounded query per
        # `DEFAULT_QUERY_BLOBS` distinct blobs, and it degrades to the stored
        # body — which already says `[image a3f2c1]`, so a join that does not
        # happen claims nothing.
        #
        # `MediaJoin` is SHARED with the fire path, the standing fold, the
        # Dreamer's batch, lazy adoption and `load_transcript`: every transcript
        # builder gives one answer to «what did that photo show».
        if self._media_join is not None:
            transcript = await traced_read(
                "media_join", "enrich", self._media_join.enrich(transcript, path="tag"),
            )

        # LAST, after naming and the media join, so the lookup rows read the
        # same as the shown ones. Empty when nothing was cut.
        shown = self._shown(transcript, msg)
        lookup = tuple(transcript) if len(shown) < len(transcript) else ()
        recalled = self._recalled(prefetch, msg, shown)

        return TagContext(
            tag_id=request.tag_id,
            message=msg,
            tagger=ref,
            transcript=shown,
            lookup_transcript=lookup,
            peer_card=card,
            started_at=time.perf_counter(),
            tagger_card=tagger_card,
            notes=notes,
            memos=memos,
            standing=standing,
            ledger=ledger,
            presence=presence,
            culture=culture,
            character=character,
            profiles=profiles,
            unit_culture=unit_culture,
            self_account=self_account,
            lessons=lessons,
            computer=computer,
            documents=documents,
            subagents=subagents,
            recalled=recalled,
            recalled_partial=bool(recalled) and prefetch is not None
            and prefetch.found["status"] == "partial",
            # A pure lookup (policy.toml, identity.toml), no read: the room's
            # listed persons and their aliases for `<room_members>`.
            room_members=self._room_members(msg),
            lineage_room=(
                self._lineage.render_room(
                    platform=msg.platform, conversation_id=msg.conversation_id,
                )
                if self._lineage is not None else None
            ),
        )

    async def _prefetch(
        self, msg: NormalizedMessage, ref: PersonRef | None,
        transcript: Sequence[TranscriptRow] = (),
    ) -> _Prefetch | None:
        """Read the tools' scoped graph from structural anchors and index hits."""
        if self._memory_search is None or self._lineage is None:
            return None
        target = self._lineage.render_room(
            platform=msg.platform, conversation_id=msg.conversation_id,
        )
        if target.room is None:
            return None
        listed = target.room.persons
        mentioned = [
            r.person_id for v in sorted(msg.mentions)
            if (r := self._persons.resolve(msg.platform, v)) is not None
        ]
        about = list(dict.fromkeys(
            ([ref.person_id] if ref is not None else []) + mentioned,
        ))
        media = [b.sha256 for b in msg.media_blobs]
        query = render_file_links(msg.body or "")[:500]
        # In the process's own zone, the zone of the clock line her prompt
        # opens on, so an item's date reads on the same calendar.
        now = self._clock().astimezone()
        try:
            item = ComprehendItem(
                WorkItem("prefetch", ("unit", f"{target.room.unit}:unit:{target.room.unit}"),
                         target.room.unit, "", now),
                (), msg.platform, msg.conversation_id,
                author=ref.person_id if ref else "", mentions=tuple(mentioned),
                reply_parent=next((("message", str(r.id)) for r in transcript
                    if msg.quote is not None and r.ts == msg.quote.quoted_ts), None))
            # ponytail: prefetch cache is local; thread it through TagContext to
            # the tools' graph_reads when sharing automatic and tool walks.
            cache: dict[str, Any] = {}
            found = await asyncio.wait_for(traced_read(
                "memory_index", "prefetch", self._memory_search.read(
                    query=query, depth=2, person_id=None, target=target,
                    now_ms=int(now.timestamp() * 1000), people=self._room_members(msg),
                    social_off=self._persons.social_off_ids(listed or ()),
                    about=about, media=media,
                    anchors=preview_seeds(item, {}), cache=cache,
                    content_checks=target.content_checks,
                ),
                count=lambda r: sum(len(g.nodes) for _, g in cache.get("_subgraphs", [])
                                    if g is not None),
            ), timeout=PREFETCH_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 — the bound, or anything else: no block, counted
            log.warning("memory pre-fetch did not answer; continuing without it")
            self._metrics.inc("nora_errors_total", stage="tag_prefetch", platform=msg.platform)
            return None
        if found["status"] == "unavailable":
            self._metrics.inc("nora_errors_total", stage="tag_prefetch", platform=msg.platform)
        graphs = [g for _, g in cache.get("_subgraphs", []) if g is not None]
        return _Prefetch(target, found, graphs[0] if graphs else None, now)

    def _recalled(
        self, prefetch: _Prefetch | None, msg: NormalizedMessage,
        shown: Sequence[TranscriptRow],
    ) -> tuple[dict[str, Any], ...]:
        """Render the scoped graph, omitting transcript bodies already shown."""
        if prefetch is None or prefetch.found["status"] not in ("ok", "partial"):
            return ()
        try:
            return self._render_recalled(prefetch, msg, shown)
        except Exception:
            log.exception("memory pre-fetch render failed; continuing without it")
            self._metrics.inc("nora_errors_total", stage="tag_prefetch", platform=msg.platform)
            return ()

    def _render_recalled(
        self, prefetch: _Prefetch, msg: NormalizedMessage,
        shown: Sequence[TranscriptRow],
    ) -> tuple[dict[str, Any], ...]:
        here = room_key(msg.platform, msg.conversation_id)
        since = min((row.ts for row in shown), default=msg.timestamp)
        from dataclasses import replace

        if prefetch.graph is None:
            return ()
        nodes = tuple(n for n in prefetch.graph.nodes if not (
            n.indexed is not None and n.key[0] in _TRANSCRIPT_KINDS
            and n.indexed.origin_room == here and n.indexed.at_ms is not None
            and n.indexed.at_ms >= since))
        # Keep semantic edges when their source is already in the transcript.
        graph = replace(prefetch.graph, nodes=nodes)
        if not nodes and not graph.relations:
            return ()
        text = preview_text(Preview((), graph, (), (prefetch.now, prefetch.now)),
                            tokens=PREFETCH_TOKENS)
        while estimate_tokens(json.dumps({"text": text}, ensure_ascii=False)) > PREFETCH_TOKENS:
            text = text.rsplit("\n", 1)[0].replace("more=none", "more=available", 1)
        if scan_identifiers(text, content_checks=prefetch.target.content_checks):
            raise ValueError("unsafe graph preview")
        if text.startswith("more=available"):
            prefetch.found["status"] = "partial"
        return ({"text": text},)

    def _room_members(self, msg: NormalizedMessage) -> tuple[PersonRef, ...]:
        """This room's listed persons, for `<room_members>`; empty for a room
        outside the policy or a `persons = "*"` room (`Persons.room_members`)."""
        if self._policy is None:
            return ()
        conv = self._policy.conversation(msg.platform, msg.conversation_id)
        return self._persons.room_members(conv.persons) if conv is not None else ()

    async def _culture(self, msg: NormalizedMessage) -> CultureRevision | None:
        """This room's account, or `None` (DEC-207).

        Keyed on the room's `primary_id`, NEVER `msg.conversation_id`: the fold
        is a Dreamer adaptor handed `scopes()`, so that is what it wrote under,
        and a read by the arriving thread's id matches zero rows on a room
        holding two ids — rendering as «no account yet», a real state nothing
        would report (DEC-181). A room outside the policy has no
        key and reads nothing.

        Bounded, fail-soft and counted (`stage="tag_culture"`) by
        `read_culture`, so it is safe inside the `gather`.
        """
        if self._profile_reader is not None:
            # The switch (45 §5): the room's `<profile>` replaced this block;
            # a switched turn never pays for both surfaces.
            return None
        if self._culture_reader is None or self._policy is None:
            return None
        convo = self._policy.conversation(msg.platform, msg.conversation_id)
        if convo is None or not convo.primary_id:
            return None
        # `traced_read` wraps the coroutine `read_culture` itself awaits
        # (under `asyncio.wait_for`, hence this class rather than
        # `nora.language.culture_render` — that timeout must stay outside the
        # span, not inside it): a cancellation from the bound propagates
        # through `span`'s own `BaseException` handler before `wait_for`
        # converts it to the `TimeoutError` `read_culture` catches, so the
        # span still ends ERROR on a read that timed out.
        found = await read_culture(
            traced_read("culture", "head", self._culture_reader.head(key=convo.primary_id)),
            stage="tag_culture", platform=msg.platform, metrics=self._metrics,
        )
        return found

    async def _recent_with_character(
        self, msg: NormalizedMessage, ref: PersonRef | None,
    ) -> tuple[
        tuple[TranscriptRow, ...],
        tuple[tuple[str, CultureRevision], ...],
        tuple[ProfileView, ...],
    ]:
        """The transcript, then the accounts of the people in it. WHO needs the
        window's speakers, so the character read cannot start before the
        transcript; chained here it still overlaps every other read in the
        gather instead of waiting for all of them. Its own bound
        (`read_character`'s timeout) is added to the transcript's, not to the
        whole gather's.

        Switched (`[memory.profile] read`): the character read is not made at all
        and the turn's Profile accounts are read instead — the room's, the
        unit's, hers, and every person the turn is about."""
        read = await self._recent(msg)
        shown = self._shown(read, msg)
        if self._profile_reader is not None:
            return read, (), await self._profiles(msg, ref, shown)
        return read, await self._character(msg, ref, shown), ()

    def _platforms_of(self, person_id: str) -> tuple[str, ...]:
        """The display names of the networks one person is reachable on, for
        their authored identity lines — "Signal", "Discord", never a handle or
        a number (R-18). Read off the identity index, so a renamed platform
        cannot drift from what `resolve` matches."""
        return tuple(sorted({
            platform_name(platform)
            for (platform, _), pid in self._persons.index.items()
            if pid == person_id
        }))

    async def _profiles(
        self, msg: NormalizedMessage, ref: PersonRef | None,
        transcript: Sequence[TranscriptRow],
    ) -> tuple[ProfileView, ...]:
        """The turn's Profile accounts (45 §5): hers, the room's, the unit's,
        then one per person the turn is about (`tag_character_people`'s set,
        the same set `<character>` rendered).

        Reads share `read_profile` (bounded, fail-soft, counted under
        `nora_errors_total{stage="tag_profile_*"}`, R-27): a failed read drops
        THAT account's view, never the turn. A line whose `exclude_persons`
        names a member of this room is dropped here, before the view is built
        (`ProfileView`'s docstring) — the renderer never sees it. Views with
        nothing to say still land in the tuple; `render_profile` emits no
        block for them, and `ctx.profiles` being non-empty is the switch, not
        any one account's content."""
        reader = self._profile_reader
        if reader is None or self._policy is None:
            return ()
        convo = self._policy.conversation(msg.platform, msg.conversation_id)
        if convo is None or not convo.unit:
            return ()
        members = self._room_members(msg)
        target = (self._lineage or LineagePolicy.from_policy(self._policy)).render_room(
            platform=msg.platform, conversation_id=msg.conversation_id,
        )
        room = Room(
            platform=msg.platform, conversation_id=msg.conversation_id,
            roster_source=self._rosters.get(msg.platform), persons=self._persons,
            tagger=ref, self_ids=self._self_ids,
        )

        async def one(
            key: str, *, entity: str, of: str, stage: str,
            authored: tuple[str, ...] = (),
        ) -> ProfileView | None:
            lines = await read_profile(
                reader.account_heads(key=key), stage=stage,
                platform=msg.platform, metrics=self._metrics,
            )
            if lines is None:
                return None
            kept = await profile_lines_for(lines, target, room)
            return ProfileView(entity=entity, of=of, lines=kept, authored=authored)

        views: list[ProfileView] = []
        # HERS, gated like `_self_account` (R-31): written from her sends in
        # every room of the unit, so a room that may not see it reads nothing.
        if self._policy.shows_self_in(msg.platform, msg.conversation_id):
            view = await one(
                nora_character_key(convo.unit),
                entity="nora", of="nora", stage="tag_profile_self",
            )
            if view is not None:
                views.append(view)
        # THE ROOM's, keyed its `primary_id` for `_culture`'s reason (DEC-181).
        # `of` is the room's `policy.toml` label, its id in the prompt (ADR
        # 0029). `primary_id` is the store key, often a platform conversation
        # id, and never reaches a prompt. A room with no label shows no block.
        if convo.primary_id and convo.label:
            view = await one(
                convo.primary_id,
                entity="room", of=convo.label,
                stage="tag_profile_room",
            )
            if view is not None:
                views.append(view)
        # THE UNIT's, keyed like `_unit_culture`.
        view = await one(
            unit_culture_key(convo.unit),
            entity="unit", of=convo.unit, stage="tag_profile_unit",
        )
        if view is not None:
            views.append(view)
        # THEIRS: every listed person this turn is about, with their authored
        # identity lines (45 §3: rendered, never written).
        for person in tag_character_people(
            members, persons=self._persons, message=msg,
            tagger=ref, transcript=transcript,
        ):
            view = await one(
                character_key(convo.unit, person.person_id),
                entity="person", of=person.person_id, stage="tag_profile_person",
                authored=authored_identity(
                    name=person.name, aliases=person.aliases,
                    platforms=self._platforms_of(person.person_id),
                ),
            )
            if view is not None:
                views.append(view)
        return tuple(views)

    def _shown(
        self, read: Sequence[TranscriptRow], msg: NormalizedMessage,
    ) -> tuple[TranscriptRow, ...]:
        """The rows the prompt renders: the newest `prompt_window` of `read`,
        reaching further back only to include the quoted parent (R-20, DEC-195)
        so a reply never loses the message it answers. Deterministic in
        (`read`, `msg.quote`), so it can be applied again after the rows are
        named and enriched and land on the same rows."""
        rows = tuple(read)
        if self._prompt_window is None or len(rows) <= self._prompt_window:
            return rows
        start = len(rows) - self._prompt_window
        if msg.quote is not None:
            parent = next(
                (i for i, row in enumerate(rows) if row.ts == msg.quote.quoted_ts),
                None,
            )
            if parent is not None:
                start = min(start, parent)
        return rows[start:]

    async def _character(
        self, msg: NormalizedMessage, ref: PersonRef | None,
        transcript: Sequence[TranscriptRow],
    ) -> tuple[tuple[str, CultureRevision], ...]:
        """The accounts of the listed persons who are IN this turn (the
        tagger, the mentioned, the replied-to, the window's speakers:
        `tag_character_people`), keyed by this room's UNIT (one per
        (unit, person)). Bounded,
        fail-soft and counted (`stage="tag_character"`) by `read_character`."""
        if self._culture_reader is None or self._policy is None:
            return ()
        convo = self._policy.conversation(msg.platform, msg.conversation_id)
        if convo is None or not convo.unit:
            return ()
        return await read_character(
            self._culture_reader, unit=convo.unit,
            members=tag_character_people(
                self._room_members(msg), persons=self._persons, message=msg,
                tagger=ref, transcript=transcript,
            ),
            stage="tag_character", platform=msg.platform, metrics=self._metrics,
        )

    async def _unit_culture(self, msg: NormalizedMessage) -> CultureRevision | None:
        """What this room's UNIT is like, keyed
        `unit_culture_key(unit)`, so it is read only in that unit's rooms.
        Bounded, fail-soft and counted (`stage="tag_unit_culture"`) by
        `read_culture`, like `_culture`."""
        if self._profile_reader is not None:
            return None  # the switch (45 §5): the unit's `<profile>` replaced it
        if self._culture_reader is None or self._policy is None:
            return None
        convo = self._policy.conversation(msg.platform, msg.conversation_id)
        if convo is None or not convo.unit:
            return None
        return await read_culture(
            traced_read("culture", "head", self._culture_reader.head(
                key=unit_culture_key(convo.unit))),
            stage="tag_unit_culture", platform=msg.platform, metrics=self._metrics,
        )

    async def _self_account(self, msg: NormalizedMessage) -> CultureRevision | None:
        """Her own account in this room's UNIT, keyed
        `nora_character_key(unit)`. Bounded, fail-soft and counted
        (`stage="tag_self"`) by `read_culture`, like `_unit_culture`; `None`
        when the unit has no account or the room may not show it
        (`Policy.shows_self_in`), so no `<self>` block renders."""
        if self._profile_reader is not None:
            return None  # the switch (45 §5): her `<profile of="nora">` replaced it
        if self._culture_reader is None or self._policy is None:
            return None
        convo = self._policy.conversation(msg.platform, msg.conversation_id)
        if convo is None or not convo.unit:
            return None
        # R-31: unit-wide, written from location rooms' sends too.
        if not self._policy.shows_self_in(msg.platform, msg.conversation_id):
            return None
        return await read_culture(
            traced_read("culture", "head", self._culture_reader.head(
                key=nora_character_key(convo.unit))),
            stage="tag_self", platform=msg.platform, metrics=self._metrics,
        )

    async def _lessons(self, msg: NormalizedMessage) -> tuple[str, ...]:
        """Her active lessons in this room's UNIT,
        bounded, fail-soft and counted (`stage="tag_lessons"`); `()` with no
        reader (`[lessons] render` off), no unit, or a room that may not show
        `<self>` (`Policy.shows_self_in`, R-31): lessons are written from her
        sends in every room of the unit, as her account is."""
        if self._lessons_reader is None or self._policy is None:
            return ()
        convo = self._policy.conversation(msg.platform, msg.conversation_id)
        if convo is None or not convo.unit:
            return ()
        if not self._policy.shows_self_in(msg.platform, msg.conversation_id):
            return ()
        return await read_lessons(
            traced_read("lessons", "active", self._lessons_reader.bodies(unit=convo.unit)),
            stage="tag_lessons", platform=msg.platform, metrics=self._metrics,
        )

    async def _computer(self, msg: NormalizedMessage) -> ComputerView | None:
        """What is on her computer for this room, or `None` (no reader, or a
        room not using the computer). `ComputerContextReader.read` never
        raises."""
        if self._computer_reader is None:
            return None
        return await traced_read("computer", "read", self._computer_reader.read(
            platform=msg.platform, conversation_id=msg.conversation_id,
        ))

    async def _documents(
        self, msg: NormalizedMessage, ref: PersonRef | None,
    ) -> DocumentsView | None:
        """This room's newest documents the tagger may read, or `None` (no reader, none
        yet, no tagger, or the read failed). `DocumentsContextReader.read` never raises."""
        if self._documents_reader is None:
            return None
        return await traced_read("documents", "read", self._documents_reader.read(
            platform=msg.platform, conversation_id=msg.conversation_id,
            person_id=ref.person_id if ref is not None else None,
        ))

    async def _subagents(
        self, msg: NormalizedMessage, ref: PersonRef | None,
    ) -> SubagentsView | None:
        """This room's sub-agent jobs, or `None` (no reader, none in the window, or the read
        failed). The documents are titled for the tagger, `ref`, and for no one without one.
        `SubagentsContextReader.read` never raises."""
        if self._subagents_reader is None:
            return None
        return await traced_read("subagents", "read", self._subagents_reader.read(
            platform=msg.platform, conversation_id=msg.conversation_id,
            person_id=ref.person_id if ref is not None else None,
        ))

    async def _presence(self, msg: NormalizedMessage) -> Presence | None:
        """Where she is, read at turn start, or `None` with no reader wired.

        `PresenceReader.read` is written never to raise; guarded anyway for
        the reason `_peer_card` is — in a `gather`, an unreachable exception
        is not a thinner prompt but six cancelled reads and a dead turn.
        """
        if self._presence_reader is None:
            return None
        try:
            return await traced_read(
                "presence", "read", self._presence_reader.read(
                    platform=msg.platform, conversation_id=msg.conversation_id,
                    direct=msg.conversation_type == "dm",
                ),
            )
        except Exception:
            log.exception("presence read failed; continuing with the network alone")
            self._metrics.inc("nora_errors_total", stage="presence", platform=msg.platform)
            return None

    async def _recent(self, msg: NormalizedMessage) -> tuple[TranscriptRow, ...]:
        """The transcript, or an empty one — a thinner reply beats no reply.

        Delegates to `QuoteWindow.recent_for_quote` (DEC-195) when one was wired
        in, which is the tag path's own shape — `msg.quote` is the ONE quote to
        check, known before any fetch. With `quote_window is None` it falls back
        to the plain `context_window` fetch. Either way a read failure yields an
        empty transcript: a thinner reply beats no reply (R-5-adjacent).

        Both branches go through `traced_read` (`nora.obs.tracing`) as
        `store.record.recent` — the one wrapper this class's reads share.
        """
        if self._quote_window is not None:
            return await traced_read(
                "record", "recent", self._quote_window.recent_for_quote(
                    conversation_id=msg.conversation_id,
                    context_window=self._context_window,
                    quote=msg.quote, platform=msg.platform, stage="context",
                ),
            )
        try:
            return tuple(await traced_read(
                "record", "recent", self._audit.recent(
                    conversation_id=msg.conversation_id,
                    limit=self._context_window,
                ),
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
        if self._profile_reader is not None:
            return ()  # the switch (45 §5): the tagger's `<profile>` replaced it
        if ref is None:
            return ()
        try:
            card = tuple(await traced_read(
                "memory", "peer_card", self._memory.peer_card(
                    peer_id=ref.honcho_peer_id, conversation_id=msg.conversation_id,
                ),
            ))
            if not card:
                # THE ONE SIGNAL THIS READ HAS. `peer_card` answers `[]` for a
                # dead endpoint, a timeout, an unknown peer and a genuinely
                # empty card alike (`ports.py:500`), so the block simply is not
                # rendered and nothing says why. A COUNTER cannot say which of
                # the four it was — only `Recollection`'s two-axis shape can,
                # and giving `peer_card` that is a port change — but it makes
                # «her card stopped appearing» visible to an operator.
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

        Fail-open and silent, deliberately: an unreachable Honcho yields no
        block rather than a note saying she cannot remember. This is
        background she may draw on, never the ground for a claim. `about`
        remains the path for anything she will actually assert, and it
        reports `unavailable` honestly.
        """
        if self._profile_reader is not None:
            return ()  # the switch (45 §5): the tagger's `<profile>` replaced it
        if ref is None or not self._ai_peer or self._tagger_card_conclusions <= 0:
            return ()
        try:
            recall = await traced_read(
                "memory", "representation", self._memory.representation(
                    peer_id=self._ai_peer,
                    target=ref.honcho_peer_id,
                    search_query=render_file_links(msg.body or ""),
                    max_conclusions=self._tagger_card_conclusions,
                    conversation_id=msg.conversation_id,
                ),
                # `Recollection` has no `__len__` — `_row_count` would leave
                # this read unmeasured even though it plainly has a count.
                count=lambda r: len(r.conclusions),
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

    async def _ledger(
        self, msg: NormalizedMessage, ref: PersonRef | None,
    ) -> tuple[LedgerEntry, ...]:
        if self._ledger_store is None or self._lineage is None:
            return ()
        room = Room(platform=msg.platform, conversation_id=msg.conversation_id,
                    roster_source=self._rosters.get(msg.platform), persons=self._persons,
                    tagger=ref, self_ids=self._self_ids)
        target = self._lineage.render_room(
            platform=msg.platform, conversation_id=msg.conversation_id)
        try:
            return await asyncio.wait_for(read_ledger(self._ledger_store, target, room), timeout=5)
        except Exception:
            log.exception("ledger context read failed")
            self._metrics.inc("nora_errors_total", stage="standing_ledger", platform=msg.platform)
            return ()

    async def _standing(self, msg: NormalizedMessage) -> Standing | None:
        """Her own position in THIS conversation, or `None`

        **Keyed `(platform, conversation_id)`**, and unlike `_notes`
        beside it there is no unit to resolve and no `Policy` read
        here at all. A position is held in a room. Two rooms in one unit
        share what the unit has not settled (notes); they do not share
        where a conversation had got to, and keying
        this on the unit would have one room's position rewritten by
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
            found = await traced_read(
                "standing", "read", self._standing_store.read(
                    platform=msg.platform, conversation_id=msg.conversation_id,
                ),
            )
        except Exception:
            log.exception("standing read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="standing", platform=msg.platform)
            return None
        return found

    async def _memos(self, msg: NormalizedMessage) -> tuple[Memo, ...]:
        """The unit's kept memos for `<memos>` (DEC-306), keyed on the UNIT as
        `_notes` is and for its reason: a memo kept in the group's Signal room
        is the same group's on Discord. Fail-soft and counted
        (`stage="memos"`): a thinner prompt beats no reply, and «I could not
        read them» must not look like «nothing is kept»."""
        if self._memos_store is None:
            return ()
        convo = (
            self._policy.conversation(msg.platform, msg.conversation_id)
            if self._policy is not None else None
        )
        if convo is None or not convo.unit:
            # A store wired with no unit to scope it by is a WIRING GAP, not an
            # empty list: counted, as `_notes` counts it .
            log.warning(
                "no policy conversation for %s/%s; the memo index cannot be scoped "
                "to a unit; continuing without it", msg.platform, msg.conversation_id,
            )
            self._metrics.inc("nora_errors_total", stage="memos", platform=msg.platform)
            return ()
        try:
            return tuple(await traced_read(
                "memos", "kept", self._memos_store.kept(unit=convo.unit),
            ))
        except Exception:
            log.exception("memo-index read failed; continuing without it")
            self._metrics.inc("nora_errors_total", stage="memos", platform=msg.platform)
            return ()

    async def _notes(self, msg: NormalizedMessage) -> tuple[Note, ...]:
        """The standing note index.

        **KEYED ON THE UNIT, NOT THE ROOM — the one read in this class
        that is.** Every sibling above passes `msg.conversation_id`; this one
        passes `Conversation.unit`, deliberately. DEC-105: rooms
        sharing a unit are ONE social unit — the boundary `send_to`
        already relays across — so a note raised in the group's Discord channel
        belongs in its Signal room's index too, and `policy.toml`'s unit
        key is what keeps a different social unit's notes out. `established_in`
        (the room a note was born in) is a routing key for promotion, never a
        filter on who sees it.

        Two ways this answers `()` rather than reading, and they are different
        states wearing the same face:

        - **No store** — `[notes] enabled = false`, the dormant default. Not an
          error, not counted. It is the last read in this assembler that can
          be dormant.
        - **No `Conversation` for this room** — a store is wired but there is
          no unit to scope it by. That is a WIRING GAP (engagement and
          ingest disagreeing about which `Policy` they hold — see
          `EngagementPipeline`'s own `convo is not None` note), so it defaults
          safely AND is counted: guessing a unit would render another
          unit's notes into this room, and staying silent about it is how a
          block goes missing for the life of a deployment (see `_peer_card`'s
          `nora_peer_card_empty_total`).

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
                "scoped to a unit; continuing without it",
                msg.platform, msg.conversation_id,
            )
            self._metrics.inc(
                "nora_errors_total", stage="notes", platform=msg.platform)
            return ()
        try:
            standing = await traced_read(
                "notes", "standing", self._notes_store.standing(unit=convo.unit),
            )
        except Exception:
            log.exception("note-index read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="notes", platform=msg.platform)
            return ()
        # `config.toml`'s [notes] section names this metric as "the tell": the
        # reading that matters is whether the index grows faster than it earns.
        #
        # It is published HERE and not from a new `gather` leaf in `assemble`,
        # because this read already happens and a further gather leaf would push
        # the gather past its typed overloads.
        #
        # It is published on the SUCCESS path only. Both returns above are `()`
        # as well, and a gauge written there would export `0` for «the tier is
        # off» and for «I could not read the notebook» alike — the
        # `unavailable`-as-`nothing_found` collapse R-27 exists to prevent. The
        # wiring gap has `nora_errors_total{stage="notes"}`; absence here says
        # the reading was not taken.
        self._metrics.gauge(
            "nora_note_index_entries", len(standing), unit=convo.unit)
        return standing
