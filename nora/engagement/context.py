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
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import replace
from datetime import datetime
from typing import Any, NamedTuple

from nora.access import IdentityAccess
from nora.agent.clock import local_now
from nora.agent.lineage import (
    LineagePolicy,
)
from nora.domain.blocks import row_speaker
from nora.domain.computer_context import ComputerView
from nora.domain.documents_context import DocumentsView
from nora.domain.lineage import (
    RenderRoom,
)
from nora.domain.managed import room_scope_id
from nora.domain.management import Scope, ScopeKind
from nora.domain.media_join import MediaJoin
from nora.domain.memory_graph import Subgraph
from nora.domain.models import (
    NormalizedMessage,
    PersonRef,
    TagContext,
    TranscriptRow,
)
from nora.domain.observations import (
    SourceEvent,
    SourceScope,
    TargetContext,
    coverage_label,
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
from nora.domain.self_presence import Presence
from nora.domain.subagents_context import SubagentsView
from nora.engagement.computer_context import ComputerContextReader
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.self_presence import PresenceReader
from nora.engagement.subagents_context import SubagentsContextReader
from nora.obs.tracing import traced_read
from nora.persons import Persons
from nora.policy import Policy

log = logging.getLogger(__name__)

#: The bounded head page used when resolving transcript event targets.
_SOURCE_OBJECT_MAX = 12

#: Distinct affected messages one assembly resolves — one batched Record
#: read, so the bound is on distinct targets, not on event count. A target
#: past the cap shows `target_unresolved`, not a scan.
_OBSERVATION_TARGET_MAX = 8


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
        policy: Policy | None = None,

        media_join: MediaJoin | None = None,
        presence: PresenceReader | None = None,
        lineage: LineagePolicy | None = None,
        computer: ComputerContextReader | None = None,
        documents: DocumentsContextReader | None = None,
        subagents: SubagentsContextReader | None = None,
        clock: Callable[[], datetime] | None = None,
        rosters: Mapping[str, RosterSource] | None = None,
        self_ids: Set[str] = frozenset(),
        observations: Any = None, access: Any = None,
    ) -> None:
        self._rosters = rosters or {}
        self._self_ids = self_ids
        # The typed platform-observation Record (`ObservationStore`): the
        # room's source events for the transcript window and heads for
        # resolving their poll labels and affected messages.
        self._observations = observations
        self._audit = audit
        # The graph's one clock (`build_dependencies`' `clock`), so a harness
        # at a fixed instant ranks and dates the pre-fetch at that instant.
        # `None` is the wall clock.
        self._clock = clock or local_now
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
        self._policy = policy
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
        events, failed, more = observations
        return TagContext(
            tag_id=request.tag_id, message=msg, tagger=ref, transcript=shown,
            lookup_transcript=tuple(transcript) if len(shown) < len(transcript) else (),
            started_at=time.perf_counter(), presence=presence,
            computer=computer, documents=documents, subagents=subagents,
            source_events=events, source_reads_failed=failed, source_events_more=more,
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


    async def _observation_reads(
        self, msg: NormalizedMessage,
    ) -> tuple[tuple[SourceEvent, ...], bool, int]:
        """Recent source events, read failure, and the overflow count for this room."""
        if self._observations is None:
            return (), False, 0
        scope = SourceScope(platform=msg.platform, account_key="",
                            conversation_id=msg.conversation_id)
        limit = self._prompt_window or self._context_window
        try:
            # Read one extra event to detect overflow. Heads resolve poll labels.
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
            return (), True, 0

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
        # Preserve the recency bound for resolving event targets.
        heads = [h for h in heads if h.applied > 0]
        shown = heads[: _SOURCE_OBJECT_MAX]
        # The affected messages the shown events and heads name, resolved
        # in two bounded reads through the room's own boundary — the
        # event's `target` says what it acted on when that message is not
        # in the transcript window (its words and author as labelled
        # context, never the reactor's).
        targets = await self._observation_targets(
            msg, scope, rows[:limit], [h.head_id for h in shown])
        events = tuple(
            replace(e, target=targets.get(e.target_message_id)
                    if e.target_message_id is not None else None)
            for e in events)
        return events, False, max(0, len(rows) - len(events))

    async def _observation_targets(
        self, msg: NormalizedMessage, scope: SourceScope,
        rows: Sequence[Any], head_ids: Sequence[int],
    ) -> dict[int, TargetContext]:
        """The shown events' and heads' affected messages, keyed by message ID.

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
                return {}
            found, gone = await traced_read(
                "record", "targets",
                self._audit.by_ids(
                    platform=msg.platform, conversation_id=msg.conversation_id,
                    ids=tuple(sorted(wanted))[:_OBSERVATION_TARGET_MAX]))
            named = await self._access.name_transcript(
                list(found.values()), platform=msg.platform,
                scope=self._room_scope(msg))
            return {
                mid: target_context(
                    row, deleted=mid in gone,
                    author=(self._display_name or row.sender_name or "you")
                    if row.is_nora else row_speaker(row))
                for mid, row in zip(found, named, strict=True)
            }
        except Exception:
            log.exception(
                "observation target read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="observations",
                platform=msg.platform)
            return {}
