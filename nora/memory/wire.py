import functools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.access import IdentityAccess
from nora.agent.dream_screen import text_offence
from nora.agent.egress import LeakGuard
from nora.agent.lineage import LineagePolicy
from nora.agent.memory_search import MemorySearch
from nora.agent.room import Room
from nora.app.cognition_grounding import place_release
from nora.app.composition_helpers import _unified_profile_writers_retired
from nora.app.memory_routing import MemoryRoutingError, build_workspace_router
from nora.app.startup import StartupError
from nora.domain.live import LiveMapping
from nora.domain.memory_index import QueryEmbedder
from nora.domain.ports import SearchProvider
from nora.infra.embedder import QUERY_TIMEOUT_SECONDS, LlamaEmbedder, fingerprint, model_file
from nora.infra.honcho import HonchoClient
from nora.infra.sql import SqlEntryStore
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.memory_graph import SqlMemoryGraph
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.rollups import SqlRollupStore
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.sql.standing_ledger import SqlStandingLedger
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.admission_rows import SqlAdmissionRows
from nora.memory.adapters.admission_store import SqlAdmissionStore
from nora.memory.adapters.claim_support import SqlClaimSupport
from nora.memory.adapters.condition_matcher import SqlConditionMatcher
from nora.memory.adapters.debrief_store import SqlDebriefStore
from nora.memory.adapters.delivery_arbitration import SqlDeliveryHistory
from nora.memory.adapters.graph_clock import SqlDueSchedules
from nora.memory.adapters.graph_store import SqlCognitiveGraph
from nora.memory.adapters.memory_intake import SqlMemoryIntake
from nora.memory.adapters.message_graph_ids import SqlMessageGraphIds
from nora.memory.adapters.pass_anchors import SqlPassAnchors
from nora.memory.adapters.pass_assembly import PassAssembler
from nora.memory.adapters.proactive_claims import SqlProactiveClaims
from nora.memory.adapters.replay_receipts import SqlReplayReceipts
from nora.memory.adapters.retrieval_store import SqlRetrieval
from nora.memory.adapters.review_anchors import SqlReviewTargets as SqlReviewTargets
from nora.memory.adapters.tools import build as _build
from nora.memory.adapters.tools.notes import _make__resolve
from nora.memory.adapters.tools.search import GraphReadTools
from nora.memory.adapters.tools.session import session_verbs as _session_verbs
from nora.memory.adapters.tools.wake import WAKE_HISTORY_TOOLS as WAKE_HISTORY_TOOLS
from nora.memory.adapters.tools.wake import WAKE_TOOLS as WAKE_TOOLS
from nora.memory.adapters.tools.wake import WakeToolSurface as WakeToolSurface
from nora.memory.adapters.verify import SqlIntentionVerification
from nora.memory.adapters.wake_claims import SqlWakeClaims
from nora.memory.adapters.watch_page import WATCH_ADAPTERS
from nora.memory.adapters.watch_results import SqlWatchResults
from nora.memory.app.acts import ActEnvelope
from nora.memory.app.admission import AdmissionService
from nora.memory.app.admission_identity import AdmissionIdentity
from nora.memory.app.delivery_arbitration import DeliveryArbitration
from nora.memory.app.graph_clock import GraphClock
from nora.memory.app.lesson_review import LessonReview
from nora.memory.app.retrieval import RetrievalService
from nora.memory.app.verify import IntentionVerifier
from nora.memory.app.watches import WatchPoller
from nora.memory.domain.admission_ports import LessonChecker, RoomSlots
from nora.memory.domain.graph_operations import REVIEW_PARTS as REVIEW_PARTS
from nora.memory.domain.graph_operations import WAKE_OPERATIONS as WAKE_OPERATIONS
from nora.persons import Persons
from nora.policy import Policy

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


def session_verbs(deps):
    return _session_verbs(deps)


def delivery_arbitration(engine: AsyncEngine, policy: Policy) -> DeliveryArbitration:
    return DeliveryArbitration(SqlDeliveryHistory(engine), policy)


def make_resolve(deps):
    return _make__resolve(deps)


def act_envelope(
    engine: AsyncEngine, registration: SqlItemRegistration | None = None,
) -> ActEnvelope:
    item_registration = registration or engine.get_execution_options()["item_registration"]
    return ActEnvelope(SqlActStore(engine, item_registration))


def bridge_act_envelope(engine: AsyncEngine) -> ActEnvelope:
    policy, persons = Policy(()), Persons({}, {})
    return act_envelope(engine, SqlItemRegistration(
        RegistrationScope(policy, persons), RuntimeRevisions({}, policy, persons),
    ))


@dataclass(frozen=True)
class StoresWiring:
    standing_store: Any


def stores(engine) -> StoresWiring:
    # Standing. The STORE is built unconditionally, REUSING `engine` for the same reason its
    # four neighbours above do — the read half feeds `TranscriptInjection`, whose own
    # `_standing` returns `None` for a `None` store, so an always-built store costs one…
    standing_store = SqlStandingStore(engine)
    return StoresWiring(standing_store=standing_store)


@dataclass(frozen=True)
class LedgerWiring:
    ledger_store: Any


def ledger(engine, metrics) -> LedgerWiring:
    ledger_store = SqlStandingLedger(engine, metrics=metrics)
    return LedgerWiring(ledger_store=ledger_store)


@dataclass(frozen=True)
class NotesWiring:
    memo_store: Any
    note_store: Any
    entry_store: Any


def notes(cfg, engine) -> NotesWiring:
    # The note tier's store, REUSING `engine` like every store above, and gated on its OWN
    # flag. Off, `TranscriptInjection` gets `notes=None` and `_notes` answers `()` without a
    # query. No `metrics=`: `SqlNoteStore.__init__` takes an engine and nothing else on…
    note_store: SqlNoteStore | None = SqlNoteStore(engine) if cfg.notes.enabled else None
    # The memo tier (DEC-306): what a unit settled and keeps. No flag: only a Cognition
    # session writes a memo (`[cognition] enabled`), and with none kept every read renders
    # nothing. ONE instance for the tag and fire `<memos>`, `memo_read` and the session's…
    memo_store = SqlMemoStore(engine)
    # The shared Notebook entry store — the topic account is its only kind so far.
    # No flag, same reason as the memo store: only a session writes an entry.
    entry_store = SqlEntryStore(engine)
    return NotesWiring(
        memo_store=memo_store, note_store=note_store, entry_store=entry_store)


@dataclass(frozen=True)
class HonchoWiring:
    memory: Any


async def honcho(allowlist, cfg, persons, owner=None) -> HonchoWiring:
    # workspace > unit > room (DEC-214): every Honcho call is routed from its room or unit
    # to the Honcho workspace its Nora workspace is bound to, and to the unit's named scope
    # inside it. Resolved ONCE, here, from the three files that each hold one part of it…
    try:
        router = build_workspace_router(
            policy=allowlist,
            bindings=persons.honcho_workspaces,
            default_workspace=cfg.honcho.workspace_id,
        )
    except MemoryRoutingError as exc:
        raise StartupError(f"memory routing error: {exc}") from exc
    memory = HonchoClient(
        cfg.honcho.base_url,
        cfg.honcho.workspace_id,
        ai_peer=cfg.honcho.ai_peer,
        workspace_router=router,
    )
    if owner is not None:
        # Owned before `ensure_workspace` and the foreign-workspaces refusal
        # below can strand it — the client exists from here on.
        owner.own_memory(memory)
    # Best-effort, never fatal — no try/except. Every bound Honcho workspace,
    # the Nora aiPeer in each (her replies feed under it to build her
    # self-model; peers are per Honcho workspace), and every unit's scope.
    await memory.ensure_workspace()
    # EXCEPT a bound Honcho workspace that belongs to another application: that is a wrong
    # line in identity.toml, and running on it would write people's speech into someone
    # else's peers. The client has already refused every call to it; this makes the refusal…
    foreign = memory.foreign_workspaces()
    if foreign:
        raise StartupError(
            f"memory routing error: Honcho workspace(s) {list(foreign)} hold "
            "sessions and are not marked as this Nora's. Another application "
            "uses them — bind the Nora workspace to a different "
            "honcho_workspace in identity.toml."
        )
    return HonchoWiring(memory=memory)


@dataclass(frozen=True)
class SearchWiring:
    memory_index_reader: Any
    memory_search: Any


def search(cfg, engine, metrics, access=None) -> SearchWiring:

    # `[memory.index] read` (ships off): the one retrieval service, held by the tag path's
    # pre-fetch (`<recalled>`) and by the tool registry's `memory_search` below, one
    # instance for both; `None` keeps the Honcho surface and no pre-fetch. The same…
    memory_index_reader = SqlMemoryIndex(engine)
    memory_search = (
        MemorySearch(
            memory_index_reader,
            metrics,
            graph=SqlMemoryGraph(engine, metrics=metrics),
            profile_render=cfg.memory.profile.write and cfg.memory.profile.read,
            # `[memory.index] embed`: dense and exact seeds, the
            # lexical seed only as the fallback; off, every answer is
            # the fallback's `partial`.
            embedder=LlamaEmbedder(
                cfg.memory.index.embedder_url,
                timeout_seconds=QUERY_TIMEOUT_SECONDS,
                query_model_file=model_file(cfg.memory.index.embedder_model),
            )
            if cfg.memory.index.embed
            else None,
            model=(
                fingerprint(cfg.memory.index.embedder_model) if cfg.memory.index.embed else None
            ),
            access=access,
            # The embedding worker's tick: a node waiting less than a
            # few of them is fresh, not a backlog that answers `partial`.
            drain_seconds=cfg.memory.index.refresh_seconds,
        )
        if cfg.memory.index.read
        else None
    )
    return SearchWiring(memory_index_reader=memory_index_reader, memory_search=memory_search)


@dataclass(frozen=True)
class GraphWiring:
    cognition_memory: Any


def _private_names(persons: Any, access: Any) -> tuple[str, ...]:
    """File names/handles; managed source names resolve before each screened read."""
    if access is not None and not access.is_file_backend:
        return ()
    return tuple(
        {
            name
            for p in persons.by_person.values()
            for name in (p.person_id, p.name, p.first_name, p.last_name, *p.aliases)
            if name
        }
        | {value for (_, value) in persons.index}
        | set(persons._instagram_handles.values())
        | set(persons._instagram_usernames)
    )


def graph(
    allowlist,
    cfg,
    content_checks,
    engine,
    media_join,
    metrics,
    persons,
    presence_circles,
    profile_index,
    registry,
    rosters,
    route_model,
    tier,
    unit_scopes,
    access=None,
) -> GraphWiring:
    cognition_memory = None
    if cfg.memory.graph.enabled and cfg.memory.graph.comprehend.enabled:
        from nora.infra.sql.memory_graph import SqlMemoryGraph

        memory_graph = SqlMemoryGraph(engine, metrics=metrics)
        from nora.app.cognition_memory import WakeMemory
        from nora.infra.sql.comprehend import SqlComprehendRecords

        rooms_by_unit = LiveMapping(
            lambda: {
                u: [
                    (c.platform, c.primary_id)
                    for c in allowlist.rooms()
                    if c.unit == u and c.primary_id
                ]
                for u in unit_scopes()
            }
        )
        graph_records = SqlComprehendRecords(
            engine,
            media_join=media_join,
            model_id=route_model("tag", tier.model),
            rooms_by_unit=rooms_by_unit,
            persons_by_unit=LiveMapping(
                lambda: {
                    u: {p for c in allowlist.rooms() if c.unit == u for p in (c.persons or ())}
                    for u in unit_scopes()
                }
            ),
            access=access,
            social_off_ids=persons.social_off_ids() if access is None else frozenset(),
            can_release_place=place_release(
                rooms_of=lambda u: [
                    (c.platform, c.primary_id, c.location)
                    for c in allowlist.rooms()
                    if c.unit == u and c.primary_id
                ],
                room_view=lambda platform, conversation_id: Room(
                    platform=platform,
                    conversation_id=conversation_id,
                    roster_source=rosters.get(platform),
                    persons=persons,
                    access=access,
                    self_ids=registry.self_aliases(),
                ).members(),
                circles=presence_circles,
                content_checks=content_checks,
            ),
        )
        # THE PROMOTED PROFILE WRITER (27 §9, 31 §5): the same `[memory.profile] write && read`
        # switch (`profile_index`) as the read surface and index projection; `None` under every
        # other flag combination, where `profile_revise` keeps recording shadow receipts and…
        wake_profile_store = (
            SqlProfileStore(
                engine,
                screen=functools.partial(text_offence, content_checks=content_checks),
                index=profile_index,
            )
            if _unified_profile_writers_retired(
                profile_index=profile_index,
                graph_comprehend=True,
                cognition_enabled=cfg.cognition.enabled,
                units=frozenset(unit_scopes()),
                home_units=frozenset(allowlist.home_rooms()),
            )
            else None
        )
        wake_listed_rooms = LiveMapping(
            lambda: {
                (u, p): tuple(
                    c.primary_id
                    for c in allowlist.rooms()
                    if c.unit == u and c.primary_id and c.persons and p in c.persons
                )
                for u in unit_scopes()
                for p in {x for c in allowlist.rooms() if c.unit == u for x in (c.persons or ())}
            }
        )
        cognition_memory = WakeMemory(
            graph=memory_graph,
            records=graph_records,
            metrics=metrics,
            content_checks=content_checks,
            profile=wake_profile_store,
            listed_rooms=(lambda u, p: wake_listed_rooms.get((u, p))),
            private_names=_private_names(persons, access),
        )
    return GraphWiring(cognition_memory=cognition_memory)



def admission(
    engine: AsyncEngine, persons: Persons | None, policy: Policy, leak_guard: LeakGuard, *,
    content_checks: bool, room_slots: RoomSlots,
    nora_accounts: Iterable[tuple[str, str]] = (),
    access: IdentityAccess | None = None,
    lesson_checker: LessonChecker | None = None,
) -> AdmissionService:
    identity = AdmissionIdentity(persons, policy, access=access, nora_accounts=nora_accounts)
    store = SqlAdmissionStore(SqlAdmissionRows(engine, identity), SqlCognitiveGraph(engine), engine)
    from nora.infra.sql.tension import SqlTensionStore

    review = LessonReview(lesson_checker, SqlTensionStore(engine)) if lesson_checker else None
    return AdmissionService(
        store, identity, leak_guard, content_checks=content_checks, room_slots=room_slots,
        watch_kinds=WATCH_ADAPTERS.keys(), lesson_review=review,
    )


def condition_matcher(engine: AsyncEngine) -> SqlConditionMatcher:
    return SqlConditionMatcher(engine, SqlCognitiveGraph(engine))


def intention_verifier(
    engine: AsyncEngine, clock: Callable[[], datetime], *, max_poll_gap_seconds: float,
    room_slots: RoomSlots,
) -> IntentionVerifier:
    return IntentionVerifier(
        SqlIntentionVerification(engine, SqlCognitiveGraph(engine)), SqlRollupStore(engine), clock,
        max_poll_gap_ms=int(max_poll_gap_seconds * 1000), room_slots=room_slots,
    )


def graph_clock(engine: AsyncEngine, clock: Callable[[], datetime]) -> GraphClock:
    return GraphClock(SqlDueSchedules(engine), clock)


def watch_poller(
    engine: AsyncEngine, clock: Callable[[], datetime], provider: SearchProvider,
) -> WatchPoller:
    registration = engine.get_execution_options()["item_registration"]
    return WatchPoller(
        SqlWatchResults(engine, registration, WATCH_ADAPTERS.keys()),
        {kind: adapter(provider) for kind, adapter in WATCH_ADAPTERS.items()}, clock,
    )



def memory_intake(
    engine: AsyncEngine, registration: SqlItemRegistration | None = None,
) -> SqlMemoryIntake:
    item_registration = registration or engine.get_execution_options()["item_registration"]
    return SqlMemoryIntake(engine, item_registration, SqlCognitiveGraph(engine))



def claim_support(engine: AsyncEngine) -> SqlClaimSupport:
    return SqlClaimSupport(engine, SqlCognitiveGraph(engine))

def retrieval(
    engine: AsyncEngine, access: IdentityAccess, lineage: LineagePolicy,
    embedder: QueryEmbedder | None, embedding_model: str | None,
) -> RetrievalService:
    """Build the shared scoped graph reader for a pass."""
    return RetrievalService(SqlRetrieval(engine, access, lineage, embedding_model), embedder)


def configured_graph_reads(cfg, engine, access, lineage) -> GraphReadTools | None:
    if not cfg.memory.index.read:
        return None
    index = cfg.memory.index
    embedder = LlamaEmbedder(
        index.embedder_url, timeout_seconds=QUERY_TIMEOUT_SECONDS,
        query_model_file=model_file(index.embedder_model),
    ) if index.embed else None
    model = fingerprint(index.embedder_model) if index.embed else None
    return graph_read_tools(engine, access, lineage, embedder, model)


def graph_read_tools(
    engine: AsyncEngine, access: IdentityAccess, lineage: LineagePolicy,
    embedder: QueryEmbedder | None, embedding_model: str | None,
) -> GraphReadTools:
    return GraphReadTools(retrieval(engine, access, lineage, embedder, embedding_model),
                          engine, access, lineage)


def debrief_store(engine: AsyncEngine) -> SqlDebriefStore:
    return SqlDebriefStore(engine, SqlActStore(
        engine, engine.get_execution_options()["item_registration"],
    ))


def message_graph_ids(engine: AsyncEngine) -> SqlMessageGraphIds:
    return SqlMessageGraphIds(engine)


def pass_assembler(
    engine: AsyncEngine, access: IdentityAccess, lineage: LineagePolicy,
    embedder: QueryEmbedder | None, embedding_model: str | None,
) -> PassAssembler:
    """Build the graph context assembler for cognition passes."""
    return PassAssembler(
        SqlPassAnchors(engine, access, lineage),
        retrieval(engine, access, lineage, embedder, embedding_model),
    )


def proactive_claims(engine: AsyncEngine) -> SqlProactiveClaims:
    return SqlProactiveClaims(engine)


def wake_claims(engine: AsyncEngine) -> SqlWakeClaims:
    return SqlWakeClaims(engine)


def replay_receipts(
    engine: AsyncEngine, access: IdentityAccess, lineage: LineagePolicy, policy: Policy,
) -> SqlReplayReceipts:
    return SqlReplayReceipts(engine, access, lineage, policy)
