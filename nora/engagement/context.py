"""`TranscriptInjection` — assembles the `TagContext` a tag reply needs.
Replaces `Handler._collect_context`; this module preserves its two
failure-shaped behaviours:

- A transcript read failure logs, counts `nora_errors_total{stage="context"}`,
  and continues with an EMPTY transcript rather than raising — a thinner
  reply beats no reply (R-5-adjacent).
- An unmapped sender (`Persons.resolve` returns `None`) yields `tagger=None`.

Takes an `EngagementRequest`, not a bare message, because `decision` is the
only route by which a classifier's `rationale` can reach the prefill; `assemble`
accepts it even though nothing reads it yet, so that seam needs no signature
change later.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence, Set
from dataclasses import replace
from datetime import datetime
from typing import Any, NamedTuple, Protocol

from nora.access import IdentityAccess
from nora.agent.clock import local_now
from nora.agent.lineage import (
    LineagePolicy,
    derived_view,
    excluded,
    section_permits,
    visible_sections,
)
from nora.agent.room import Room
from nora.agent.standing_ledger import ledger_permits, ledger_target
from nora.domain.blocks import row_speaker
from nora.domain.computer_context import ComputerView
from nora.domain.documents_context import DocumentsView
from nora.domain.lineage import (
    BODY,
    HOLD_OPEN,
    I_OWE,
    WORKING_SET,
    RenderRoom,
)
from nora.domain.managed import room_scope_id
from nora.domain.management import Scope, ScopeKind
from nora.domain.media_join import MediaJoin
from nora.domain.memo import Memo, MemoStore
from nora.domain.memory_graph import Subgraph
from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
    TagContext,
    TranscriptRow,
)
from nora.domain.notes import Note, RetainedSource
from nora.domain.observations import (
    SourceEvent,
    SourceScope,
    TargetContext,
    coverage_label,
    handle_of,
    head_summary,
    object_handle,
    observation_summary,
    revision_label,
    target_context,
)
from nora.domain.ports import (
    EngagementRequest,
    RecordStore,
    RosterSource,
)
from nora.domain.quote_window import QuoteWindow
from nora.domain.rollup import room_key
from nora.domain.self_presence import Presence
from nora.domain.shared_state import SharedView
from nora.domain.standing import Standing
from nora.domain.standing_ledger import LedgerEntry
from nora.domain.subagents_context import SubagentsView
from nora.domain.turn_record import DisclosureRequirement
from nora.engagement.computer_context import ComputerContextReader
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.self_presence import PresenceReader
from nora.engagement.subagents_context import SubagentsContextReader
from nora.language.lesson_render import ActiveLessons, read_lessons
from nora.obs.tracing import traced_read
from nora.persons import Persons
from nora.policy import Policy

log = logging.getLogger(__name__)

#: The folded object heads the Standing presentation shows at most — a
#: bound on the most recently observed objects, not "every open poll
#: forever" and not an arbitrary first-N of the scope.
_SOURCE_OBJECT_MAX = 12

#: Distinct affected messages one assembly resolves — one batched Record
#: read, so the bound is on distinct targets, not on event count. A target
#: past the cap shows `target_unresolved`, not a scan.
_OBSERVATION_TARGET_MAX = 8


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
    `SELECT` — which is why `_notes` guards rather than trusts.
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
        persons: Persons,
        context_window: int,
        prompt_window: int | None = None,
        metrics: Any,
        quote_window: QuoteWindow | None = None,
        ai_peer: str = "",
        display_name: str = "",
        notes: StandingNotesReader | None = None,
        policy: Policy | None = None,

        media_join: MediaJoin | None = None,
        presence: PresenceReader | None = None,
        lessons: ActiveLessons | None = None,
        lineage: LineagePolicy | None = None,
        computer: ComputerContextReader | None = None,
        documents: DocumentsContextReader | None = None,
        subagents: SubagentsContextReader | None = None,
        memos: MemoStore | None = None,
        shared: Callable[..., Awaitable[SharedView]] | None = None,
        clock: Callable[[], datetime] | None = None,
        rosters: Mapping[str, RosterSource] | None = None,
        self_ids: Set[str] = frozenset(),
        observations: Any = None, access: Any = None,
    ) -> None:
        self._rosters = rosters or {}
        self._self_ids = self_ids
        # The typed platform-observation Record (`ObservationStore`): the
        # room's source events for the transcript window and its folded
        # object heads for the standing slot. `None` on a harness that
        # wires none renders neither — never a guessed source line.
        self._observations = observations
        self._audit = audit
        # The graph's one clock (`build_dependencies`' `clock`), so a harness
        # at a fixed instant ranks and dates the pre-fetch at that instant.
        # `None` is the wall clock.
        self._clock = clock or local_now
        # `<memos>` (DEC-306): the unit's kept memos, unit-wide like `notes`.
        # `None` renders no block.
        self._memos_store = memos
        # The unit's shared cognitive state reader (docs/design/cognition/graph.md
        # 20-standing.md): one callable bound at the composition
        # root over the canonical stores, `None` in every harness that wires
        # none — which renders no `<standing>` block.
        self._shared_reader = shared
        self._access = access or IdentityAccess.for_files(persons, policy)
        self._context_window = context_window
        # `None` renders everything that was read (every harness that predates
        # `[store] prompt_window`); a number renders at most that many rows,
        # never reaching back less far than the quoted parent.
        self._prompt_window = prompt_window
        self._quote_window = quote_window
        self._metrics = metrics
        self._ai_peer = ai_peer
        self._display_name = display_name
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

    @staticmethod
    def _room_scope(msg: NormalizedMessage) -> Scope:
        """The message's trusted room scope."""
        return Scope(ScopeKind.ROOM, room_scope_id(msg.platform, msg.conversation_id))

    async def assemble(self, *, request: EngagementRequest) -> TagContext:
        msg = request.message
        # Resolved BEFORE the gather because reads in it are keyed on it.
        ref = await self._access.resolve_ref(
            msg.platform, msg.sender_id, scope=self._room_scope(msg))

        transcript, presence, computer, documents, subagents, observations = await asyncio.gather(
            self._recent(msg), self._presence(msg), self._computer(msg),
            self._documents(msg, ref), self._subagents(msg, ref), self._observation_reads(msg),
        )
        transcript = await self._access.name_transcript(
            transcript, platform=msg.platform, scope=self._room_scope(msg))
        if self._media_join is not None:
            transcript = await traced_read(
                "media_join", "enrich", self._media_join.enrich(transcript, path="tag"),
            )

        shown = self._shown(transcript, msg)
        events, objects, failed, more, objects_more = observations
        return TagContext(
            tag_id=request.tag_id, message=msg, tagger=ref, transcript=shown,
            lookup_transcript=tuple(transcript) if len(shown) < len(transcript) else (),
            peer_card=(), started_at=time.perf_counter(), presence=presence,
            computer=computer, documents=documents, subagents=subagents,
            source_events=events, source_objects=objects, source_reads_failed=failed,
            source_events_more=more, source_objects_more=objects_more,
            room_members=await self._room_members(msg),
        )

    async def _room_members(self, msg: NormalizedMessage) -> tuple[PersonRef, ...]:
        """This room's listed persons, for `<room_members>`; empty for a room
        outside the policy or a `persons = "*"` room (`Persons.room_members`)."""
        if self._policy is None and self._access.is_file_backend:
            return ()
        return await self._access.room_refs(msg.platform, msg.conversation_id)

    def _obs_actor_name(
        self, platform: str, actor: str | None, *, known: bool = True
    ) -> str | None:
        """The name a sync summary renders — file mode only, `None` under managed."""
        if not (self._access.is_file_backend and actor and known):
            return None
        return getattr(
            self._access.persons_view().resolve(platform, actor), "name", None)

    async def _render_room(self, msg: NormalizedMessage) -> Any:
        """The lineage policy's room view — configured lineage or policy-derived."""
        lineage = self._lineage
        if lineage is None:
            if self._policy is None:
                raise ValueError("a room view requires lineage or policy")
            lineage = LineagePolicy.from_policy(self._policy, access=self._access)
        return await lineage.read_room(
            platform=msg.platform, conversation_id=msg.conversation_id)

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
            conversation_type=msg.conversation_type,
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
        the reason every read in the `gather` is — an unreachable exception
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

    async def _shared(
        self, msg: NormalizedMessage, ref: PersonRef | None,
    ) -> SharedView | None:
        """The unit's shared cognitive state, projected for THIS destination.

        Unit-scoped like `_notes`, with the ledger read's own destination
        predicate applied to each entry so a room sees every entry it may and
        `permitted=False` on the rest — never an absence that reads as
        nothing there. A wired read failure returns an unavailable view;
        `None` is reserved for a reader that was never wired.
        """
        if self._shared_reader is None:
            return None
        convo = (
            self._policy.conversation(msg.platform, msg.conversation_id)
            if self._policy is not None
            else None
        )
        if convo is None:
            log.warning(
                "no policy conversation for %s/%s — the shared state cannot "
                "be scoped to a unit; continuing without it",
                msg.platform, msg.conversation_id,
            )
            self._metrics.inc(
                "nora_errors_total", stage="shared_state", platform=msg.platform)
            return SharedView(
                unit="", status="unavailable",
                as_of_ms=int(self._clock().timestamp() * 1000),
            )
        permit = None
        permit_req = None
        standing_permits = None
        if self._lineage is not None:
            room = Room(
                platform=msg.platform, conversation_id=msg.conversation_id,
                roster_source=self._rosters.get(msg.platform),
                tagger=ref, self_ids=self._self_ids, access=self._access,
            )
            target = await self._render_room(msg)
            try:
                narrowed = await ledger_target(target, room)
            except Exception:
                # A permit that cannot be built withholds EVERYTHING: an
                # unfiltered unit read into one room is the disclosure
                # failure, so the read itself is reported unavailable —
                # the failure the renderer can show.
                log.exception(
                    "shared-state permit build failed for %s:%s",
                    msg.platform, msg.conversation_id,
                )
                return SharedView(
                    unit=convo.unit, status="unavailable",
                    as_of_ms=int(self._clock().timestamp() * 1000),
                )
            if narrowed is not None:
                def permit(entry: LedgerEntry) -> bool:
                    return ledger_permits(entry, narrowed)

                def permit_req(requirements: tuple[DisclosureRequirement, ...]) -> bool:
                    # Turn-record items carry their own per-item disclosure;
                    # the same lineage predicates decide it for this room.
                    # An item carrying NO requirements has no gate to
                    # check — withhold, never vacuously permit.
                    return bool(requirements) and all(
                        req.known
                        and not excluded(req.exclude_persons, narrowed)
                        and section_permits(
                            req.entries, narrowed, origin=req.origin)
                        for req in requirements
                    )

                def standing_permits(position: Standing) -> dict[str, bool]:
                    # The derived-view gate, run against the destination's
                    # RenderRoom: an excluded or unpermitted section is
                    # withheld exactly as the retired standing blocks did.
                    return visible_sections(
                        position.lineage,
                        (BODY, HOLD_OPEN, I_OWE, WORKING_SET), narrowed,
                        origin=room_key(position.platform, position.conversation_id),
                        exclude_persons=position.exclude_persons,
                    )
        try:
            return await asyncio.wait_for(self._shared_reader(
                unit=convo.unit,
                room=(msg.platform, msg.conversation_id),
                permit=permit,
                permit_req=permit_req,
                standing_permits=standing_permits,
                now_ms=int(self._clock().timestamp() * 1000),
            ), timeout=5.0)
        except Exception:
            # A wired reader that fails is NOT an unwired one: `None` would
            # re-enable the legacy fold in `_debrief` — the independent
            # thinker the wired route retired. `unavailable` keeps the
            # record-only route and says why.
            log.exception("shared-state read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="shared_state", platform=msg.platform)
            return SharedView(
                unit=convo.unit, status="unavailable",
                as_of_ms=int(self._clock().timestamp() * 1000),
            )

    async def _observation_reads(
        self, msg: NormalizedMessage,
    ) -> tuple[
        tuple[SourceEvent, ...], tuple[SourceEvent, ...], bool, int, int,
    ]:
        """`(events, objects, failed, events_more, objects_more)` of THIS
        room — the scoped source read of docs/design/platform/observations.md

        `recent` is bounded by the prompt window; `heads` by
        `_SOURCE_OBJECT_MAX` of the objects OBSERVED NEWEST first (an old
        object's state is not pinned forever; a poll the room just voted
        on outranks a stale one). Both read through the room's boundary
        (`account_key=""` — the turn already passed the allowlist for
        this room, and a row of another room never resolves). Fail-soft
        and counted (`stage="observations"`), like `_standing`: a thinner
        prompt beats no reply — but a failed read SAYS it failed
        (`failed`), never an empty list dressed as "nothing recorded";
        an unwired store is `unavailable`, not nothing."""
        if self._observations is None:
            return (), (), False, 0, 0
        scope = SourceScope(platform=msg.platform, account_key="",
                            conversation_id=msg.conversation_id)
        limit = self._prompt_window or self._context_window
        try:
            # Both reads take ONE row past what they show — the sentinel
            # that makes `*_more` an honest count instead of a silently
            # complete-looking page. `heads` answers recency-ordered
            # (`updated_at` — the object most recently touched first), so
            # the page IS the freshest and `objects_more` is a real count
            # of unshown heads, up to the page bound.
            rows, heads = await asyncio.gather(
                traced_read(
                    "observations", "recent",
                    self._observations.recent(scope=scope, limit=limit + 1)),
                traced_read(
                    "observations", "heads",
                    self._observations.heads(scope=scope, limit=64)),
            )
        except Exception:
            log.exception("observation reads failed; continuing without them")
            self._metrics.inc(
                "nora_errors_total", stage="observations", platform=msg.platform)
            return (), (), True, 0, 0

        # `observation_summary`/`head_summary` are sync renderers — file-mode
        # names only, never a stale managed answer (`_obs_actor_name`).

        # A vote's option ref resolves to the definition's label through
        # the object's OWN head (matched on ref + its account/thread —
        # two accounts' same-named polls never lend each other labels).
        poll_heads = {(h.object_ref, h.account_key, h.thread): h
                      for h in heads if h.kind == "poll"}

        def option_label(r: Any, ref: str) -> str | None:
            h = poll_heads.get((
                r.observation.object_ref, r.observation.scope.account_key,
                r.observation.scope.thread))
            entry = (h.state.get("options") or {}).get(ref) if h else None
            return (entry or {}).get("label")

        events = tuple(
            SourceEvent(
                ref=f"platform_observation:{r.id}",
                kind=r.observation.object_kind, operation=r.observation.operation,
                at_ms=(r.observation.occurred_ms
                       if r.observation.occurred_ms is not None
                       else r.observation.received_ms),
                time_quality=r.observation.time_quality,
                actor=self._obs_actor_name(
                    r.observation.scope.platform, r.observation.actor_id,
                    known=r.observation.actor_status == "known"),
                actor_status=r.observation.actor_status,
                handle=object_handle(
                    r.observation.object_kind, r.observation.object_ref,
                    account_key=r.observation.scope.account_key,
                    thread=r.observation.scope.thread),
                summary=observation_summary(
                    r.observation,
                    name_of=lambda ref, o=r.observation: self._obs_actor_name(
                        o.scope.platform, ref),
                    option_label=lambda ref, r=r: option_label(r, ref)),
                target_message_id=r.target_message_id,
                delivered_message_id=r.delivered_message_id,
                received_ms=r.observation.received_ms,
                revision=revision_label(
                    r.observation.source_position,
                    r.observation.revision_scope),
                coverage=coverage_label(r.observation.coverage),
            )
            for r in rows[:limit]
        )
        # Current state, most-recently-observed first — relevance is
        # recency, not all-open-polls-forever: an object the room has not
        # touched in a while yields its slot to one it just did, and the
        # bound says how many older heads were not shown. A head's own
        # clock stays honest: `observed_ms` is the newest SOURCE time the
        # fold applied, `observed_quality` says which clock that is, and
        # `received_ms` is the newest receipt — never presented as the
        # source's own time. Recency ranks on whichever clock exists.
        # Preserve the store's last-write order; source clocks can be late
        # or wrong and must not evict a freshly changed object.
        heads = [h for h in heads if h.applied > 0]
        shown = heads[: _SOURCE_OBJECT_MAX]
        # The affected messages the shown events and heads name, resolved
        # in two bounded reads through the room's own boundary — the
        # event's `target` says what it acted on when that message is not
        # in the transcript window (its words and author as labelled
        # context, never the reactor's).
        targets, head_targets = await self._observation_targets(
            msg, scope, rows[:limit], [h.head_id for h in shown])
        events = tuple(
            replace(e, target=targets.get(e.target_message_id)
                    if e.target_message_id is not None else None)
            for e in events)
        # Each shown head carries its newest applied row's affected
        # message — the SAME resolved context the events carry.
        head_ctx = {
            hid: targets.get(mid) for hid, mid in head_targets.items()}
        objects = tuple(
            SourceEvent(
                # `head_id` IS the newest applied row's id — naming the
                # head by `platform_observation:<id>` makes it the SAME
                # ref an event row carries, so an object whose newest
                # change is already shown as an `<source_event>` is
                # recognisably the same thing, not a second fact.
                ref=f"platform_observation:{h.head_id}",
                kind=h.kind, operation="state",
                at_ms=h.observed_ms or 0,
                time_quality=h.observed_quality,
                actor=None, actor_status="not_applicable",
                handle=handle_of(h),
                summary="", ordering=h.ordering,
                state=head_summary(
                    h, name_of=lambda ref: self._obs_actor_name(msg.platform, ref)),
                received_ms=h.received_ms,
                revision=revision_label(h.position, h.scope),
                coverage=coverage_label(h.coverage),
                target_message_id=head_targets.get(h.head_id),
                target=head_ctx.get(h.head_id),
            )
            for h in shown
        )
        return (
            events, objects, False,
            max(0, len(rows) - len(events)),
            max(0, len(heads) - len(objects)),
        )

    async def _retained_sources(
        self, msg: NormalizedMessage, notes: tuple[Note, ...],
        shown_handles: frozenset[str],
    ) -> tuple[tuple[RetainedSource, ...], int]:
        """This room's retained lane: the `object_refs` open notes of the
        unit curated, resolved through `head_for_handle` under each ref's
        OWN scope — never the recency page.

        A ref naming another room contributes nothing here (the digest binds
        account and thread, and the room check is the boundary); one the
        page already shows contributes nothing twice. A ref the store no
        longer resolves comes back `head=None` — the explicit unresolved
        marker the render names, never a dropped row. Fail-soft and
        counted like the sibling read: a failed resolve is one marker,
        not a failed turn."""
        if self._observations is None:
            return (), 0
        if self._lineage is not None and self._lineage.mode == "on":
            notes = derived_view(
                notes=notes, standing=None,
                target=await self._render_room(msg)).notes
        out: list[RetainedSource] = []
        more = 0
        seen: set[str] = set(shown_handles)

        def name_of(person_ref: str) -> str | None:
            return self._obs_actor_name(msg.platform, person_ref)

        for note in notes:
            for ref in note.object_refs:
                if (ref.platform, ref.conversation_id) != (
                        msg.platform, msg.conversation_id):
                    continue
                if ref.handle in seen:
                    continue
                seen.add(ref.handle)
                if len(out) >= _SOURCE_OBJECT_MAX:
                    more += 1
                    continue
                try:
                    head = await traced_read(
                        "observations", "retained",
                        self._observations.head_for_handle(
                            scope=SourceScope(
                                platform=ref.platform, account_key="",
                                conversation_id=ref.conversation_id,
                                thread=ref.thread),
                            handle=ref.handle))
                except Exception:
                    log.exception("retained source read failed for %s",
                                  ref.handle[:16])
                    self._metrics.inc(
                        "nora_errors_total", stage="observations",
                        platform=msg.platform)
                    head = None
                out.append(RetainedSource(
                    ref=ref, matter=note.title,
                    head=None if head is None else SourceEvent(
                        ref=f"platform_observation:{head.head_id}",
                        kind=head.kind, operation="state",
                        at_ms=head.observed_ms or 0,
                        time_quality=head.observed_quality,
                        actor=None, actor_status="not_applicable",
                        handle=handle_of(head),
                        summary="", ordering=head.ordering,
                        state=head_summary(head, name_of=name_of),
                        received_ms=head.received_ms,
                        revision=revision_label(head.position, head.scope),
                        coverage=coverage_label(head.coverage))))
        return tuple(out), more

    async def _observation_targets(
        self, msg: NormalizedMessage, scope: SourceScope,
        rows: Sequence[Any], head_ids: Sequence[int],
    ) -> tuple[dict[int, TargetContext], dict[int, int]]:
        """The shown events'/heads' affected messages — `(target context by
        message id, target id by newest applied row id)`.

        TWO bounded reads, both inside the room's own boundary:
        `observations.by_ids` maps each shown head's newest applied row to
        its `target_message_id`, then `RecordStore.by_ids` resolves those
        ids — same-room only, hidden rows absent, recorded deletes named.
        A failed read resolves NOTHING (fail-soft and counted, like the
        observation reads themselves): `target` stays absent rather than
        guessed, and `target_unresolved` on the render says why.
        """
        try:
            head_rows = (
                await traced_read(
                    "observations", "targets",
                    self._observations.by_ids(
                        scope=scope, ids=tuple(head_ids)))
                if head_ids else ())
            wanted = {r.target_message_id for r in rows}
            wanted.update(r.target_message_id for r in head_rows)
            wanted.discard(None)
            if not wanted:
                return {}, {}
            found, gone = await traced_read(
                "record", "targets",
                self._audit.by_ids(
                    platform=msg.platform, conversation_id=msg.conversation_id,
                    ids=tuple(sorted(wanted))[:_OBSERVATION_TARGET_MAX]))
            named = await self._access.name_transcript(
                list(found.values()), platform=msg.platform,
                scope=self._room_scope(msg))
            targets = {
                mid: target_context(
                    row, deleted=mid in gone,
                    author=(self._display_name or row.sender_name or "you")
                    if row.is_nora else row_speaker(row))
                for mid, row in zip(found, named, strict=True)
            }
            return (
                targets,
                {r.id: r.target_message_id for r in head_rows
                 if r.target_message_id is not None},
            )
        except Exception:
            log.exception(
                "observation target read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="observations",
                platform=msg.platform)
            return {}, {}

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
          block goes missing for the life of a deployment.

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
