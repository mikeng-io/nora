
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.delivery_arbitration import SqlDeliveryHistory
from nora.memory.adapters.graph_search import (
    projected_graph_search_rows as projected_graph_search_rows,
)
from nora.memory.adapters.graph_search import rebuild_graph_search as rebuild_graph_search
from nora.memory.adapters.graph_state import entity_anchor_aliases as entity_anchor_aliases
from nora.memory.adapters.graph_state import unit_edges as unit_edges
from nora.memory.adapters.legacy_agenda import (
    seed_scheduled_intention as seed_scheduled_intention,
)
from nora.memory.adapters.profile_snapshot import PROFILE_METADATA as PROFILE_METADATA
from nora.memory.adapters.profile_snapshot import PROFILE_PARTS as PROFILE_PARTS
from nora.memory.adapters.profile_snapshot import profile_snapshot as profile_snapshot
from nora.memory.adapters.review_anchors import SqlReviewTargets as SqlReviewTargets
from nora.memory.adapters.subject_claims import SqlSubjectClaims
from nora.memory.adapters.tools import build as _build
from nora.memory.adapters.tools.wake import WAKE_HISTORY_TOOLS as WAKE_HISTORY_TOOLS
from nora.memory.adapters.tools.wake import WAKE_TOOLS as WAKE_TOOLS
from nora.memory.adapters.tools.wake import WakeToolSurface as WakeToolSurface
from nora.memory.app.acts import ActEnvelope
from nora.memory.app.delivery_arbitration import DeliveryArbitration
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


