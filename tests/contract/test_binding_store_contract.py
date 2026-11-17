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
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

import pytest

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
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    SourceObservation,
)
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.testing.managed import FakeBindingStore

ACCOUNT_1 = str(uuid4())
ACCOUNT_2 = str(uuid4())
ROOM = Scope(ScopeKind.ROOM, "signal/conv-1")
PERSON = "person_alice"
ACCT = Identifier(platform="signal", kind="uuid", value="85290000101")
ACCT_2 = Identifier(platform="signal", kind="uuid", value="85290000102")
PHONE = Identifier(platform="signal", kind="phone", value="85290000101")


class _Store(Protocol):
    """The surface both implementations offer — the suite type-checks the
    SHAPE, not whichever class the fixture yielded."""

    def mutation(self) -> AbstractAsyncContextManager[None]: ...
    async def identity_held(self, person_id: str) -> bool: ...
    async def hold_identity(self, **kw: Any) -> Any: ...
    async def release_identity_hold(self, **kw: Any) -> Any: ...
    async def register(self, **kw: Any) -> Any: ...
    async def bind_identifiers(self, **kw: Any) -> Any: ...
    async def person(self, person_id: str) -> Any: ...
    async def persons(self) -> tuple[ManagedPerson, ...]: ...
    async def record_for(self, *, account: Identifier) -> Any: ...
    async def account_for(self, platform: str, kind: str, value: str) -> Any: ...
    async def resolve(self, platform: str, value: str) -> Any: ...
    async def accounts_for(self, person_id: str) -> Any: ...
    async def set_lifecycle(self, **kw: Any) -> Any: ...
    async def set_display_name(self, **kw: Any) -> Any: ...
    async def person_lifecycle(self, person_id: str) -> Any: ...
    async def set_membership(self, **kw: Any) -> Any: ...
    async def drop_membership_support(self, **kw: Any) -> Any: ...
    async def consolidate(self, **kw: Any) -> Any: ...
    async def memberships_for(self, person_id: str) -> Any: ...
    async def members_of(self, scope: Scope) -> Any: ...
    async def revoke_binding(self, *, account: Identifier) -> Any: ...
    async def add_alias(self, *, former_id: str, person_id: str) -> Any: ...
    async def canonical_person(self, person_id: str) -> Any: ...
    async def identity_ids(self, person_id: str) -> Any: ...
    async def correlate_kratos(self, **kw: Any) -> Any: ...
    async def record_operation(self, record: OperationRecord) -> Any: ...
    async def operation(self, operation_id: str) -> Any: ...
    async def update_operation(self, record: OperationRecord) -> Any: ...
    async def incomplete_operations(self) -> Any: ...
    async def latest_profile_operation(self, person_id: str) -> Any: ...
    async def block_authority(self, block: AuthorityBlock) -> Any: ...
    async def authority_blocks(self) -> Any: ...
    async def clear_block(self, *, person_id: str, scope: Scope) -> Any: ...
    async def observation_for(
        self, *, service: str, collection: str, scope: Scope
    ) -> Any: ...
    async def record_observation(self, observation: SourceObservation) -> Any: ...
    async def aclose(self) -> None: ...


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any, clean_pg_url: str) -> AsyncIterator[_Store]:
    if request.param == "memory":
        yield FakeBindingStore()
        return
    engine = create_sql_engine(clean_pg_url)
    try:
        yield SqlBindingStore(engine)
    finally:
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


async def test_person_directory_is_callable_active_and_preserves_the_canonical_name(store: _Store):
    assert (await store.register(
        person=replace(_person(), display_name="Generated canonical name"),
        account=_account(ACCOUNT_1), membership=None,
    )).status is OutcomeStatus.COMPLETE
    assert (await store.register(
        person=_person("generated-suspended"),
        account=_account(
            ACCOUNT_2, person_id="generated-suspended", identifiers=frozenset({ACCT_2})
        ),
        membership=None,
    )).status is OutcomeStatus.COMPLETE
    assert (await store.set_lifecycle(
        person_id="generated-suspended", lifecycle=PersonLifecycle.SUSPENDED,
    )).status is OutcomeStatus.COMPLETE
    people = await store.persons()
    assert [(person.person_id, person.display_name) for person in people] == [
        (PERSON, "Generated canonical name")
    ]


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
    assert await store.person("person_other") is None
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


async def test_display_name_projects_descriptively_without_a_revision(store: _Store):
    """`set_display_name` is the projection write that follows the
    authoritative Kratos patch — descriptive, so the person revision a
    session binding observed does NOT move."""
    await store.register(
        person=replace(_person(), display_name="Generated Original"),
        account=_account(ACCOUNT_1), membership=None)
    before = await store.person(PERSON)
    outcome = await store.set_display_name(
        person_id=PERSON, display_name="Generated New")
    assert outcome.status is OutcomeStatus.COMPLETE
    after = await store.person(PERSON)
    assert after is not None and after.display_name == "Generated New"
    assert before is not None and after.revision == before.revision
    # A same-effect replay is a no-op; an absent person refuses.
    assert (await store.set_display_name(
        person_id=PERSON, display_name="Generated New")).status is OutcomeStatus.COMPLETE
    assert (await store.set_display_name(
        person_id="generated-absent", display_name="Generated")).status is OutcomeStatus.DENIED


async def test_latest_profile_operation_is_the_newest_journaled_intent(store: _Store):
    """`update_profile` replays consult the journal's order: the newest
    recorded intent owns the name — earlier records, another person's ops
    and other kinds never supersede."""
    def _record(
        op_id: str, *, kind: str = "update_profile", person: str = PERSON
    ) -> OperationRecord:
        return OperationRecord(
            operation_id=op_id, kind=kind,
            state=OperationJournalState.PENDING,
            steps=MappingProxyType({"kratos": "pending"}),
            snapshot=MappingProxyType(
                {"target_person": person, "display_name": "Generated"}))

    assert await store.latest_profile_operation(PERSON) is None
    await store.record_operation(_record("generated-op-1"))
    await store.record_operation(_record("generated-op-2", person="person_other"))
    await store.record_operation(_record("generated-op-3", kind="publish_link"))
    latest = await store.latest_profile_operation(PERSON)
    assert latest is not None and latest.operation_id == "generated-op-1"
    # A completed newer op still supersedes — any journal state counts.
    await store.record_operation(_record("generated-op-4"))
    record = await store.operation("generated-op-4")
    assert record is not None
    await store.update_operation(
        replace(record, state=OperationJournalState.COMPLETE))
    latest = await store.latest_profile_operation(PERSON)
    assert latest is not None and latest.operation_id == "generated-op-4"

async def test_bind_identifiers_enriches_one_account_atomically(store: _Store):
    """The grouped-observation write: new identifiers join the ONE account
    the group already names — same owner, one revision, nothing else."""
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    before = await store.record_for(account=ACCT)
    outcome = await store.bind_identifiers(
        account_id=ACCOUNT_1, identifiers=frozenset({ACCT, PHONE})
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    for ident in (ACCT, PHONE):
        bound = await store.record_for(account=ident)
        assert bound is not None and bound.account_id == ACCOUNT_1
        assert bound.person_id == PERSON
    after = await store.record_for(account=ACCT)
    assert before is not None and after.revision > before.revision
    # Enrichment is not registration — no second person, no second account.
    assert len(await store.accounts_for(PERSON)) == 1


async def test_bind_identifiers_replays_without_new_writes(store: _Store):
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    for _ in range(2):
        outcome = await store.bind_identifiers(
            account_id=ACCOUNT_1, identifiers=frozenset({ACCT, PHONE})
        )
        assert outcome.status is OutcomeStatus.COMPLETE
    bound = await store.record_for(account=ACCT)
    assert bound is not None
    assert bound.identifiers == frozenset({ACCT, PHONE})
    revision = bound.revision
    # already-owned identifiers are a no-op — no second revision
    outcome = await store.bind_identifiers(
        account_id=ACCOUNT_1, identifiers=frozenset({PHONE})
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    settled = await store.record_for(account=ACCT)
    assert settled is not None and settled.revision == revision


async def test_bind_identifiers_never_claims_another_accounts_identifier(
    store: _Store,
):
    """A pair spanning two accounts is refused atomically — the group's
    first identifier is not widened and the second is not touched."""
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    await store.register(
        person=_person("person_other"),
        account=_account(
            ACCOUNT_2, person_id="person_other",
            identifiers=frozenset({PHONE}),
        ),
        membership=None,
    )
    with pytest.raises(IdentifierConflictError):
        await store.bind_identifiers(
            account_id=ACCOUNT_1, identifiers=frozenset({ACCT, PHONE})
        )
    phone = await store.record_for(account=PHONE)
    assert phone is not None and phone.account_id == ACCOUNT_2
    acct = await store.record_for(account=ACCT)
    assert acct is not None and acct.identifiers == frozenset({ACCT})


async def test_bind_identifiers_keeps_revoked_ownership_reserved(store: _Store):
    """Revocation keeps the reservation: a retired identifier never
    re-points, and a revoked account cannot be enriched."""
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    await store.revoke_binding(account=ACCT)
    await store.register(
        person=_person("person_other"),
        account=_account(
            ACCOUNT_2, person_id="person_other",
            identifiers=frozenset({PHONE}),
        ),
        membership=None,
    )
    with pytest.raises(IdentifierConflictError):
        await store.bind_identifiers(
            account_id=ACCOUNT_2, identifiers=frozenset({PHONE, ACCT})
        )
    assert await store.record_for(account=ACCT) is None
    phone = await store.record_for(account=PHONE)
    assert phone is not None and phone.identifiers == frozenset({PHONE})
    outcome = await store.bind_identifiers(
        account_id=ACCOUNT_1, identifiers=frozenset({ACCT})
    )
    assert outcome.status is OutcomeStatus.DENIED


async def test_concurrent_identifier_claims_have_exactly_one_winner(store: _Store):
    """Two accounts racing for the same new identifier — the store's
    unique ownership, not a pre-read, is the arbiter."""
    await store.register(
        person=_person(), account=_account(ACCOUNT_1), membership=None
    )
    await store.register(
        person=_person("person_other"),
        account=_account(
            ACCOUNT_2, person_id="person_other",
            identifiers=frozenset({ACCT_2}),
        ),
        membership=None,
    )
    results = await asyncio.gather(
        store.bind_identifiers(
            account_id=ACCOUNT_1, identifiers=frozenset({ACCT, PHONE})
        ),
        store.bind_identifiers(
            account_id=ACCOUNT_2, identifiers=frozenset({ACCT_2, PHONE})
        ),
        return_exceptions=True,
    )
    completes = [
        r for r in results
        if isinstance(r, Outcome) and r.status is OutcomeStatus.COMPLETE
    ]
    conflicts = [r for r in results if isinstance(r, IdentifierConflictError)]
    assert len(completes) == 1 and len(conflicts) == 1
    owner = await store.record_for(account=PHONE)
    assert owner is not None and owner.account_id in {ACCOUNT_1, ACCOUNT_2}
    loser = next(a for a in (ACCOUNT_1, ACCOUNT_2) if a != owner.account_id)
    losing_ident = ACCT if loser == ACCOUNT_1 else ACCT_2
    record = await store.record_for(account=losing_ident)
    assert record is not None and record.identifiers == frozenset({losing_ident})


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
    assert len(await store.members_of(ROOM)) == 1
    await store.set_membership(
        person_id=PERSON, scope=ROOM, member=False, source="delegation"
    )
    assert await store.members_of(ROOM) == ()
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
    assert await store.identity_ids(survivor) is None
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
    assert await store.identity_ids(survivor) == (survivor,)
    await store.add_alias(former_id="generated-earlier", person_id=former)
    survivor_revision = (await store.person(survivor)).revision
    moved_revision = (await store.record_for(account=ACCT_2)).revision

    outcome = await store.consolidate(survivor=survivor, former=former)
    assert outcome.status is OutcomeStatus.COMPLETE

    # The retired id canonicalizes: person() resolves it to the survivor.
    assert (await store.person(former)).person_id == survivor
    assert await store.canonical_person(former) == survivor
    family = (survivor, "generated-earlier", former)
    for identity in family:
        assert await store.identity_ids(identity) == family
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


def _observation(
    observed_ms: int, *, service: str = "generated-bridge",
    collection: str = "generated-guild", scope: Scope = ROOM,
    operation_id: str = "generated-op", effect: str = "generated-effect",
) -> SourceObservation:
    return SourceObservation(
        service=service, collection=collection, scope=scope,
        observed_ms=observed_ms, operation_id=operation_id, effect=effect)


async def test_an_observation_mark_round_trips_per_source(store: _Store):
    """Absent before the first write; the newest accepted instant answers
    back with its operation and effect identity intact."""
    assert await store.observation_for(
        service="generated-bridge", collection="generated-guild",
        scope=ROOM) is None
    outcome = await store.record_observation(_observation(1_700_000_000_000))
    assert outcome.status is OutcomeStatus.COMPLETE
    mark = await store.observation_for(
        service="generated-bridge", collection="generated-guild",
        scope=ROOM)
    assert mark == _observation(1_700_000_000_000)


async def test_observation_marks_are_isolated_by_service_collection_scope(
    store: _Store,
):
    """Each exact (service, collection, scope kind, scope id) keeps its own
    mark — nothing bleeds across a neighbour's ordering."""
    other_room = Scope(ScopeKind.ROOM, "signal/conv-2")
    unit = Scope(ScopeKind.UNIT, "unit-a")
    await store.record_observation(_observation(2_000))
    await store.record_observation(_observation(
        1_000, collection="generated-guild-two"))
    await store.record_observation(_observation(
        3_000, service="generated-other"))
    await store.record_observation(_observation(4_000, scope=other_room))
    await store.record_observation(_observation(5_000, scope=unit))
    marks = {
        key: (await store.observation_for(
            service=key[0], collection=key[1], scope=key[2])).observed_ms
        for key in (
            ("generated-bridge", "generated-guild", ROOM),
            ("generated-bridge", "generated-guild-two", ROOM),
            ("generated-other", "generated-guild", ROOM),
            ("generated-bridge", "generated-guild", other_room),
            ("generated-bridge", "generated-guild", unit),
        )
    }
    assert marks == {
        ("generated-bridge", "generated-guild", ROOM): 2_000,
        ("generated-bridge", "generated-guild-two", ROOM): 1_000,
        ("generated-other", "generated-guild", ROOM): 3_000,
        ("generated-bridge", "generated-guild", other_room): 4_000,
        ("generated-bridge", "generated-guild", unit): 5_000,
    }


async def test_recording_an_observation_replaces_the_mark_whole(store: _Store):
    """The port is an upsert — admission ordering lives at the boundary,
    so the newest write wins whole: instant, operation AND effect."""
    await store.record_observation(_observation(1_000, effect="first"))
    await store.record_observation(_observation(
        2_000, operation_id="generated-op-2", effect="second"))
    mark = await store.observation_for(
        service="generated-bridge", collection="generated-guild",
        scope=ROOM)
    assert mark == _observation(
        2_000, operation_id="generated-op-2", effect="second")
