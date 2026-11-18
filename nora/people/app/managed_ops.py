"""The managed operations' journal plumbing — snapshots, replay and the
per-step bookkeeping both services share (`design/managed-identity-access/`).

Every mutation journals its intended effect before applying it; these
helpers build the snapshot an `OperationRecord` carries, re-read it after a
restart, and mark the steps a replay resumes. Nothing here revalidates an
actor — authority was proved at first commitment, and the journaled effect
is the warrant for finishing it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from nora.domain.identity import Identifier
from nora.domain.managed import (
    CheckResult,
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    check_unavailable,
    denied,
    incomplete,
    unavailable,
)
from nora.domain.managed_ports import (
    AuthorityBackend,
    BindingStore,
    TopologySource,
)
from nora.domain.management import (
    AccessChange,
    AccessDirection,
    AccountObservation,
    AccountRegistration,
    Delegation,
    MembershipStanding,
    Operation,
    ProfileEdit,
    RosterSync,
    Scope,
    ScopeKind,
    Unlink,
)

#: A journaled store effect, produced LAZILY: a step replayed as already
#: done never builds the call it skips, so no coroutine is ever created
#: un-awaited.
Effect = Callable[[], Awaitable[Outcome]]
Effects = Callable[[], Iterable[Awaitable[Outcome]]]


async def _publication_refusal(
    store: BindingStore, *, people: Iterable[str | None], payload: object,
    account: Identifier | None = None, scope: Scope | None = None,
) -> Outcome | None:
    subjects = {person for person in people if person is not None}
    accounts = set() if account is None else {account}
    if isinstance(payload, AccountRegistration):
        subjects.add(payload.person_id)
    elif isinstance(payload, AccessChange):
        subjects.add(payload.grantee)
    elif isinstance(payload, Delegation):
        subjects.add(payload.delegatee)
    elif isinstance(payload, Unlink):
        subjects.add(payload.expected_person)
    elif isinstance(payload, AccountObservation):
        # The group's claim: every identifier that already resolves names
        # a person the observation could enrich — each must be hold-free.
        accounts.update(payload.identifiers)
    elif isinstance(payload, RosterSync):
        accounts.update(payload.members)
        if payload.complete and scope is not None:
            subjects.update(member.person_id for member in await store.members_of(scope))
    for identifier in accounts:
        bound = await store.account_for(identifier.platform, identifier.kind, identifier.value)
        if bound is not None:
            subjects.add(bound.person_id)
    for person in subjects:
        if await store.identity_held(person) is not False:
            return unavailable("identity-publication-pending")
    return None

def _scope_snapshot(scope: Scope) -> dict[str, str]:
    return {"kind": scope.kind.value, "id": scope.id}


def _scope_from(snapshot: dict[str, str]) -> Scope:
    return Scope(ScopeKind(snapshot["kind"]), snapshot["id"])


def _ident_snapshot(ident: Identifier) -> dict[str, str]:
    return {"platform": ident.platform, "kind": ident.kind, "value": ident.value}


def _ident_from(snapshot: dict[str, str]) -> Identifier:
    return Identifier(
        platform=snapshot["platform"], kind=snapshot["kind"], value=snapshot["value"]
    )


def _op_snapshot(op: Operation) -> MappingProxyType[str, Any]:
    """The journaled effect — enough to rebuild the write side (the actor is never journaled)."""
    payload = op.payload
    snap: dict[str, Any] = {"kind": op.kind}
    if isinstance(payload, AccountObservation):
        snap["identifiers"] = [_ident_snapshot(i) for i in sorted(
            payload.identifiers, key=lambda i: (i.platform, i.kind, i.value))]
    elif isinstance(payload, AccountRegistration):
        snap["person_id"] = payload.person_id
        snap["scope"] = _scope_snapshot(payload.scope)
        snap["identifiers"] = [_ident_snapshot(i) for i in sorted(
            payload.identifiers, key=lambda i: (i.platform, i.kind, i.value))]
    elif isinstance(payload, RosterSync):
        snap["members"] = [_ident_snapshot(i) for i in sorted(
            payload.members, key=lambda i: (i.platform, i.kind, i.value))]
        snap["complete"] = payload.complete
        snap["collection"] = payload.collection
        snap["observed_ms"] = payload.observed_ms
        snap["service"] = op.actor.service_id
        snap["sync_source"] = "sync:" + json.dumps(
            [op.actor.service_id, payload.collection], separators=(",", ":"))
    elif isinstance(payload, MembershipStanding):
        snap["member"] = payload.member
    elif isinstance(payload, AccessChange):
        snap["direction"] = payload.direction.value
        snap["grantee"] = payload.grantee
        snap["permits"] = sorted(payload.permits)
    elif isinstance(payload, Delegation):
        snap["delegatee"] = payload.delegatee
        snap["permits"] = sorted(payload.permits)
    elif isinstance(payload, Unlink):
        snap["expected_person"] = payload.expected_person
    elif isinstance(payload, ProfileEdit):
        if payload.display_name is not None:
            snap["display_name"] = payload.display_name
        if payload.scope is not None:
            snap["profile_scope"] = _scope_snapshot(payload.scope)
            snap["nickname"] = payload.nickname
    if op.target.account is not None:
        snap["account"] = _ident_snapshot(op.target.account)
    if op.target.person_id is not None:
        snap["target_person"] = op.target.person_id
    if op.target.scope is not None:
        snap["target_scope"] = _scope_snapshot(op.target.scope)
    return MappingProxyType(snap)


async def _record(
    store: BindingStore, op: Operation, steps: tuple[str, ...]
) -> OperationRecord | Outcome:
    """Journal the intended effect — an `Outcome` means it refused or could not answer."""
    record = OperationRecord(
        operation_id=op.operation_id,
        kind=op.kind,
        state=OperationJournalState.PENDING,
        steps=MappingProxyType(dict.fromkeys(steps, "pending")),
        snapshot=_op_snapshot(op),
    )
    outcome = await store.record_operation(record)
    return record if outcome.status is OutcomeStatus.COMPLETE else outcome


async def _step_done(store: BindingStore, record: OperationRecord, step: str) -> None:
    steps = dict(record.steps)
    steps[step] = "done"
    await store.update_operation(
        OperationRecord(
            operation_id=record.operation_id,
            kind=record.kind,
            state=OperationJournalState.INCOMPLETE,
            steps=MappingProxyType(steps),
            snapshot=record.snapshot,
        )
    )
    object.__setattr__(record, "steps", MappingProxyType(steps))


async def _finish(
    store: BindingStore,
    record: OperationRecord,
    state: OperationJournalState,
    outcome: str,
    detail: str = "",
) -> None:
    await store.update_operation(
        OperationRecord(
            operation_id=record.operation_id,
            kind=record.kind,
            state=state,
            steps=record.steps,
            outcome=outcome,
            detail=detail,
            snapshot=record.snapshot,
        )
    )


def _pending(record: OperationRecord, step: str) -> bool:
    return record.steps.get(step) != "done"


async def _fail(
    store: BindingStore, record: OperationRecord, outcome: Outcome
) -> Outcome:
    """Report journaled partial writes and retain their unfinished effects."""
    await _finish(store, record, OperationJournalState.INCOMPLETE,
                  outcome.status.value, outcome.detail)
    if any(done == "done" for done in record.steps.values()):
        return incomplete(outcome.detail)
    return outcome


async def _journal_after_failure(
    store: BindingStore, op: Operation
) -> Outcome | None:
    """Read the exact recorded effect after failure, or retain uncertainty."""
    try:
        record = await store.operation(op.operation_id)
    except Exception:  # noqa: BLE001 — an unreadable journal is a clean outage
        return None
    if record is None:
        return None
    if record.kind != op.kind or record.snapshot != _op_snapshot(op):
        return denied("operation-conflict")
    if record.state is OperationJournalState.COMPLETE:
        return Outcome(OutcomeStatus(record.outcome or "complete"),
                       record.detail or "replayed")
    if any(done == "done" for done in record.steps.values()):
        return incomplete("store-failure")
    return None


async def _recorded_replay(store: BindingStore, op: Operation) -> Outcome | None:
    """A journaled op id replays its recorded state; a different effect conflicts."""
    recorded = await store.operation(op.operation_id)
    if recorded is None:
        return None
    if recorded.kind != op.kind or recorded.snapshot != _op_snapshot(op):
        return denied("operation-conflict")
    if recorded.state is OperationJournalState.COMPLETE:
        return Outcome(
            OutcomeStatus(recorded.outcome or "complete"),
            recorded.detail or "replayed")
    return incomplete("replayed: still incomplete")


async def _revisions_fresh(store: BindingStore, op: Operation) -> Outcome | None:
    """Revalidate the revisions the submitter observed, at commitment, before ANY effect."""
    for name, observed in op.observed_revisions.items():
        if name == "identity":
            person_id = op.target.person_id or op.actor.person_id
            if person_id is None:
                continue
            person = await store.person(person_id)
            if person is None:
                return denied("unknown-person")
            if str(person.revision) != observed:
                return denied("stale-identity-revision")
        elif name == "ownership" and op.target.account is not None:
            record = await store.record_for(account=op.target.account)
            if record is not None and str(record.revision) != observed:
                return denied("stale-ownership-revision")
            # No binding yet: nothing to be stale about — discovery's own
            # conflict path decides.
        # "authority": unversioned — read-back is the write-path proof.
    return None


def _digest_lines(lines: Iterable[str]) -> str:
    """One canonical digest over a fact set — order-free, exact."""
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def _effect_digest(snapshot: MappingProxyType[str, Any]) -> str:
    """The journaled effect's fingerprint — a same-instant replay must match it."""
    return hashlib.sha256(json.dumps(
        dict(snapshot), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _scope_authority(
    backend: AuthorityBackend, scope: Scope
) -> CheckResult | str:
    """The digest an `authority` observed-revision compares against for one scope."""
    rows = await backend.relations_on(scope=scope)
    if isinstance(rows, CheckResult):
        return check_unavailable(rows.detail)
    return _digest_lines(
        f"{scope.kind.value}:{scope.id}:{relation}:{person}"
        for relation, person in rows
    )


async def _person_authority(
    store: BindingStore,
    backend: AuthorityBackend,
    topology: TopologySource,
    persons: frozenset[str],
) -> CheckResult | str:
    """The digest an `authority` observed-revision compares against for a person set."""
    try:
        scopes = await topology.scopes()
    except Exception:  # noqa: BLE001 — a broken topology adapter means unavailable, never a digest
        return check_unavailable("topology-unavailable")
    lines: list[str] = []
    for scope in scopes:
        rows = await backend.relations_on(scope=scope)
        if isinstance(rows, CheckResult):
            return check_unavailable(rows.detail)
        lines += [
            f"{scope.kind.value}:{scope.id}:{relation}:{subject}"
            for relation, subject in rows
            if subject in persons
        ]
    for person in persons:
        for membership in await store.memberships_for(person):
            lines.append(
                f"membership:{membership.scope.kind.value}:{membership.scope.id}:"
                f"{membership.state.value}:{','.join(sorted(membership.sources))}"
            )
    for block in await store.authority_blocks():
        if block.person_id in persons:
            lines.append(
                f"block:{block.person_id}:{block.scope.kind.value}:"
                f"{block.scope.id}:{block.reason}"
            )
    return _digest_lines(lines)


@dataclass(frozen=True)
class _ReplayOp:
    """The write side of a journaled operation, rebuilt from the journal
    snapshot — NOT a fresh `Operation`: no actor (authority was validated at
    first commitment; the journaled effect is the warrant for finishing it),
    and no re-admission, so replay cannot be refused by a check the original
    commitment already passed."""

    operation_id: str
    kind: str
    person_id: str | None
    scope: Scope
    payload: object
    sync_source: str = "sync"
    service: str = ""


def _rebuild(record: OperationRecord) -> _ReplayOp | None:
    """Reconstruct the effect from the journal snapshot — `None` when it names no known kind."""
    snap = dict(record.snapshot)
    kind = snap.get("kind")
    if "target_scope" not in snap:
        return None
    try:
        scope = _scope_from(snap["target_scope"])
        payload: object
        if kind == "set_membership":
            payload = MembershipStanding(member=bool(snap["member"]))
        elif kind in ("grant_access", "revoke_access"):
            payload = AccessChange(
                direction=AccessDirection(snap["direction"]),
                grantee=snap["grantee"],
                permits=frozenset(snap["permits"]),
            )
        elif kind in ("delegate", "revoke_delegation"):
            payload = Delegation(
                delegatee=snap["delegatee"],
                permits=frozenset(snap["permits"]),
            )
        elif kind == "sync_membership":
            payload = RosterSync(
                members=frozenset(
                    _ident_from(i) for i in snap.get("members", [])
                ),
                complete=bool(snap["complete"]),
                collection=snap.get("collection", ""),
                observed_ms=int(snap.get("observed_ms", 0)),
            )
        else:
            return None
        return _ReplayOp(
            operation_id=record.operation_id,
            kind=str(kind),
            person_id=snap.get("target_person"),
            scope=scope,
            payload=payload,
            sync_source=snap.get("sync_source", "sync"),
            service=str(snap.get("service") or ""),
        )
    except (KeyError, ValueError, TypeError):
        return None

