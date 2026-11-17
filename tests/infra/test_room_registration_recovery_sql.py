"""A fresh process graph resumes an interrupted room registration from its
durable PostgreSQL journal — the same message retried over a rebuilt engine
and rebuilt services finishes the recorded effect and nothing else."""

from uuid import uuid4

import pytest

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    OutcomeStatus,
    PersonLifecycle,
    room_scope_id,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import NormalizedMessage
from nora.infra.sql.account_links import SqlLinkRequests
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people import RoomManagement, RoomRequest
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeIdentityRegistry,
    FakeTopologySource,
    unavailable_outcome,
)

WS_ID = "generated-ws"
UNIT_ID = "generated-unit"
CONV = "generated-conv"
ROOM_ID = room_scope_id("signal", CONV)
ROOM = Scope(ScopeKind.ROOM, ROOM_ID)
OP_ID = "85290000101"
OP_PERSON = "person_generated_operator"
TARGET_ID = "85290000102"
OPERATION_ID = f"room:signal:{CONV}:generated-m-1"


@pytest.fixture
def database(clean_pg_url):
    return clean_pg_url


@pytest.fixture
async def engine(database):
    engine = create_sql_engine(database)
    yield engine
    await engine.dispose()


class _EnsuresDown(FakeIdentityRegistry):
    """`ensure_identity` answers unavailable while `identity_state` still
    works — a Kratos write outage that does not block the sender's own
    session check (the registry's full `down` would fail that first)."""

    def __init__(self) -> None:
        super().__init__()
        self.ensures_down = False

    async def ensure_identity(self, **kwargs):
        if self.ensures_down:
            return unavailable_outcome(), None
        return await super().ensure_identity(**kwargs)


class _CrashAtStep(SqlBindingStore):
    """A crash at exactly one journaled boundary — the named step raises
    once before it applies (or once while the `complete` state is being
    written for `finish`), then the store behaves. `calls` counts each
    store effect so a resume's skipped steps stay observable."""

    def __init__(self, engine, step: str, *, fired: bool = False) -> None:
        super().__init__(engine)
        self._step = step
        self._fired = fired
        self.calls: dict[str, int] = {}

    def _count(self, step: str) -> None:
        self.calls[step] = self.calls.get(step, 0) + 1

    async def register(self, **kwargs):
        self._count("binding")
        return await super().register(**kwargs)

    async def set_membership(self, **kwargs):
        self._count("membership")
        if self._step == "membership" and not self._fired:
            self._fired = True
            raise ConnectionError("generated crash at membership")
        return await super().set_membership(**kwargs)

    async def correlate_kratos(self, **kwargs):
        self._count("correlation")
        if self._step == "correlation" and not self._fired:
            self._fired = True
            raise ConnectionError("generated crash at correlation")
        return await super().correlate_kratos(**kwargs)

    async def update_operation(self, record):
        if (
            self._step == "finish"
            and not self._fired
            and record.state is OperationJournalState.COMPLETE
        ):
            self._fired = True
            raise ConnectionError("generated crash at finish")
        return await super().update_operation(record)


def services(engine, registry, backend, bindings=None):
    graph = ManagedServices(
        bindings or SqlBindingStore(engine), backend,
        FakeTopologySource(
            rooms={ROOM_ID: (UNIT_ID, False)},
            units={UNIT_ID: WS_ID},
            workspaces={WS_ID},
        ),
        registry, SqlLinkRequests(engine))
    return graph, RoomManagement(graph)


async def seed_operator(bindings, backend, registry):
    await bindings.register(
        person=ManagedPerson(
            person_id=OP_PERSON, lifecycle=PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            account_id=str(uuid4()), person_id=OP_PERSON,
            platform="signal",
            identifiers=frozenset({Identifier("signal", "phone", OP_ID)}),
            state=AccountState.BOUND),
        membership=None)
    await backend.put_relation(
        scope=ROOM, relation="grant_manage_members", person_id=OP_PERSON)
    registry.identities[OP_PERSON] = {
        "id": "generated-kratos-operator", "state": "active", "traits": {}}


def register_message():
    return NormalizedMessage(
        platform="signal", conversation_id=CONV, conversation_type="group",
        sender_id=OP_ID, sender_name=None, timestamp=1_700_000_000_000,
        body="!!!member register", raw={}, mentions=frozenset({TARGET_ID}),
        quote=None, message_id="generated-m-1", edit_of=None, touch=None)


@pytest.mark.parametrize(
    ("step", "done_steps", "pending_steps"),
    [
        ("membership", ("binding",), ("membership", "kratos", "correlation")),
        ("correlation", ("binding", "membership", "kratos"), ("correlation",)),
        ("finish", ("binding", "membership", "kratos", "correlation"), ()),
    ],
)
async def test_a_fresh_engine_resumes_the_interrupted_registration(
    engine, database, step, done_steps, pending_steps,
):
    """Interrupted at each journaled boundary, a rebuilt engine + service
    graph over the same database resumes the recorded effect: the account
    keeps its surrogate id, owed steps run once, done steps never re-run."""
    registry, backend = FakeIdentityRegistry(), FakeAuthorityBackend()
    store = _CrashAtStep(engine, step)
    graph, management = services(engine, registry, backend, bindings=store)
    await seed_operator(store, backend, registry)
    message = register_message()
    interrupted = await management.invoke(message, RoomRequest(kind="register"))
    assert interrupted.status is OutcomeStatus.INCOMPLETE
    record = await graph.bindings.operation(OPERATION_ID)
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE
    for done in done_steps:
        assert record.steps[done] == "done"
    for pending in pending_steps:
        assert record.steps[pending] == "pending"
    before = await graph.bindings.record_for(
        account=Identifier("signal", "phone", TARGET_ID))
    assert before is not None
    restarted = create_sql_engine(database)
    try:
        fresh_store = _CrashAtStep(restarted, step, fired=True)
        fresh_graph, fresh = services(
            restarted, registry, backend, bindings=fresh_store)
        resumed = await fresh.invoke(message, RoomRequest(kind="register"))
        assert resumed.status is OutcomeStatus.COMPLETE
        after = await fresh_graph.bindings.record_for(
            account=Identifier("signal", "phone", TARGET_ID))
        assert after is not None
        assert after.person_id == before.person_id
        assert after.account_id == before.account_id
        person = await fresh_graph.bindings.person(before.person_id)
        assert person is not None
        assert person.kratos_identity_id == registry.identities[before.person_id]["id"]
        members = await fresh_graph.bindings.members_of(ROOM)
        assert any(m.person_id == before.person_id for m in members)
        assert len(await fresh_graph.bindings.persons()) == 2
        record = await fresh_graph.bindings.operation(OPERATION_ID)
        assert record is not None
        assert record.state is OperationJournalState.COMPLETE
        # Only the owed steps ran again over the fresh engine — done steps
        # never re-ran (the finish crash re-runs nothing at all).
        assert fresh_store.calls.get("binding", 0) == 0
        assert fresh_store.calls.get("membership", 0) == (1 if step == "membership" else 0)
        assert fresh_store.calls.get("correlation", 0) == (0 if step == "finish" else 1)
        again = await fresh.invoke(message, RoomRequest(kind="register"))
        assert again.status is OutcomeStatus.COMPLETE
        assert again.detail == "replayed"
    finally:
        await restarted.dispose()


async def test_a_fresh_engine_resumes_after_the_kratos_outage(engine, database):
    """An outage mid-registration journals the owed remainder; the same
    message retried over a fresh engine finishes it — never a second
    person, account, membership or Kratos identity."""
    registry, backend = _EnsuresDown(), FakeAuthorityBackend()
    graph, management = services(engine, registry, backend)
    await seed_operator(graph.bindings, backend, registry)
    message = register_message()
    registry.ensures_down = True
    interrupted = await management.invoke(message, RoomRequest(kind="register"))
    assert interrupted.status is OutcomeStatus.INCOMPLETE
    record = await graph.bindings.operation(OPERATION_ID)
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE
    assert record.steps["binding"] == "done"
    assert record.steps["membership"] == "done"
    assert record.steps["kratos"] == "pending"
    before = await graph.bindings.record_for(
        account=Identifier("signal", "phone", TARGET_ID))
    assert before is not None
    restarted = create_sql_engine(database)
    try:
        fresh_graph, fresh = services(restarted, registry, backend)
        registry.ensures_down = False
        resumed = await fresh.invoke(message, RoomRequest(kind="register"))
        assert resumed.status is OutcomeStatus.COMPLETE
        after = await fresh_graph.bindings.record_for(
            account=Identifier("signal", "phone", TARGET_ID))
        assert after is not None
        assert after.person_id == before.person_id
        assert after.account_id == before.account_id
        person = await fresh_graph.bindings.person(before.person_id)
        assert person is not None
        assert person.kratos_identity_id == registry.identities[before.person_id]["id"]
        members = await fresh_graph.bindings.members_of(ROOM)
        assert [m.person_id for m in members] == [before.person_id]
        record = await fresh_graph.bindings.operation(OPERATION_ID)
        assert record is not None
        assert record.state is OperationJournalState.COMPLETE
    finally:
        await restarted.dispose()
