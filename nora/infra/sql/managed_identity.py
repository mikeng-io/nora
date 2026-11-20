"""SQL BindingStore with individually committed reads/writes.

Ownership, lifecycle, aliases and recovery contracts are declared in
`design/managed-identity-access/` and the shared binding-store contract suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    SourceObservation,
    complete,
    denied,
)
from nora.domain.management import Scope
from nora.infra.sql import managed_observations
from nora.infra.sql import managed_scoped_names as scoped_names
from nora.infra.sql.identity_views import stored_identity_family
from nora.infra.sql.managed_publication import (
    consolidate_people,
    drop_membership_support,
    retract_membership_support,
    supports_for,
)
from nora.infra.sql.managed_registration import (
    _PG_UNIQUE_VIOLATION,
    _bind_account,
    _canonical_person,
    _insert_person,
    bind_identifiers,
)
from nora.infra.sql.managed_rows import (
    _block_from,
    _membership_from,
    _operation_from,
    _person_from,
)
from nora.infra.sql.schema_managed import (
    MANAGED_ACCOUNT,
    MANAGED_ALIAS,
    MANAGED_AUTHORITY_BLOCK,
    MANAGED_IDENTIFIER,
    MANAGED_IDENTITY_HOLD,
    MANAGED_MEMBERSHIP,
    MANAGED_MEMBERSHIP_SUPPORT,
    MANAGED_OPERATION,
    MANAGED_PERSON,
)

MANAGED_MUTATION_LOCK = "nora-managed-identity-writer"


class SqlBindingStore:
    """PostgreSQL `BindingStore` — see the module docstring for the rules it
    enforces. `nora.testing.managed.FakeBindingStore` is the second
    implementation; `tests/contract/` is the one suite both pass."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._mutation_lock = asyncio.Lock()

    @asynccontextmanager
    async def mutation(self) -> AsyncIterator[None]:
        # ponytail: one store per engine; queue locally so lock waiters cannot exhaust its pool.
        async with self._mutation_lock, self._engine.begin() as connection:
            await connection.execute(sa.select(sa.func.pg_advisory_xact_lock(
                sa.func.hashtextextended(MANAGED_MUTATION_LOCK, 0),
            )))
            yield

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
        """Fold `person_id` through the alias chain — one hop (aliases point at LIVE rows)."""
        return await _canonical_person(conn, person_id)

    async def canonical_person(self, person_id: str) -> str | None:
        async with self._engine.connect() as conn:
            return await self._canonical(conn, person_id)

    async def identity_ids(self, person_id: str) -> tuple[str, ...] | None:
        return await stored_identity_family(self._engine, person_id)

    async def former_persons(self, person_id: str) -> tuple[str, ...]:
        return (await self.identity_ids(person_id) or ())[1:]

    async def persons(self) -> tuple[ManagedPerson, ...]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(MANAGED_PERSON)
                    .where(
                        MANAGED_PERSON.c.lifecycle == PersonLifecycle.ACTIVE.value
                    )
                    .order_by(MANAGED_PERSON.c.person_id)
                )
            ).fetchall()
            return tuple(_person_from(r) for r in rows)

    async def identity_held(self, person_id: str) -> bool:
        async with self._engine.connect() as connection:
            canonical = await self._canonical(connection, person_id)
            return bool(await connection.scalar(sa.select(sa.exists().where(
                MANAGED_IDENTITY_HOLD.c.person_id.in_({person_id, canonical} - {None}),
            ))))

    async def hold_identity(self, *, person_id: str, operation_id: str) -> Outcome:
        if not operation_id.strip():
            raise ManagedError("an identity hold requires an owning operation")
        async with self._engine.begin() as connection:
            canonical = await self._canonical(connection, person_id)
            if canonical is None:
                return denied("unknown-person")
            await connection.execute(pg_insert(MANAGED_IDENTITY_HOLD).values(
                person_id=canonical, operation_id=operation_id,
            ).on_conflict_do_nothing())
            return complete()

    async def release_identity_hold(self, *, person_id: str, operation_id: str) -> Outcome:
        if not operation_id.strip():
            raise ManagedError("an identity hold requires an owning operation")
        async with self._engine.begin() as connection:
            canonical = await self._canonical(connection, person_id)
            await connection.execute(sa.delete(MANAGED_IDENTITY_HOLD).where(
                MANAGED_IDENTITY_HOLD.c.person_id.in_({person_id, canonical} - {None}),
                MANAGED_IDENTITY_HOLD.c.operation_id == operation_id,
            ))
            return complete()

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
            canonical = await self._canonical(conn, person_id)
            if canonical is None:
                return None
            row = (
                await conn.execute(
                    sa.select(MANAGED_PERSON).where(
                        MANAGED_PERSON.c.person_id == canonical
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
        """Atomically register person, account, identifiers and optional membership.

        Exact replay is idempotent; foreign ownership conflicts; own revoked bindings rebind.
        """
        async with self._engine.begin() as conn:
            await _insert_person(conn, person)
            await _bind_account(conn, person=person, account=account)
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

    async def bind_identifiers(
        self, *, account_id: str, identifiers: frozenset[Identifier]
    ) -> Outcome:
        return await bind_identifiers(self._engine, account_id=account_id, identifiers=identifiers)

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

    async def set_display_name(
        self, *, person_id: str, display_name: str
    ) -> Outcome:
        """The projected display name — written only after the
        authoritative profile mutation. Descriptive, not identity
        evidence, so the person revision does NOT bump and sessions keep
        their binding; idempotent on replay."""
        async with self._engine.begin() as conn:
            canonical = await self._canonical(conn, person_id)
            if canonical is None:
                return denied("absent-person")
            result = await conn.execute(
                sa.update(MANAGED_PERSON)
                .where(MANAGED_PERSON.c.person_id == canonical)
                .values(display_name=display_name, updated_at=sa.func.now())
            )
            return complete() if result.rowcount else denied("absent-person")

    async def set_scoped_name(
        self, *, person_id: str, scope: Scope, nickname: str,
        operation_id: str
    ) -> Outcome:
        """The (person, scope) nickname row — `operation_id` its journal
        provenance; descriptive metadata, so no revision bumps."""
        async with self._engine.begin() as conn:
            return await scoped_names.set_scoped_name(
                conn, person_id=person_id, scope=scope, nickname=nickname,
                operation_id=operation_id)

    async def scoped_name_for(
        self, *, person_id: str, scope: Scope
    ) -> str | None:
        async with self._engine.connect() as conn:
            return await scoped_names.scoped_name_for(
                conn, person_id=person_id, scope=scope)

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
        # member=True adds one support; member=False ends the standing —
        # every support is cleared, since an operator removal is never a
        # single-source correction (that verb is drop_membership_support).
        if member:
            await conn.execute(
                pg_insert(MANAGED_MEMBERSHIP_SUPPORT)
                .values(
                    person_id=person_id,
                    scope_kind=scope.kind.value,
                    scope_id=scope.id,
                    source=source,
                )
                .on_conflict_do_nothing()
            )
        else:
            await drop_membership_support(conn, person_id=person_id, scope=scope)
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

    async def drop_membership_support(
        self, *, person_id: str, scope: Scope, source: str
    ) -> Outcome:
        async with self._engine.begin() as conn:
            exists = (
                await conn.execute(
                    sa.select(MANAGED_PERSON.c.person_id).where(
                        MANAGED_PERSON.c.person_id == person_id
                    )
                )
            ).fetchone()
            if exists is None:
                return denied("absent-person")
            await retract_membership_support(
                conn, person_id=person_id, scope=scope, source=source)
            return complete()

    async def memberships_for(
        self, person_id: str
    ) -> tuple[ManagedMembership, ...]:
        async with self._engine.connect() as conn:
            canonical = await self._canonical(conn, person_id)
            if canonical is None:
                return ()
            rows = (
                await conn.execute(
                    sa.select(MANAGED_MEMBERSHIP).where(
                        MANAGED_MEMBERSHIP.c.person_id == canonical
                    )
                )
            ).fetchall()
            supports = await supports_for(conn, rows)
            return tuple(
                _membership_from(
                    r, supports.get(
                        (r.person_id, r.scope_kind, r.scope_id), frozenset()))
                for r in rows
            )

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
            supports = await supports_for(conn, rows)
            return tuple(
                _membership_from(
                    r, supports.get(
                        (r.person_id, r.scope_kind, r.scope_id), frozenset()))
                for r in rows
            )

    async def consolidate(self, *, survivor: str, former: str) -> Outcome:
        async with self._engine.begin() as conn:
            return await consolidate_people(conn, survivor=survivor, former=former)

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

    async def latest_profile_operation(
        self, person_id: str, *, scope: Scope | None = None
    ) -> OperationRecord | None:
        where = [
            MANAGED_OPERATION.c.kind == "update_profile",
            MANAGED_OPERATION.c.snapshot["target_person"].astext == person_id,
        ]
        if scope is None:
            # The holder's global edit carries no profile_scope — a scoped
            # nickname never orders against it.
            where.append(
                ~MANAGED_OPERATION.c.snapshot.has_key("profile_scope"))
        else:
            where += [
                MANAGED_OPERATION.c.snapshot["profile_scope"]["kind"].astext
                == scope.kind.value,
                MANAGED_OPERATION.c.snapshot["profile_scope"]["id"].astext
                == scope.id,
            ]
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(MANAGED_OPERATION)
                    .where(*where)
                    .order_by(
                        MANAGED_OPERATION.c.created_at.desc(),
                        MANAGED_OPERATION.c.operation_id.desc(),
                    )
                    .limit(1)
                )
            ).fetchone()
            return None if row is None else _operation_from(row)

    # per-source observation ordering: `managed_observations`

    async def observation_for(
            self, *, service: str, collection: str,
            scope: Scope) -> SourceObservation | None:
        return await managed_observations.observation_for(
            self._engine, service=service, collection=collection, scope=scope)

    async def record_observation(self, observation: SourceObservation) -> Outcome:
        return await managed_observations.record_observation(self._engine, observation)

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
