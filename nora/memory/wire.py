from collections.abc import Callable, Iterable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.access import IdentityAccess
from nora.agent.egress import LeakGuard
from nora.agent.lineage import LineagePolicy
from nora.domain.memory_index import QueryEmbedder
from nora.domain.ports import SearchProvider
from nora.infra.embedder import QUERY_TIMEOUT_SECONDS, LlamaEmbedder, fingerprint, model_file
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.infra.sql.rollups import SqlRollupStore
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.admission_rows import SqlAdmissionRows
from nora.memory.adapters.admission_store import SqlAdmissionStore
from nora.memory.adapters.claim_support import SqlClaimSupport
from nora.memory.adapters.condition_matcher import SqlConditionMatcher
from nora.memory.adapters.debrief_store import SqlDebriefStore
from nora.memory.adapters.delivery_arbitration import SqlDeliveryHistory
from nora.memory.adapters.graph_clock import SqlDueSchedules
from nora.memory.adapters.graph_search import (
    projected_graph_search_rows as projected_graph_search_rows,
)
from nora.memory.adapters.graph_search import rebuild_graph_search as rebuild_graph_search
from nora.memory.adapters.graph_state import entity_anchor_aliases as entity_anchor_aliases
from nora.memory.adapters.graph_state import unit_edges as unit_edges
from nora.memory.adapters.graph_store import SqlCognitiveGraph
from nora.memory.adapters.legacy_agenda import (
    SqlLegacyAgenda,
)
from nora.memory.adapters.legacy_agenda import (
    seed_scheduled_intention as seed_scheduled_intention,
)
from nora.memory.adapters.memory_intake import SqlMemoryIntake
from nora.memory.adapters.message_graph_ids import SqlMessageGraphIds
from nora.memory.adapters.pass_anchors import SqlPassAnchors
from nora.memory.adapters.pass_assembly import PassAssembler
from nora.memory.adapters.proactive_claims import SqlProactiveClaims
from nora.memory.adapters.profile_snapshot import PROFILE_METADATA as PROFILE_METADATA
from nora.memory.adapters.profile_snapshot import PROFILE_PARTS as PROFILE_PARTS
from nora.memory.adapters.profile_snapshot import profile_snapshot as profile_snapshot
from nora.memory.adapters.replay_receipts import SqlReplayReceipts
from nora.memory.adapters.retrieval_store import SqlRetrieval
from nora.memory.adapters.review_anchors import SqlReviewTargets as SqlReviewTargets
from nora.memory.adapters.subject_claims import SqlSubjectClaims
from nora.memory.adapters.tools import build as _build
from nora.memory.adapters.tools.search import GraphReadTools
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
from nora.memory.app.migrate_legacy_agenda import MigrateLegacyAgenda
from nora.memory.app.retrieval import RetrievalService
from nora.memory.app.verify import IntentionVerifier
from nora.memory.app.watches import WatchPoller
from nora.memory.domain.admission_ports import LessonChecker, RoomSlots
from nora.memory.domain.graph_operations import REVIEW_PARTS as REVIEW_PARTS
from nora.memory.domain.graph_operations import WAKE_OPERATIONS as WAKE_OPERATIONS
from nora.persons import Persons
from nora.policy import Policy

"""Compose this area's tool adapters from shared dependencies."""


def subject_claims(engine: AsyncEngine) -> SqlSubjectClaims:
    return SqlSubjectClaims(engine)


def tool_specs(deps):
    return _build(deps)


def delivery_arbitration(engine: AsyncEngine, policy: Policy) -> DeliveryArbitration:
    return DeliveryArbitration(SqlDeliveryHistory(engine), policy)


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


def admission(
    engine: AsyncEngine, persons: Persons | None, policy: Policy, leak_guard: LeakGuard, *,
    content_checks: bool, room_slots: RoomSlots,
    nora_accounts: Iterable[tuple[str, str]] = (),
    access: IdentityAccess | None = None,
    lesson_checker: LessonChecker | None = None,
) -> AdmissionService:
    identity = AdmissionIdentity(persons, policy, access=access, nora_accounts=nora_accounts)
    store = SqlAdmissionStore(
        SqlAdmissionRows(engine, identity), SqlCognitiveGraph(engine), engine,
        lesson_checker=lesson_checker,
    )
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


def legacy_agenda_migration(
    engine: AsyncEngine, policy: Policy, persons: Persons,
) -> MigrateLegacyAgenda:
    return MigrateLegacyAgenda(SqlLegacyAgenda(engine, policy, persons))


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


def configured_graph_reads(cfg, engine, access, lineage) -> GraphReadTools:
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
