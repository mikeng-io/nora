"""`BindingStore` over SQLAlchemy Core — the managed backend's Postgres
record (`design/managed-identity-access/identity.md`, `management.md`).

Shaped like `nora.infra.sql.reminders`: the engine comes through `__init__`,
every write is one transaction inside `async with self._engine.begin()`,
every read is one statement inside `async with self._engine.connect()`.

THE RULES THIS STORE ENFORCES, NOT DESCRIBES
--------------------------------------------
- **One matchable identifier, one owner.** `managed_identifier`'s primary
  key is (platform, kind, value); a second account claiming it is an
  `IdentifierConflictError` read back from the unique violation — Postgres
  decides the race, so concurrent discovery cannot fork a person.
- **Discovery never merges.** `register` creates an INDEPENDENT person per
  unclaimed account; nothing here groups by name, number or heuristic.
- **Lifecycle facts stay distinct.** A suspended person (`managed_person.
  lifecycle`), a revoked binding (`managed_account.state`) and a left room
  (`managed_membership.state`) are three rows, not one flag.
- **Retired ids keep resolving.** `managed_alias` folds a former person_id
  into the survivor so `canonical_person` answers before any read.
- **The journal is the restart boundary.** `managed_operation` records the
  intended effect and each step's state BEFORE the first store write;
  `incomplete`/`blocked` rows are the reconciler's queue and persist a
  crash. `managed_authority_block` is the local block a failed revocation
  raises — check consults it before trusting a grant.

Revisions: `person.revision` bumps on lifecycle/correlation writes,
`account.revision` on state changes, `membership.revision` on standing
changes — the values `identity`/`ownership`/`authority` observed revisions
compare against when the service revalidates at commitment.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.identity import BoundAccount, Identifier
from nora.domain.managed import (
    AccountState,
    AuthorityBlock,
    IdentifierConflictError,
    ManagedAccount,
    ManagedError,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OperationConflictError,
    OperationJournalState,
    OperationRecord,
    Outcome,
    PersonLifecycle,
    complete,
    denied,
)
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.schema import (
    MANAGED_ACCOUNT,
    MANAGED_ALIAS,
    MANAGED_AUTHORITY_BLOCK,
    MANAGED_IDENTIFIER,
    MANAGED_MEMBERSHIP,
    MANAGED_OPERATION,
    MANAGED_PERSON,
)

# PostgreSQL SQLSTATE 23505, unique_violation — matched EXACTLY, the same
# rule `nora.infra.sql.reminders` applies: a CHECK violation is also an
# IntegrityError and must not be misread as a duplicate.
_PG_UNIQUE_VIOLATION = "23505"


def _person_from(r: Row[Any]) -> ManagedPerson:
    return ManagedPerson(
        person_id=r.person_id,
        lifecycle=PersonLifecycle(r.lifecycle),
        kratos_identity_id=r.kratos_identity_id,
        display_name=r.display_name,
        revision=r.revision,
    )


def _scope_from(kind: str, scope_id: str) -> Scope:
    return Scope(kind=ScopeKind(kind), id=scope_id)


def _membership_from(r: Row[Any]) -> ManagedMembership:
    return ManagedMembership(
        person_id=r.person_id,
        scope=_scope_from(r.scope_kind, r.scope_id),
        state=MembershipState(r.state),
        source=r.source,
        revision=r.revision,
    )


def _operation_from(r: Row[Any]) -> OperationRecord:
    return OperationRecord(
        operation_id=r.operation_id,
        kind=r.kind,
        state=OperationJournalState(r.state),
        steps=MappingProxyType(dict(r.steps or {})),
        outcome=r.outcome,
        detail=r.detail,
        snapshot=MappingProxyType(dict(r.snapshot or {})),
    )


def _block_from(r: Row[Any]) -> AuthorityBlock:
    return AuthorityBlock(
        person_id=r.person_id,
        scope=_scope_from(r.scope_kind, r.scope_id),
        reason=r.reason,
        operation_id=r.operation_id,
    )


class SqlBindingStore:
    """PostgreSQL `BindingStore` — see the module docstring for the rules it
    enforces. `nora.testing.managed.FakeBindingStore` is the second
    implementation; `tests/contract/` is the one suite both pass."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def aclose(self) -> None:
        await self._engine.dispose()

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def _identifiers_of(
        self, conn: Any, account_id: str
    ) -> frozenset[Identifier]:
        rows = (
            await conn.execute(
                sa.select(
                    MANAGED_IDENTIFIER.c.platform,
                    MANAGED_IDENTIFIER.c.kind,
                    MANAGED_IDENTIFIER.c.value,
                ).where(MANAGED_IDENTIFIER.c.account_id == account_id)
            )
        ).fetchall()
        return frozenset(
            Identifier(platform=r.platform, kind=r.kind, value=r.value)
            for r in rows
        )

    async def _account_from_row(
        self, conn: Any, row: Row[Any]
    ) -> ManagedAccount:
        return ManagedAccount(
            account_id=str(row.account_id),
            person_id=row.person_id,
            platform=row.platform,
            identifiers=await self._identifiers_of(conn, str(row.account_id)),
            state=AccountState(row.state),
            label=row.label,
            revision=row.revision,
        )

    async def _account_by_identifier(
        self, conn: Any, platform: str, value: str, kind: str | None
    ) -> ManagedAccount | None:
        conditions = [
            MANAGED_IDENTIFIER.c.platform == platform,
            MANAGED_IDENTIFIER.c.value == value,
            MANAGED_ACCOUNT.c.state == AccountState.BOUND.value,
        ]
        if kind is not None:
            conditions.append(MANAGED_IDENTIFIER.c.kind == kind)
        stmt = (
            sa.select(MANAGED_ACCOUNT)
            .select_from(
                MANAGED_IDENTIFIER.join(
                    MANAGED_ACCOUNT,
                    MANAGED_IDENTIFIER.c.account_id
                    == MANAGED_ACCOUNT.c.account_id,
                )
            )
            .where(*conditions)
            .limit(1)
        )
        row = (await conn.execute(stmt)).fetchone()
        if row is None:
            return None
        return await self._account_from_row(conn, row)

    async def resolve(self, platform: str, value: str) -> BoundAccount | None:
        """The bound account ANY matchable `(platform, value)` identifier
        names — the flat lookup a sender arrives as."""
        async with self._engine.connect() as conn:
            account = await self._account_by_identifier(conn, platform, value, None)
            return None if account is None else account.bound_account()

    async def account_for(
        self, platform: str, kind: str, value: str
    ) -> BoundAccount | None:
        async with self._engine.connect() as conn:
            account = await self._account_by_identifier(
                conn, platform, value, kind
            )
            return None if account is None else account.bound_account()

    async def _canonical(
        self, conn: Any, person_id: str
    ) -> str | None:
        """Fold `person_id` through the alias chain — at most one hop,
        because `add_alias` only ever points at a LIVE person row."""
        exists = await conn.execute(
            sa.select(MANAGED_PERSON.c.person_id).where(
                MANAGED_PERSON.c.person_id == person_id
            )
        )
        if exists.fetchone() is not None:
            return person_id
        alias = await conn.execute(
            sa.select(MANAGED_ALIAS.c.person_id).where(
                MANAGED_ALIAS.c.former_person_id == person_id
            )
        )
        row = alias.fetchone()
        return None if row is None else row.person_id

    async def canonical_person(self, person_id: str) -> str | None:
        async with self._engine.connect() as conn:
            return await self._canonical(conn, person_id)

    async def accounts_for(self, person_id: str) -> tuple[BoundAccount, ...]:
        async with self._engine.connect() as conn:
            canonical = await self._canonical(conn, person_id)
            if canonical is None:
                return ()
            rows = (
                await conn.execute(
                    sa.select(MANAGED_ACCOUNT).where(
                        MANAGED_ACCOUNT.c.person_id == canonical,
                        MANAGED_ACCOUNT.c.state == AccountState.BOUND.value,
                    )
                )
            ).fetchall()
            out = []
            for row in rows:
                out.append((await self._account_from_row(conn, row)).bound_account())
            return tuple(out)

    async def person(self, person_id: str) -> ManagedPerson | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(MANAGED_PERSON).where(
                        MANAGED_PERSON.c.person_id == person_id
                    )
                )
            ).fetchone()
            return None if row is None else _person_from(row)

    async def person_lifecycle(self, person_id: str) -> PersonLifecycle | None:
        async with self._engine.connect() as conn:
            canonical = await self._canonical(conn, person_id)
            if canonical is None:
                return None
            row = (
                await conn.execute(
                    sa.select(MANAGED_PERSON.c.lifecycle).where(
                        MANAGED_PERSON.c.person_id == canonical
                    )
                )
            ).fetchone()
            return None if row is None else PersonLifecycle(row.lifecycle)

    async def account_record(self, account_id: str) -> ManagedAccount | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(MANAGED_ACCOUNT).where(
                        MANAGED_ACCOUNT.c.account_id == account_id
                    )
                )
            ).fetchone()
            return None if row is None else await self._account_from_row(conn, row)

    async def record_for(self, *, account: Identifier) -> ManagedAccount | None:
        """The BOUND binding an exact identifier names, with its revision —
        `account_for`'s record-carrying twin."""
        async with self._engine.connect() as conn:
            return await self._account_by_identifier(
                conn, account.platform, account.value, account.kind
            )

    # ------------------------------------------------------------------
    # registration / correlation
    # ------------------------------------------------------------------

    async def register(
        self, *, person: ManagedPerson, account: ManagedAccount,
        membership: ManagedMembership | None = None,
    ) -> Outcome:
        """The atomic registration — person, account, every identifier and
        the optional onboarding membership in ONE transaction.

        Idempotent: replaying the same effect returns `complete`; an
        identifier already owned by a DIFFERENT account raises
        `IdentifierConflictError` (never a merge), and an identifier owned
        by a REVOKED binding on the same person re-binds it.
        """
        async with self._engine.begin() as conn:
            # 1. The person row — idempotent insert. A replay never touches
            #    an existing person's lifecycle (a suspension is not undone
            #    by re-registration) or its Kratos correlation.
            await conn.execute(
                pg_insert(MANAGED_PERSON)
                .values(
                    person_id=person.person_id,
                    lifecycle=person.lifecycle.value,
                    kratos_identity_id=person.kratos_identity_id,
                    display_name=person.display_name,
                )
                .on_conflict_do_nothing(index_elements=["person_id"])
            )
            # 2. Current ownership of every claimed identifier, in this
            #    transaction so the writes below see the same state.
            owners: dict[Identifier, Row[Any] | None] = {}
            for ident in account.identifiers:
                owners[ident] = (
                    await conn.execute(
                        sa.select(
                            MANAGED_IDENTIFIER.c.account_id,
                            MANAGED_ACCOUNT.c.person_id,
                            MANAGED_ACCOUNT.c.state,
                        )
                        .select_from(
                            MANAGED_IDENTIFIER.join(
                                MANAGED_ACCOUNT,
                                MANAGED_IDENTIFIER.c.account_id
                                == MANAGED_ACCOUNT.c.account_id,
                            )
                        )
                        .where(
                            MANAGED_IDENTIFIER.c.platform == ident.platform,
                            MANAGED_IDENTIFIER.c.kind == ident.kind,
                            MANAGED_IDENTIFIER.c.value == ident.value,
                        )
                    )
                ).fetchone()
            # 3. Ownership rules. An identifier owned by a DIFFERENT person
            #    is a conflict forever — a link/unlink is the only path that
            #    changes ownership, and this is not it. An identifier owned
            #    by a different BOUND account on the same person is a
            #    conflict too: the binding exists, and a second account row
            #    cannot take it without an unlink. A REVOKED binding on the
            #    same person may hand its identifiers to this registration.
            for ident, owner in owners.items():
                if owner is None:
                    continue
                if owner.person_id != person.person_id:
                    raise IdentifierConflictError(
                        f"identifier {ident.platform}:{ident.kind} is owned "
                        "by another person"
                    )
                if (
                    owner.state == AccountState.BOUND.value
                    and str(owner.account_id) != account.account_id
                ):
                    raise IdentifierConflictError(
                        f"identifier {ident.platform}:{ident.kind} is held "
                        "by a different bound account"
                    )
            # 4. Re-bind what a REVOKED binding on this person was holding:
            #    the same account revives, a different account takes over
            #    the identifiers outright.
            revoked_ids = {
                str(o.account_id)
                for o in owners.values()
                if o is not None and o.state == AccountState.REVOKED.value
            }
            if account.account_id in revoked_ids:
                await conn.execute(
                    sa.update(MANAGED_ACCOUNT)
                    .where(
                        MANAGED_ACCOUNT.c.account_id == account.account_id,
                        MANAGED_ACCOUNT.c.state == AccountState.REVOKED.value,
                    )
                    .values(
                        state=AccountState.BOUND.value,
                        revoked_at=None,
                        revision=MANAGED_ACCOUNT.c.revision + 1,
                    )
                )
            elif revoked_ids:
                await conn.execute(
                    sa.update(MANAGED_IDENTIFIER)
                    .where(MANAGED_IDENTIFIER.c.account_id.in_(revoked_ids))
                    .values(account_id=account.account_id)
                )
            # 5. The account row — idempotent by surrogate id.
            try:
                await conn.execute(
                    pg_insert(MANAGED_ACCOUNT)
                    .values(
                        account_id=account.account_id,
                        person_id=account.person_id,
                        platform=account.platform,
                        label=account.label,
                        state=account.state.value,
                    )
                    .on_conflict_do_nothing(index_elements=["account_id"])
                )
                # 6. Identifiers with no existing row. An existing row is
                #    already this account's, or was just re-pointed in
                #    step 4 — inserting it again would falsely conflict.
                #    No conflict-skip: a concurrent claim between the
                #    pre-flight and this insert is a REAL conflict the
                #    caller must see, not a silently dropped row.
                for ident in account.identifiers:
                    if owners[ident] is not None:
                        continue
                    await conn.execute(
                        sa.insert(MANAGED_IDENTIFIER).values(
                            platform=ident.platform,
                            kind=ident.kind,
                            value=ident.value,
                            account_id=account.account_id,
                        )
                    )
            except IntegrityError as exc:
                if _PG_UNIQUE_VIOLATION in str(getattr(exc, "orig", exc)):
                    raise IdentifierConflictError(
                        "an identifier is owned by another account"
                    ) from exc
                raise
            if membership is not None:
                outcome = await self._set_membership(
                    conn,
                    person_id=membership.person_id,
                    scope=membership.scope,
                    member=membership.state is MembershipState.MEMBER,
                    source=membership.source,
                )
                if outcome.detail == "absent-person":
                    raise ManagedError(
                        "the onboarding membership names an absent person"
                    )
        return complete()

    async def correlate_kratos(
        self, *, person_id: str, kratos_identity_id: str
    ) -> Outcome:
        """The person↔Kratos correlation — `denied` when the person is
        absent or already correlates to a DIFFERENT identity, and the
        UNIQUE column makes a second person claiming the same identity a
        constraint violation, not a silent share."""
        async with self._engine.begin() as conn:
            try:
                result = await conn.execute(
                    sa.update(MANAGED_PERSON)
                    .where(
                        MANAGED_PERSON.c.person_id == person_id,
                        sa.or_(
                            MANAGED_PERSON.c.kratos_identity_id.is_(None),
                            MANAGED_PERSON.c.kratos_identity_id
                            == kratos_identity_id,
                        ),
                    )
                    .values(
                        kratos_identity_id=kratos_identity_id,
                        revision=MANAGED_PERSON.c.revision + 1,
                        updated_at=sa.func.now(),
                    )
                )
            except IntegrityError as exc:
                if _PG_UNIQUE_VIOLATION in str(getattr(exc, "orig", exc)):
                    return denied("kratos-id-claimed")
                raise
            if result.rowcount:
                return complete()
            exists = (
                await conn.execute(
                    sa.select(MANAGED_PERSON.c.person_id).where(
                        MANAGED_PERSON.c.person_id == person_id
                    )
                )
            ).fetchone()
            return (
                denied("absent-person")
                if exists is None
                else denied("conflicting-correlation")
            )

    async def set_lifecycle(
        self, *, person_id: str, lifecycle: PersonLifecycle
    ) -> Outcome:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.update(MANAGED_PERSON)
                .where(MANAGED_PERSON.c.person_id == person_id)
                .values(
                    lifecycle=lifecycle.value,
                    revision=MANAGED_PERSON.c.revision + 1,
                    updated_at=sa.func.now(),
                )
            )
            return complete() if result.rowcount else denied("absent-person")

    async def revoke_binding(self, *, account: Identifier) -> Outcome:
        """End one binding — the identifiers stay owned (one owner, ever);
        resolution stops because `state` is no longer `bound`."""
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(MANAGED_IDENTIFIER.c.account_id).where(
                        MANAGED_IDENTIFIER.c.platform == account.platform,
                        MANAGED_IDENTIFIER.c.kind == account.kind,
                        MANAGED_IDENTIFIER.c.value == account.value,
                    )
                )
            ).fetchone()
            if row is None:
                return denied("absent-binding")
            result = await conn.execute(
                sa.update(MANAGED_ACCOUNT)
                .where(
                    MANAGED_ACCOUNT.c.account_id == row.account_id,
                    MANAGED_ACCOUNT.c.state == AccountState.BOUND.value,
                )
                .values(
                    state=AccountState.REVOKED.value,
                    revoked_at=sa.func.now(),
                    revision=MANAGED_ACCOUNT.c.revision + 1,
                )
            )
            return complete() if result.rowcount else denied("already-revoked")

    # ------------------------------------------------------------------
    # membership
    # ------------------------------------------------------------------

    async def _set_membership(
        self, conn: Any, *, person_id: str, scope: Scope,
        member: bool, source: str,
    ) -> Outcome:
        exists = (
            await conn.execute(
                sa.select(MANAGED_PERSON.c.person_id).where(
                    MANAGED_PERSON.c.person_id == person_id
                )
            )
        ).fetchone()
        if exists is None:
            return denied("absent-person")
        state = (
            MembershipState.MEMBER if member else MembershipState.LEFT
        ).value
        await conn.execute(
            pg_insert(MANAGED_MEMBERSHIP)
            .values(
                person_id=person_id,
                scope_kind=scope.kind.value,
                scope_id=scope.id,
                state=state,
                source=source,
            )
            .on_conflict_do_update(
                index_elements=["person_id", "scope_kind", "scope_id"],
                set_={
                    "state": state,
                    "source": source,
                    "revision": MANAGED_MEMBERSHIP.c.revision + 1,
                    "updated_at": sa.func.now(),
                },
            )
        )
        return complete()

    async def set_membership(
        self, *, person_id: str, scope: Scope,
        member: bool, source: str,
    ) -> Outcome:
        async with self._engine.begin() as conn:
            return await self._set_membership(
                conn, person_id=person_id, scope=scope, member=member,
                source=source,
            )

    async def memberships_for(
        self, person_id: str
    ) -> tuple[ManagedMembership, ...]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(MANAGED_MEMBERSHIP).where(
                        MANAGED_MEMBERSHIP.c.person_id == person_id
                    )
                )
            ).fetchall()
            return tuple(_membership_from(r) for r in rows)

    async def members_of(self, scope: Scope) -> tuple[ManagedMembership, ...]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(MANAGED_MEMBERSHIP).where(
                        MANAGED_MEMBERSHIP.c.scope_kind == scope.kind.value,
                        MANAGED_MEMBERSHIP.c.scope_id == scope.id,
                        MANAGED_MEMBERSHIP.c.state == MembershipState.MEMBER.value,
                    )
                )
            ).fetchall()
            return tuple(_membership_from(r) for r in rows)

    async def add_alias(self, *, former_id: str, person_id: str) -> Outcome:
        async with self._engine.begin() as conn:
            live = (
                await conn.execute(
                    sa.select(MANAGED_PERSON.c.person_id).where(
                        MANAGED_PERSON.c.person_id == former_id
                    )
                )
            ).fetchone()
            if live is not None:
                return denied("former-id-is-live")
            existing = (
                await conn.execute(
                    sa.select(MANAGED_ALIAS.c.person_id).where(
                        MANAGED_ALIAS.c.former_person_id == former_id
                    )
                )
            ).fetchone()
            if existing is not None:
                return (
                    complete()
                    if existing.person_id == person_id
                    else denied("alias-conflict")
                )
            target = (
                await conn.execute(
                    sa.select(MANAGED_PERSON.c.person_id).where(
                        MANAGED_PERSON.c.person_id == person_id
                    )
                )
            ).fetchone()
            if target is None:
                return denied("absent-person")
            await conn.execute(
                sa.insert(MANAGED_ALIAS).values(
                    former_person_id=former_id, person_id=person_id
                )
            )
            return complete()

    # ------------------------------------------------------------------
    # the operation journal
    # ------------------------------------------------------------------

    async def record_operation(self, record: OperationRecord) -> Outcome:
        """Journal the intended effect BEFORE the first store write. A
        replay of the same effect is `complete` with the recorded row; a
        DIFFERENT effect under a reused id is a conflict, raised — it must
        never be read as a retry."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                pg_insert(MANAGED_OPERATION)
                .values(
                    operation_id=record.operation_id,
                    kind=record.kind,
                    state=record.state.value,
                    steps=dict(record.steps),
                    snapshot=dict(record.snapshot),
                    outcome=record.outcome,
                    detail=record.detail,
                )
                .on_conflict_do_nothing(index_elements=["operation_id"])
            )
            if result.rowcount:
                return complete()
            existing = (
                await conn.execute(
                    sa.select(MANAGED_OPERATION).where(
                        MANAGED_OPERATION.c.operation_id == record.operation_id
                    )
                )
            ).fetchone()
            if existing is not None and (
                existing.kind == record.kind
                and dict(existing.snapshot) == dict(record.snapshot)
            ):
                return complete()
            raise OperationConflictError(
                f"operation {record.operation_id!r} was recorded with a "
                "different effect"
            )

    async def operation(self, operation_id: str) -> OperationRecord | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(MANAGED_OPERATION).where(
                        MANAGED_OPERATION.c.operation_id == operation_id
                    )
                )
            ).fetchone()
            return None if row is None else _operation_from(row)

    async def update_operation(self, record: OperationRecord) -> Outcome:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.update(MANAGED_OPERATION)
                .where(
                    MANAGED_OPERATION.c.operation_id == record.operation_id
                )
                .values(
                    state=record.state.value,
                    steps=dict(record.steps),
                    outcome=record.outcome,
                    detail=record.detail,
                    updated_at=sa.func.now(),
                )
            )
            return complete() if result.rowcount else denied("absent-operation")

    async def incomplete_operations(self) -> tuple[OperationRecord, ...]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(MANAGED_OPERATION).where(
                        MANAGED_OPERATION.c.state.in_(
                            [
                                OperationJournalState.PENDING.value,
                                OperationJournalState.INCOMPLETE.value,
                                OperationJournalState.BLOCKED.value,
                            ]
                        )
                    )
                )
            ).fetchall()
            return tuple(_operation_from(r) for r in rows)

    # ------------------------------------------------------------------
    # revocation-first authority blocks
    # ------------------------------------------------------------------

    async def block_authority(self, block: AuthorityBlock) -> Outcome:
        async with self._engine.begin() as conn:
            await conn.execute(
                pg_insert(MANAGED_AUTHORITY_BLOCK)
                .values(
                    person_id=block.person_id,
                    scope_kind=block.scope.kind.value,
                    scope_id=block.scope.id,
                    reason=block.reason,
                    operation_id=block.operation_id,
                )
                .on_conflict_do_nothing(
                    index_elements=["person_id", "scope_kind", "scope_id"]
                )
            )
            return complete()

    async def authority_blocks(self) -> tuple[AuthorityBlock, ...]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(sa.select(MANAGED_AUTHORITY_BLOCK))
            ).fetchall()
            return tuple(_block_from(r) for r in rows)

    async def clear_block(self, *, person_id: str, scope: Scope) -> Outcome:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.delete(MANAGED_AUTHORITY_BLOCK).where(
                    MANAGED_AUTHORITY_BLOCK.c.person_id == person_id,
                    MANAGED_AUTHORITY_BLOCK.c.scope_kind == scope.kind.value,
                    MANAGED_AUTHORITY_BLOCK.c.scope_id == scope.id,
                )
            )
            return complete() if result.rowcount else denied("no-block")
