"""Shared contract for the managed `BindingStore` — every implementation
runs every test.

Two implementations: `nora.infra.sql.managed_identity.SqlBindingStore` over
real Postgres in a testcontainer, and `nora.testing.managed.FakeBindingStore`
built on dicts. The port's own docstring states the rules this suite pins:

- registration is ATOMIC and IDEMPOTENT — person + account + identifiers +
  membership commit together; replaying the same effect is a no-op;
- one matchable identifier, one owner — a claimed identifier raises
  `IdentifierConflictError`, never merges, and never re-binds to a
  DIFFERENT person even after revocation;
- `record_for`/`account_for`/`resolve` see BOUND bindings only;
- the journal refuses a reused `operation_id` carrying a different effect
  (`OperationConflictError`).

The database and its migrations are built once per module and the managed
tables are truncated between tests, per the contract-suite house rule.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    AuthorityBlock,
    IdentifierConflictError,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OperationConflictError,
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
PHONE = Identifier(platform="signal", kind="phone", value="85290000101")

_MANAGED_TABLES = (
    "managed_alias, managed_membership, managed_membership_support,"
    " managed_identifier, managed_account, managed_person, managed_operation,"
    " managed_authority_block"
)


class _Store(Protocol):
    """The surface both implementations offer — the suite type-checks the
    SHAPE, not whichever class the fixture yielded."""

    def mutation(self) -> AbstractAsyncContextManager[None]: ...
    async def identity_held(self, person_id: str) -> bool: ...
    async def hold_identity(self, **kw: Any) -> Any: ...
    async def release_identity_hold(self, **kw: Any) -> Any: ...
    async def register(self, **kw: Any) -> Any: ...
    async def person(self, person_id: str) -> Any: ...
    async def record_for(self, *, account: Identifier) -> Any: ...
    async def account_for(self, platform: str, kind: str, value: str) -> Any: ...
    async def resolve(self, platform: str, value: str) -> Any: ...
    async def accounts_for(self, person_id: str) -> Any: ...
    async def set_lifecycle(self, **kw: Any) -> Any: ...
    async def person_lifecycle(self, person_id: str) -> Any: ...
    async def set_membership(self, **kw: Any) -> Any: ...
    async def drop_membership_support(self, **kw: Any) -> Any: ...
    async def consolidate(self, **kw: Any) -> Any: ...
    async def memberships_for(self, person_id: str) -> Any: ...
    async def members_of(self, scope: Scope) -> Any: ...
    async def revoke_binding(self, *, account: Identifier) -> Any: ...
    async def add_alias(self, *, former_id: str, person_id: str) -> Any: ...
    async def canonical_person(self, person_id: str) -> Any: ...
    async def correlate_kratos(self, **kw: Any) -> Any: ...
    async def record_operation(self, record: OperationRecord) -> Any: ...
    async def operation(self, operation_id: str) -> Any: ...
    async def update_operation(self, record: OperationRecord) -> Any: ...
    async def incomplete_operations(self) -> Any: ...
    async def block_authority(self, block: AuthorityBlock) -> Any: ...
    async def authority_blocks(self) -> Any: ...
    async def clear_block(self, *, person_id: str, scope: Scope) -> Any: ...
    async def aclose(self) -> None: ...


@pytest.fixture(scope="module")
def _managed_db(pg_admin: Any) -> Iterator[str]:
    """ONE database and ONE migration run for this whole file (see module docstring)."""
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


async def test_registration_commits_person_account_identifiers_and_membership(
    store: _Store,
):
    outcome = await store.register(
        person=_person(),
        account=_account(ACCOUNT_1, identifiers=frozenset({ACCT, PHONE})),
        membership=ManagedMembership(
            person_id=PERSON, scope=ROOM,
            state=MembershipState.MEMBER, source="registration",
        ),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    person = await store.person(PERSON)
    assert person is not None and person.lifecycle is PersonLifecycle.ACTIVE
    # Every identifier — whichever kind — resolves to the one account.
    for ident in (ACCT, PHONE):
        bound = await store.record_for(account=ident)
        assert bound is not None and bound.account_id == ACCOUNT_1
    account = await store.account_for("signal", "uuid", "85290000101")
    assert account is not None and account.person_id == PERSON
    # and the flat platform lookup crosses kinds
    assert await store.resolve("signal", "85290000101") is not None
    members = await store.members_of(ROOM)
    assert [m.person_id for m in members] == [PERSON]


async def test_replaying_the_same_registration_is_a_no_op(store: _Store):
    for _ in range(2):
        outcome = await store.register(
            person=_person(), account=_account(ACCOUNT_1), membership=None
        )
        assert outcome.status is OutcomeStatus.COMPLETE
    assert len(await store.accounts_for(PERSON)) == 1


async def test_an_identifier_owned_by_another_person_is_a_conflict_never_a_merge(
    store: _Store,
):
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    with pytest.raises(IdentifierConflictError):
        await store.register(
            person=_person("person_other"),
            account=_account(
                ACCOUNT_2, person_id="person_other",
                identifiers=frozenset({ACCT}),
            ),
            membership=None,
        )
    # and the original ownership is untouched
    bound = await store.record_for(account=ACCT)
    assert bound is not None and bound.person_id == PERSON


async def test_two_accounts_on_one_person_are_distinct_bindings(store: _Store):
    """One canonical person, many accounts — a second account adds a
    binding, never a merge and never a second person."""
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    outcome = await store.register(
        person=_person(),
        account=_account(ACCOUNT_2, identifiers=frozenset({ACCT_2})),
        membership=None,
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    accounts = await store.accounts_for(PERSON)
    assert len(accounts) == 2


async def test_revoked_bindings_stop_resolving_and_never_rebind_elsewhere(
    store: _Store,
):
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    outcome = await store.revoke_binding(account=ACCT)
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await store.record_for(account=ACCT) is None
    assert await store.account_for("signal", "uuid", "85290000101") is None
    # A revoked identifier can never be claimed for a DIFFERENT person.
    with pytest.raises(IdentifierConflictError):
        await store.register(
            person=_person("person_other"),
            account=_account(
                ACCOUNT_2, person_id="person_other",
                identifiers=frozenset({ACCT}),
            ),
            membership=None,
        )


async def test_lifecycle_changes_revise_the_person(store: _Store):
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    before = await store.person(PERSON)
    outcome = await store.set_lifecycle(
        person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    after = await store.person(PERSON)
    assert before is not None and after is not None
    assert after.lifecycle is PersonLifecycle.SUSPENDED
    assert after.revision > before.revision
    # the binding itself is untouched — suspension is not revocation
    bound = await store.record_for(account=ACCT)
    assert bound is not None and bound.state is AccountState.BOUND


async def test_leaving_is_a_state_not_a_delete(store: _Store):
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    await store.set_membership(
        person_id=PERSON, scope=ROOM, member=True, source="delegation"
    )
    await store.set_membership(
        person_id=PERSON, scope=ROOM, member=False, source="delegation"
    )
    memberships = await store.memberships_for(PERSON)
    assert len(memberships) == 1
    assert memberships[0].state is MembershipState.LEFT
    assert memberships[0].source == "delegation"


async def test_an_alias_folds_a_retired_id_to_the_survivor(store: _Store):
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    outcome = await store.add_alias(former_id="person_old", person_id=PERSON)
    assert outcome.status is OutcomeStatus.COMPLETE
    assert await store.canonical_person("person_old") == PERSON
    # a live person is never an alias
    outcome = await store.add_alias(former_id=PERSON, person_id=PERSON)
    assert outcome.status is OutcomeStatus.DENIED


async def test_kratos_correlation_is_one_person_one_identity(store: _Store):
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    outcome = await store.correlate_kratos(
        person_id=PERSON, kratos_identity_id="kratos-1"
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    # the same correlation replays; a different identity refuses
    outcome = await store.correlate_kratos(
        person_id=PERSON, kratos_identity_id="kratos-1"
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    outcome = await store.correlate_kratos(
        person_id=PERSON, kratos_identity_id="kratos-2"
    )
    assert outcome.status is OutcomeStatus.DENIED


async def test_the_journal_records_and_rejects_conflicting_reuse(store: _Store):
    record = OperationRecord(
        operation_id="op-1", kind="grant_access",
        state=OperationJournalState.PENDING,
        steps=MappingProxyType({"authority": "pending"}),
        snapshot=MappingProxyType({"kind": "grant_access", "permits": ["speak"]}),
    )
    outcome = await store.record_operation(record)
    assert outcome.status is OutcomeStatus.COMPLETE
    # identical replay is a no-op
    outcome = await store.record_operation(record)
    assert outcome.status is OutcomeStatus.COMPLETE
    # a different effect under the same id is a conflict, not a retry
    with pytest.raises(OperationConflictError):
        await store.record_operation(
            OperationRecord(
                operation_id="op-1", kind="grant_access",
                state=OperationJournalState.PENDING,
                steps=MappingProxyType({"authority": "pending"}),
                snapshot=MappingProxyType(
                    {"kind": "grant_access", "permits": ["view"]}
                ),
            )
        )


async def test_authority_blocks_persist_and_clear(store: _Store):
    block = AuthorityBlock(
        person_id=PERSON, scope=ROOM,
        reason="revoke-unconfirmed", operation_id="op-1",
    )
    await store.block_authority(block)
    blocks = await store.authority_blocks()
    assert [(b.person_id, b.operation_id) for b in blocks] == [(PERSON, "op-1")]
    await store.clear_block(person_id=PERSON, scope=ROOM)
    assert await store.authority_blocks() == ()


async def test_incomplete_operations_surface_for_reconciliation(store: _Store):
    await store.record_operation(
        OperationRecord(
            operation_id="op-1", kind="set_membership",
            state=OperationJournalState.INCOMPLETE,
            steps=MappingProxyType({"authority": "done", "binding": "pending"}),
            snapshot=MappingProxyType({"kind": "set_membership"}),
        )
    )
    incomplete = await store.incomplete_operations()
    assert [r.operation_id for r in incomplete] == ["op-1"]


async def test_mutation_gate_serializes_journal_read_modify_write(store: _Store):
    record = OperationRecord("generated-counter", "grant_access", OperationJournalState.PENDING,
                            MappingProxyType({"counter": "0"}))
    await store.record_operation(record)

    async def increment():
        async with store.mutation():
            current = await store.operation(record.operation_id)
            await asyncio.sleep(0)
            await store.update_operation(replace(
                current, steps=MappingProxyType({
                    "counter": str(int(current.steps["counter"]) + 1),
                }),
            ))

    await asyncio.gather(*(increment() for _ in range(8)))
    assert (await store.operation(record.operation_id)).steps["counter"] == "8"


async def test_mutation_gate_release_preserves_committed_saga_steps(store: _Store):
    record = OperationRecord("generated-step", "grant_access", OperationJournalState.INCOMPLETE,
                            MappingProxyType({"authority": "pending"}))
    with pytest.raises(RuntimeError, match="generated interruption"):
        async with store.mutation():
            await store.record_operation(record)
            raise RuntimeError("generated interruption")
    async with store.mutation():
        found = await store.operation(record.operation_id)
        assert found.state is OperationJournalState.INCOMPLETE


async def test_publication_holds_are_canonical_and_owned_by_each_operation(store: _Store):
    assert (await store.hold_identity(person_id=PERSON,
        operation_id="generated-missing")).status is OutcomeStatus.DENIED
    await store.register(person=_person(), account=_account(ACCOUNT_1), membership=None)
    await store.add_alias(former_id="generated-retired", person_id=PERSON)
    assert not await store.identity_held(PERSON)
    for operation in ("generated-publication-a", "generated-publication-b"):
        for _ in range(2):
            assert (await store.hold_identity(person_id=PERSON,
                operation_id=operation)).status is OutcomeStatus.COMPLETE
    assert await store.identity_held("generated-retired")
    await store.release_identity_hold(person_id="generated-retired",
                                       operation_id="generated-publication-a")
    assert await store.identity_held(PERSON)
    await store.release_identity_hold(person_id=PERSON, operation_id="generated-unrelated")
    assert await store.identity_held(PERSON)
    await store.release_identity_hold(person_id=PERSON, operation_id="generated-publication-b")
    assert not await store.identity_held("generated-retired")


async def test_consolidation_moves_accounts_merges_supports_and_aliases(store: _Store):
    survivor = "generated-survivor"
    former = "generated-former"
    await store.register(person=_person(survivor), account=_account(
        ACCOUNT_1, person_id=survivor), membership=None)
    await store.register(
        person=_person(former),
        account=_account(ACCOUNT_2, person_id=former,
                         identifiers=frozenset({ACCT_2})),
        membership=ManagedMembership(
            person_id=former, scope=ROOM,
            state=MembershipState.MEMBER, source="registration"),
    )
    await store.set_membership(
        person_id=former, scope=ROOM, member=True, source="sync")
    survivor_revision = (await store.person(survivor)).revision
    moved_revision = (await store.record_for(account=ACCT_2)).revision

    outcome = await store.consolidate(survivor=survivor, former=former)
    assert outcome.status is OutcomeStatus.COMPLETE

    # The retired id canonicalizes: person() resolves it to the survivor.
    assert (await store.person(former)).person_id == survivor
    assert await store.canonical_person(former) == survivor
    survivor_person = await store.person(survivor)
    assert survivor_person is not None
    assert survivor_person.revision > survivor_revision
    moved = await store.record_for(account=ACCT_2)
    assert moved is not None and moved.person_id == survivor
    assert moved.revision > moved_revision
    # The supports merged per scope — the survivor carries BOTH sources.
    room = {m for m in await store.memberships_for(survivor) if m.scope == ROOM}
    assert {frozenset(m.sources) for m in room} == {
        frozenset({"registration", "sync"})}
    # Reads through the retired id resolve to the survivor, not nothing.
    assert await store.memberships_for(former) == await store.memberships_for(survivor)


async def test_consolidation_replays_and_refuses_inactive_people(store: _Store):
    survivor = "generated-survivor"
    former = "generated-former"
    await store.register(person=_person(survivor), account=_account(
        ACCOUNT_1, person_id=survivor), membership=None)
    await store.register(person=_person(former), account=_account(
        ACCOUNT_2, person_id=former, identifiers=frozenset({ACCT_2})),
        membership=None)
    assert (await store.consolidate(
        survivor=survivor, former=former)).status is OutcomeStatus.COMPLETE
    # Replay is a no-op — the alias already names the survivor.
    assert (await store.consolidate(
        survivor=survivor, former=former)).status is OutcomeStatus.COMPLETE
    # And a suspended survivor can never absorb another person.
    await store.register(person=_person("generated-third"), account=_account(
        str(uuid4()), person_id="generated-third",
        identifiers=frozenset({Identifier("signal", "uuid", "85290000103")})),
        membership=None)
    await store.set_lifecycle(
        person_id=survivor, lifecycle=PersonLifecycle.SUSPENDED)
    assert (await store.consolidate(
        survivor=survivor, former="generated-third"
    )).status is OutcomeStatus.DENIED
    assert await store.person("generated-third") is not None


async def test_drop_membership_support_retracts_only_its_source(store: _Store):
    await store.register(person=_person(), account=_account(ACCOUNT_1), membership=None)
    await store.set_membership(
        person_id=PERSON, scope=ROOM, member=True, source="sync")
    await store.set_membership(
        person_id=PERSON, scope=ROOM, member=True, source="delegation")
    outcome = await store.drop_membership_support(
        person_id=PERSON, scope=ROOM, source="sync")
    assert outcome.status is OutcomeStatus.COMPLETE
    room = {m for m in await store.memberships_for(PERSON) if m.scope == ROOM}
    standing = room.pop()
    assert standing.state is MembershipState.MEMBER
    assert frozenset(standing.sources) == frozenset({"delegation"})
    # The last retraction takes the standing down — a state, not a delete.
    await store.drop_membership_support(
        person_id=PERSON, scope=ROOM, source="delegation")
    room = {m for m in await store.memberships_for(PERSON) if m.scope == ROOM}
    assert room.pop().state is MembershipState.LEFT
