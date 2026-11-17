"""Managed ingest failure reaches the queue boundary without committing a denied handoff."""

from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.access import IdentityAccess
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.obs.metrics import Metrics
from nora.people.app.managed_authority import AuthorityService
from nora.policy import Conversation, Policy
from nora.testing import FakeMemoryStore
from nora.testing.managed import (
    FakeIdentityRegistry,
    FakeTopologySource,
)
from nora.testing.managed_authority import FakeAuthorityBackend

PERSON = "generated-person"
ACCOUNT = Identifier("signal", "uuid", "generated-account")
ROOM_ID = "generated-room"
ROOM = Scope(ScopeKind.ROOM, f"signal/{ROOM_ID}")
MESSAGE = NormalizedMessage(
    "signal", ROOM_ID, "group", ACCOUNT.value, "Generated person",
    1_790_000_000_000, "nora.000 hello", {},
)


@pytest.fixture
async def engine(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _pipeline(engine):
    bindings = SqlBindingStore(engine)
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: ("generated-unit", False)},
        units={"generated-unit": "generated-workspace"}, workspaces={"generated-workspace"},
    )
    assert (await bindings.register(
        person=ManagedPerson(PERSON, PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            str(uuid4()), PERSON, "signal", frozenset({ACCOUNT}), AccountState.BOUND
        ), membership=None,
    )).status is OutcomeStatus.COMPLETE
    assert (await backend.put_relation(scope=ROOM, relation="member", person_id=PERSON)
            ).status is OutcomeStatus.COMPLETE
    access = IdentityAccess(
        bindings=bindings, authority=AuthorityService(bindings, backend, topology),
        topology=topology, registry=FakeIdentityRegistry(),
    )
    room = Conversation("signal", frozenset({ROOM_ID}), frozenset(), unit="generated-unit")
    policy = Policy((room,), _by_id={("signal", ROOM_ID): room})
    pipeline = IngestPipeline(
        audit=SqlRecordStore(engine), memory=FakeMemoryStore(),
        policy=TriggerTextPolicy("nora.000"),
        allowlist=policy, access=access, metrics=Metrics(), spawn=lambda task: task.close(),
    )
    return pipeline, backend


async def _message_count(engine):
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT count(*) FROM messages"))).scalar_one()


async def test_durable_authority_outage_leaves_no_committed_handoff_and_recovers_once(engine):
    pipeline, backend = await _pipeline(engine)
    handoffs = []

    async def handoff(_conn, row_id, context):
        handoffs.append((row_id, context["decision"]))

    backend.down = True
    pending = await pipeline.run(message=MESSAGE, tx_hook=handoff, durable=True)
    assert pending.outcome == "sender_unavailable"
    assert await _message_count(engine) == 0
    assert handoffs == []

    backend.down = False
    recovered = await pipeline.run(message=MESSAGE, tx_hook=handoff, durable=True)
    assert recovered.outcome == "engaged"
    assert recovered.person.person_id == PERSON
    assert await _message_count(engine) == 1
    assert len(handoffs) == 1 and handoffs[0][1].engage


async def test_inline_authority_outage_is_unavailable_instead_of_recorded_silence(engine):
    pipeline, backend = await _pipeline(engine)
    backend.down = True
    result = await pipeline.run(message=MESSAGE)
    assert result.outcome == "sender_unavailable"
    assert result.decision.reason == "sender_unavailable"
    assert not result.decision.engage
    assert await _message_count(engine) == 1


async def test_current_revocation_refuses_managed_ingest_after_a_positive_admission(engine):
    pipeline, backend = await _pipeline(engine)
    admitted = await pipeline.run(message=MESSAGE, durable=True)
    assert admitted.decision.engage
    assert (await backend.drop_relation(scope=ROOM, relation="member", person_id=PERSON)
            ).status is OutcomeStatus.COMPLETE
    refused = await pipeline.run(message=MESSAGE, durable=True)
    assert not refused.decision.engage
    assert refused.decision.reason == "sender_not_allowed"
