"""Shared contract for the managed `BindingStore` — the scoped-name
surface and the profile-journal's scope partition, run against every
implementation (`test_binding_store_contract.py` owns the binding side).

The rules this suite pins, from the port and `authorization.md`:

- a nickname lives under (canonical person, exact scope) — the room sees
  its own override, a sibling room and the parents see honest absence;
- `set_scoped_name` refuses absent and suspended persons, its
  `operation_id` is journal provenance the SQL store enforces RESTRICT;
- `latest_profile_operation` partitions per profile target: the global
  display name and every scope own independent orderings, so a local
  edit never supersedes or replay-overwrites another target's intent;
- consolidation folds the former person's names to the survivor — a
  same-scope collision keeps the SURVIVOR's name, a retired id reads
  through the alias and never selects another person's label.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    OperationRecord,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing.managed import FakeBindingStore

ACCOUNT_1 = str(uuid4())
ACCOUNT_2 = str(uuid4())
ROOM = Scope(ScopeKind.ROOM, "signal/conv-1")
PERSON = "person_alice"
ACCT = Identifier(platform="signal", kind="uuid", value="85290000101")
ACCT_2 = Identifier(platform="signal", kind="uuid", value="85290000102")

_MANAGED_TABLES = (
    "managed_alias, managed_membership, managed_membership_support,"
    " managed_identifier, managed_account, managed_person, managed_operation,"
    " managed_authority_block, managed_observation, managed_scoped_name"
)


class _Store(Protocol):
    """The surface both implementations offer — the suite type-checks the
    fake and the SQL store against the same contract."""

    def mutation(self) -> AbstractAsyncContextManager[None]: ...
    async def register(self, **kw: Any) -> Any: ...
    async def person(self, person_id: str) -> Any: ...
    async def set_lifecycle(self, **kw: Any) -> Any: ...
    async def consolidate(self, *, survivor: str, former: str) -> Any: ...
    async def record_operation(self, record: OperationRecord) -> Any: ...
    async def operation(self, operation_id: str) -> Any: ...
    async def update_operation(self, record: OperationRecord) -> Any: ...
    async def latest_profile_operation(
        self, person_id: str, *, scope: Scope | None = None
    ) -> Any: ...
    async def set_scoped_name(self, **kw: Any) -> Any: ...
    async def scoped_name_for(self, *, person_id: str, scope: Scope) -> Any: ...
    async def aclose(self) -> None: ...


@pytest.fixture(scope="module")
def _managed_db(pg_admin: Any) -> Iterator[str]:
    """ONE database and ONE migration run for this whole file."""
    dbname = "t_managed_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[_Store]:
    if request.param == "memory":
        yield FakeBindingStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_managed_db"))
    try:
        yield SqlBindingStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(f"truncate {_MANAGED_TABLES} cascade")
            )
        await engine.dispose()


def _person(person_id: str = PERSON) -> ManagedPerson:
    return ManagedPerson(person_id=person_id, lifecycle=PersonLifecycle.ACTIVE)


def _account(
    account_id: str | None = None,
    person_id: str = PERSON,
    identifiers: frozenset[Identifier] = frozenset({ACCT}),
) -> ManagedAccount:
    return ManagedAccount(
        account_id=account_id or str(uuid4()),
        person_id=person_id,
        platform="signal",
        identifiers=identifiers,
        state=AccountState.BOUND,
    )


def _profile_record(
    op_id: str, *, person: str = PERSON, scope: Scope | None = None
) -> OperationRecord:
    snapshot: dict[str, Any] = {"target_person": person}
    if scope is None:
        snapshot["display_name"] = "Generated global"
    else:
        snapshot["profile_scope"] = {"kind": scope.kind.value, "id": scope.id}
        snapshot["nickname"] = "Generated local"
    return OperationRecord(
        operation_id=op_id, kind="update_profile",
        state=OperationJournalState.PENDING,
        steps=MappingProxyType({"profile": "pending"}),
        snapshot=MappingProxyType(snapshot))


async def _journal(store: _Store, op_id: str) -> None:
    """Journal the op first — the row's `operation_id` is RESTRICT provenance."""
    await store.record_operation(OperationRecord(
        operation_id=op_id, kind="update_profile",
        state=OperationJournalState.COMPLETE,
        steps=MappingProxyType({}), snapshot=MappingProxyType({})))


async def test_latest_profile_operation_partitions_global_and_each_scope(
    store: _Store,
):
    """A scoped nickname and the global name own independent orderings:
    newest-per-target, never newest-per-person across profile targets."""
    await store.record_operation(_profile_record("generated-global-1"))
    await store.record_operation(_profile_record("generated-room-1", scope=ROOM))
    # The newer scoped op does not own the global name.
    latest = await store.latest_profile_operation(PERSON)
    assert latest is not None and latest.operation_id == "generated-global-1"
    latest = await store.latest_profile_operation(PERSON, scope=ROOM)
    assert latest is not None and latest.operation_id == "generated-room-1"
    # A different scope is independent too — and an unread scope is absent.
    other = Scope(ScopeKind.UNIT, "generated-unit")
    assert await store.latest_profile_operation(PERSON, scope=other) is None
    await store.record_operation(_profile_record("generated-room-2", scope=ROOM))
    latest = await store.latest_profile_operation(PERSON, scope=ROOM)
    assert latest is not None and latest.operation_id == "generated-room-2"


async def test_scoped_name_reads_the_exact_scope_and_nothing_else(store: _Store):
    """A nickname lives under (canonical person, exact scope): the room
    sees its own override, a sibling room and the unit see absence —
    never another scope's name."""
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None)
    unit = Scope(ScopeKind.UNIT, "generated-unit")
    sibling = Scope(ScopeKind.ROOM, "signal/conv-2")
    assert await store.scoped_name_for(person_id=PERSON, scope=ROOM) is None
    await _journal(store, "generated-op-1")
    outcome = await store.set_scoped_name(
        person_id=PERSON, scope=ROOM, nickname="Generated room name",
        operation_id="generated-op-1")
    assert outcome.status is OutcomeStatus.COMPLETE
    assert (await store.scoped_name_for(person_id=PERSON, scope=ROOM)
            == "Generated room name")
    assert await store.scoped_name_for(person_id=PERSON, scope=sibling) is None
    assert await store.scoped_name_for(person_id=PERSON, scope=unit) is None
    # The newest committed write wins — idempotent same-value replay.
    await _journal(store, "generated-op-2")
    assert (await store.set_scoped_name(
        person_id=PERSON, scope=ROOM, nickname="Generated newer",
        operation_id="generated-op-2")).status is OutcomeStatus.COMPLETE
    assert (await store.set_scoped_name(
        person_id=PERSON, scope=ROOM, nickname="Generated newer",
        operation_id="generated-op-2")).status is OutcomeStatus.COMPLETE
    assert (await store.scoped_name_for(person_id=PERSON, scope=ROOM)
            == "Generated newer")


async def test_scoped_name_refuses_absent_and_suspended_persons(store: _Store):
    await _journal(store, "generated-op-1")
    outcome = await store.set_scoped_name(
        person_id="generated-absent", scope=ROOM, nickname="Generated",
        operation_id="generated-op-1")
    assert outcome.status is OutcomeStatus.DENIED
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None)
    await store.set_lifecycle(
        person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED)
    await _journal(store, "generated-op-2")
    outcome = await store.set_scoped_name(
        person_id=PERSON, scope=ROOM, nickname="Generated",
        operation_id="generated-op-2")
    assert outcome.status is OutcomeStatus.DENIED


async def test_scoped_names_fold_through_consolidation(store: _Store):
    """A retired person id reads the survivor's nickname row through the
    alias — never another person's stale label at the same scope."""
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None)
    await store.register(
        person=_person("person_former"),
        account=_account(ACCOUNT_2, person_id="person_former",
                         identifiers=frozenset({ACCT_2})),
        membership=None)
    await _journal(store, "generated-op-1")
    await store.set_scoped_name(
        person_id="person_former", scope=ROOM, nickname="Generated former",
        operation_id="generated-op-1")
    await _journal(store, "generated-op-2")
    await store.set_scoped_name(
        person_id=PERSON, scope=ROOM, nickname="Generated survivor",
        operation_id="generated-op-2")
    outcome = await store.consolidate(survivor=PERSON, former="person_former")
    assert outcome.status is OutcomeStatus.COMPLETE
    # Collision at the same scope: the survivor's own name wins.
    assert (await store.scoped_name_for(person_id="person_former", scope=ROOM)
            == "Generated survivor")
    # A scope only the former held moves to the survivor.
    unit = Scope(ScopeKind.UNIT, "generated-unit")
    await store.register(
        person=_person("person_late"),
        account=_account(str(uuid4()), person_id="person_late",
                         identifiers=frozenset({
                             Identifier("signal", "uuid", "85290000103")})),
        membership=None)
    await _journal(store, "generated-op-3")
    await store.set_scoped_name(
        person_id="person_late", scope=unit, nickname="Generated unit name",
        operation_id="generated-op-3")
    assert (await store.consolidate(
        survivor=PERSON, former="person_late")).status is OutcomeStatus.COMPLETE
    assert (await store.scoped_name_for(person_id="person_late", scope=unit)
            == "Generated unit name")
