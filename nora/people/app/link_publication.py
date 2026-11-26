"""Publish an approved bilateral account link — the central
consolidation executor of `docs/design/managed-identity-access/`.

`observe` produces the authoritative snapshot a `PendingLink` records as
its owners/revisions, so the approval binds REAL observed state.
`commit` turns a fully-approved durable request into published ownership
under the shared mutation gate:

    revalidate -> journal -> holds -> begin_commit -> consolidate ->
    retire the former Kratos identity + sessions -> normalize Keto ->
    read-back -> release only this operation's holds -> COMMITTED.

Every step is journaled; a crash leaves an `incomplete` operation with
the holds standing, so access stays `unavailable` until `reconcile`
re-runs the recorded steps and read-back proves all stores agree.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from nora.domain.account_linking import (
    LinkObservation,
    LinkState,
    LinkStateError,
    PendingLink,
)
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    CheckResult,
    ManagedError,
    OperationConflictError,
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
    check_denied,
    check_unavailable,
    complete,
    denied,
    unavailable,
)
from nora.domain.managed_ports import (
    AuthorityBackend,
    BindingStore,
    IdentityRegistry,
    LinkRequestStore,
    TopologySource,
)
from nora.domain.management import LinkSide, Scope, ScopeKind
from nora.people.app.managed_ops import (
    _finish,
    _ident_from,
    _ident_snapshot,
    _pending,
    _person_authority,
    _step_done,
)

log = logging.getLogger("nora.people.managed")

_PUBLISH_KIND = "publish_link"
_PUBLISH_STEPS = ("consolidate", "kratos", "authority", "readback")
_UNLINK_KIND = "unlink_account"
_UNLINK_STEPS = ("binding", "sessions", "readback")
_OWNERSHIP_KINDS = frozenset({_PUBLISH_KIND, _UNLINK_KIND})


def _journal_id(request_id: str) -> str:
    return f"publish-link:{request_id}"


def _scope_key(scope: Scope) -> str:
    return f"{scope.kind.value}:{scope.id}"


@dataclass(frozen=True)
class _CommitPlan:
    """The live reads a commit revalidates and the journaled effect it
    records — computed once, inside the mutation gate."""
    owners: Mapping[Identifier, str]
    active_people: frozenset[str]
    revisions: Mapping[str, str]
    survivor: str
    loser: str
    loser_kratos: str | None
    loser_relations: Mapping[Scope, frozenset[str]]
    loser_supports: Mapping[Scope, frozenset[str]]


class LinkPublicationService:
    """The request/approval boundary's other half: observation, commit and
    restart reconciliation over the durable `PendingLink` rows the
    `LinkRequestStore` recorded."""

    def __init__(
        self,
        bindings: BindingStore,
        backend: AuthorityBackend,
        topology: TopologySource,
        registry: IdentityRegistry,
        links: LinkRequestStore,
    ) -> None:
        self._bindings = bindings
        self._backend = backend
        self._topology = topology
        self._registry = registry
        self._links = links

    # -- the request-time observation ---------------------------------------

    async def observe(
        self, *, initiating: Identifier, receiving: Identifier,
        surviving_person: str,
    ) -> LinkObservation | CheckResult:
        """The authoritative snapshot the request binds: both accounts'
        CURRENT owners and the live identity/ownership/authority revisions.
        The caller records the returned values in the `PendingLink` — the
        approval then proves exactly this state."""
        try:
            return await self._observe(initiating, receiving, surviving_person)
        except Exception:
            log.exception("link observation failed")
            return check_unavailable("store-failure")

    async def _observe(
        self, initiating: Identifier, receiving: Identifier,
        surviving_person: str,
    ) -> LinkObservation | CheckResult:
        bound = await self._bound({
            LinkSide.INITIATING: initiating, LinkSide.RECEIVING: receiving,
        })
        if isinstance(bound, CheckResult):
            return bound
        owners = {side: record.person_id for side, record in bound.items()}
        if owners[LinkSide.INITIATING] == owners[LinkSide.RECEIVING]:
            return check_denied("already-linked")
        if surviving_person not in owners.values():
            return check_denied("survivor-not-owner")
        people = await self._people(
            frozenset(owners.values()),
            require_identity=frozenset(owners.values()),
        )
        if isinstance(people, CheckResult):
            return people
        revisions = await self._link_revisions(bound, people)
        if isinstance(revisions, CheckResult):
            return revisions
        return LinkObservation(
            owners=owners,
            receiving_person=owners[LinkSide.RECEIVING],
            revisions=revisions,
        )

    async def _bound(
        self, accounts: Mapping[LinkSide, Identifier]
    ) -> dict[LinkSide, Any] | CheckResult:
        """Each account's live BOUND record — exact or refused."""
        out: dict[LinkSide, Any] = {}
        for side, ident in accounts.items():
            record = await self._bindings.record_for(account=ident)
            if record is None or record.state is not AccountState.BOUND:
                return check_denied("account-unbound")
            out[side] = record
        return out

    async def _people(
        self, persons: frozenset[str], *, require_identity: frozenset[str]
    ) -> dict[str, Any] | CheckResult:
        """Live person records: present, ACTIVE, unheld and Kratos-verified."""
        out: dict[str, Any] = {}
        for person_id in persons:
            person = await self._bindings.person(person_id)
            if person is None:
                return check_denied("absent-person")
            if person.lifecycle is not PersonLifecycle.ACTIVE:
                return check_denied("person-suspended")
            if await self._bindings.identity_held(person_id) is not False:
                return check_unavailable("identity-publication-pending")
            state = await self._registry.identity_state(external_id=person_id)
            if state.verdict is Verdict.DENIED:
                return check_denied("identity-inactive")
            if state.verdict is Verdict.UNAVAILABLE:
                return check_unavailable("identity-unavailable")
            if person_id in require_identity and state.verdict is not Verdict.ALLOWED:
                return check_unavailable("identity-unconfirmed")
            out[person_id] = person
        return out

    async def _link_revisions(
        self, bound: Mapping[LinkSide, Any], people: Mapping[str, Any]
    ) -> Mapping[str, str] | CheckResult:
        """The revision set a request records — shared by observe and commit."""
        authority = await _person_authority(
            self._bindings, self._backend, self._topology, frozenset(people)
        )
        if isinstance(authority, CheckResult):
            return authority
        sides = (LinkSide.INITIATING, LinkSide.RECEIVING)
        return MappingProxyType({
            "identity": ":".join(
                str(people[person].revision) for person in sorted(people)
            ),
            "ownership": ":".join(
                f"{bound[side].revision}@{bound[side].person_id}" for side in sides
            ),
            "authority": authority,
        })

    async def authority_revision(self, person_id: str) -> str | None:
        """The live `authority` digest a holder's observed revisions bind —
        `None` when any store cannot answer (never a guessed value)."""
        result = await _person_authority(
            self._bindings, self._backend, self._topology,
            frozenset({person_id}))
        return result if isinstance(result, str) else None

    # -- commit ---------------------------------------------------------------

    async def commit(self, request_id: str, *, now: datetime) -> Outcome:
        """Publish one fully-approved request — account-exact and effect-
        bound by the stored `PendingLink`; the journal id is derived from
        the request so a retry or a restart resumes rather than re-asks."""
        try:
            async with self._bindings.mutation():
                return await self._commit(request_id, now)
        except OperationConflictError:
            return denied("operation-conflict")
        except ManagedError:
            raise
        except Exception:
            log.exception("link publication failed")
            return unavailable("store-failure")

    async def _commit(self, request_id: str, now: datetime) -> Outcome:
        link = await self._links.get(request_id)
        if link is None:
            return denied("unknown-request")
        record = await self._bindings.operation(_journal_id(request_id))
        if link.state is LinkState.COMMITTED:
            return await self._finish_committed(record)
        if link.state is LinkState.COMMITTING:
            if record is None:
                return unavailable("journal-missing")
            return await self._resume_committed(record, link)
        if link.state is not LinkState.PENDING:
            if record is not None and record.state is not OperationJournalState.COMPLETE:
                released = await self._release_holds(record)
                if released.status is not OutcomeStatus.COMPLETE:
                    return released
                await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                              "denied", f"request-{link.state.value}")
            return denied(f"request-{link.state.value}")
        if not link.ready:
            return denied("awaiting-approvals")
        if record is not None and record.state is not OperationJournalState.COMPLETE:
            released = await self._release_holds(record)
            if released.status is not OutcomeStatus.COMPLETE:
                return released
        plan = await self._plan(link)
        if isinstance(plan, Outcome):
            if (plan.status is OutcomeStatus.DENIED and record is not None
                    and record.state is not OperationJournalState.COMPLETE):
                await _finish(self._bindings, record,
                              OperationJournalState.COMPLETE,
                              plan.status.value, plan.detail)
            return plan
        if record is not None and record.state is OperationJournalState.COMPLETE:
            return Outcome(OutcomeStatus(record.outcome or "complete"), record.detail)
        if record is None:
            recorded = await self._record_publish(link, plan)
            if isinstance(recorded, Outcome):
                return recorded
            record = recorded
        elif dict(record.snapshot) != self._snapshot(link, plan):
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "denied", "effect-conflict")
            return denied("effect-conflict")
        return await self._publish(record, link, plan, now)

    async def _publish(
        self, record: OperationRecord, link: PendingLink,
        plan: _CommitPlan, now: datetime,
    ) -> Outcome:
        """Holds first, then the COMMITTING transition, then the journaled steps."""
        for person in (plan.survivor, plan.loser):
            outcome = await self._bindings.hold_identity(
                person_id=person, operation_id=record.operation_id
            )
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        try:
            await self._links.begin_commit(
                link.request_id, owners=plan.owners,
                active_people=plan.active_people,
                revisions=plan.revisions, now=now)
        except LinkStateError as error:
            released = await self._release_holds(record)
            if released.status is not OutcomeStatus.COMPLETE:
                return released
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "denied", str(error))
            return denied(str(error))
        return await self._run_steps(record)

    async def _resume_committed(
        self, record: OperationRecord, link: PendingLink
    ) -> Outcome:
        """COMMITTING: re-place this operation's holds and resume its recorded steps."""
        snapshot = dict(record.snapshot)
        for person in (snapshot["survivor"], snapshot["loser"]):
            outcome = await self._bindings.hold_identity(
                person_id=person, operation_id=record.operation_id
            )
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return await self._run_steps(record, link=link)

    async def _finish_committed(self, record: OperationRecord | None) -> Outcome:
        """COMMITTED: release this operation's holds and close the journal."""
        if record is None:
            return unavailable("journal-missing")
        if record.state is OperationJournalState.COMPLETE:
            return Outcome(
                OutcomeStatus(record.outcome or "complete"), record.detail)
        released = await self._release_holds(record)
        if released.status is not OutcomeStatus.COMPLETE:
            return released
        await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                      "complete")
        return complete()

    async def _plan(self, link: PendingLink) -> _CommitPlan | Outcome:
        """The live revalidation plus the exact Keto fact set to normalize."""
        bound = await self._bound({
            side: link.pair.account(side) for side in LinkSide
        })
        if isinstance(bound, CheckResult):
            return denied(bound.detail)
        owners = {
            link.pair.account(side): bound[side].person_id for side in LinkSide
        }
        person_ids = frozenset(owners.values())
        if len(person_ids) != 2 or link.surviving_person not in person_ids:
            return denied("survivor-not-owner")
        people = await self._people(
            person_ids, require_identity=person_ids,
        )
        if isinstance(people, CheckResult):
            if people.verdict is Verdict.UNAVAILABLE:
                return unavailable(people.detail)
            return denied(people.detail)
        revisions = await self._link_revisions(bound, people)
        if isinstance(revisions, CheckResult):
            return unavailable(revisions.detail)
        loser = next(p for p in person_ids if p != link.surviving_person)
        facts = await self._loser_facts(loser)
        if facts is None:
            return unavailable("authority-unavailable")
        relations, supports = facts
        return _CommitPlan(
            owners=MappingProxyType(owners),
            active_people=person_ids,
            revisions=MappingProxyType(revisions),
            survivor=link.surviving_person,
            loser=loser,
            loser_kratos=people[loser].kratos_identity_id,
            loser_relations=MappingProxyType(relations),
            loser_supports=MappingProxyType(supports),
        )

    async def _loser_facts(
        self, loser: str
    ) -> tuple[dict[Scope, frozenset[str]], dict[Scope, frozenset[str]]] | None:
        """The exact relation+support set to move — journaled, never re-derived."""
        try:
            scopes = await self._topology.scopes()
        except Exception:  # noqa: BLE001 — unknown scopes mean the effect cannot be journaled
            return None
        relations: dict[Scope, frozenset[str]] = {}
        for scope in scopes:
            rows = await self._backend.relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return None
            owned = frozenset(r for r, s in rows if s == loser)
            if owned:
                relations[scope] = owned
        supports = {
            m.scope: frozenset(m.sources)
            for m in await self._bindings.memberships_for(loser)
            if m.sources
        }
        return relations, supports

    def _snapshot(self, link: PendingLink, plan: _CommitPlan) -> dict[str, Any]:
        """The journaled effect — the recorded step set a restart replays."""
        return {
            "request_id": link.request_id,
            "survivor": plan.survivor,
            "loser": plan.loser,
            "loser_kratos": plan.loser_kratos,
            "pair": {
                side.value: _ident_snapshot(link.pair.account(side))
                for side in LinkSide
            },
            "scopes": [
                {"kind": scope.kind.value, "id": scope.id,
                 "relations": sorted(relations)}
                for scope, relations in sorted(
                    plan.loser_relations.items(),
                    key=lambda item: _scope_key(item[0]))
            ],
            "supports": {
                _scope_key(scope): sorted(sources)
                for scope, sources in plan.loser_supports.items()
            },
        }

    async def _record_publish(
        self, link: PendingLink, plan: _CommitPlan
    ) -> OperationRecord | Outcome:
        record = OperationRecord(
            operation_id=_journal_id(link.request_id),
            kind=_PUBLISH_KIND,
            state=OperationJournalState.PENDING,
            steps=MappingProxyType(dict.fromkeys(_PUBLISH_STEPS, "pending")),
            snapshot=MappingProxyType(self._snapshot(link, plan)),
        )
        outcome = await self._bindings.record_operation(record)
        return record if outcome.status is OutcomeStatus.COMPLETE else outcome

    # -- the journaled steps --------------------------------------------------

    async def _run_steps(
        self, record: OperationRecord, link: PendingLink | None = None
    ) -> Outcome:
        snapshot = dict(record.snapshot)
        outcome = await self._step(
            record, "consolidate",
            lambda: self._bindings.consolidate(
                survivor=snapshot["survivor"], former=snapshot["loser"]),
        )
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._step(
                record, "kratos", lambda: self._retire_kratos(snapshot))
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._step(
                record, "authority", lambda: self._normalize(snapshot))
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._step(
                record, "readback", lambda: self._readback(snapshot))
        if outcome.status is not OutcomeStatus.COMPLETE:
            await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        return await self._tail(record, link)

    async def _step(
        self, record: OperationRecord, step: str, effect: Any
    ) -> Outcome:
        if not _pending(record, step):
            return complete()
        outcome = await effect()
        if outcome.status is OutcomeStatus.COMPLETE:
            await _step_done(self._bindings, record, step)
        return outcome

    async def _retire_kratos(self, snapshot: Mapping[str, Any]) -> Outcome:
        """Deactivate the retired person's identity and end its sessions."""
        loser = snapshot["loser"]
        outcome = await self._registry.set_state(external_id=loser, active=False)
        if outcome.status is not OutcomeStatus.COMPLETE:
            return outcome
        return await self._registry.drop_sessions(external_id=loser)

    async def _normalize(self, snapshot: Mapping[str, Any]) -> Outcome:
        """Move every journaled relation to the survivor — put before drop."""
        survivor = snapshot["survivor"]
        loser = snapshot["loser"]
        for entry in snapshot["scopes"]:
            scope = Scope(kind=ScopeKind(entry["kind"]), id=entry["id"])
            rows = await self._backend.relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return unavailable(rows.detail)
            have = set(rows)
            for relation in entry["relations"]:
                if (relation, survivor) not in have:
                    outcome = await self._backend.put_relation(
                        scope=scope, relation=relation, person_id=survivor)
                    if outcome.status is not OutcomeStatus.COMPLETE:
                        return outcome
                outcome = await self._backend.drop_relation(
                    scope=scope, relation=relation, person_id=loser)
                if outcome.status is not OutcomeStatus.COMPLETE:
                    return outcome
        return complete()

    async def _readback(self, snapshot: Mapping[str, Any]) -> Outcome:
        """Fail-closed agreement across bindings, Keto facts, supports and Kratos."""
        survivor = snapshot["survivor"]
        loser = snapshot["loser"]
        canonical = await self._bindings.canonical_person(loser)
        if canonical != survivor:
            return unavailable("read-back-mismatch")
        for entry in snapshot["scopes"]:
            scope = Scope(kind=ScopeKind(entry["kind"]), id=entry["id"])
            rows = await self._backend.relations_on(scope=scope)
            if isinstance(rows, CheckResult):
                return unavailable(rows.detail)
            have = set(rows)
            moved = {(relation, survivor) for relation in entry["relations"]}
            if {pair for pair in have if pair[1] == loser} or not moved <= have:
                return unavailable("read-back-mismatch")
        for side in ("initiating", "receiving"):
            ident = _ident_from(snapshot["pair"][side])
            bound = await self._bindings.record_for(account=ident)
            if bound is None or bound.person_id != survivor:
                return unavailable("read-back-mismatch")
        survivor_members = {
            m.scope: frozenset(m.sources)
            for m in await self._bindings.memberships_for(survivor)
        }
        for key, sources in snapshot["supports"].items():
            kind, _, scope_id = key.partition(":")
            scope = Scope(kind=ScopeKind(kind), id=scope_id)
            if not set(sources) <= set(survivor_members.get(scope, ())):
                return unavailable("read-back-mismatch")
        state = await self._registry.identity_state(external_id=loser)
        if state.verdict in {Verdict.UNAVAILABLE, Verdict.UNKNOWN_SCOPE}:
            return unavailable("identity-unavailable")
        if state.verdict is Verdict.ALLOWED:
            return unavailable("kratos-not-retired")
        return complete()

    async def _tail(
        self, record: OperationRecord, link: PendingLink | None
    ) -> Outcome:
        """Release ONLY this operation's holds, mark COMMITTED, close the journal."""
        snapshot = dict(record.snapshot)
        request_id = snapshot["request_id"]
        if link is None:
            link = await self._links.get(request_id)
        if link is None or link.state not in {LinkState.COMMITTING, LinkState.COMMITTED}:
            return unavailable("request-state-unconfirmed")
        if link.state is LinkState.COMMITTING:
            await self._links.mark_committed(request_id)
        released = await self._release_holds(record)
        if released.status is not OutcomeStatus.COMPLETE:
            return released
        await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                      "complete")
        return complete()

    # -- reconciliation --------------------------------------------------------

    async def reconcile(self) -> tuple[OperationRecord, ...]:
        """Restart recovery — resume every journaled publish/unlink the
        journal still owes. Each kind re-runs ONLY its recorded steps;
        other journals (`authentication-session`, authority ops) are not
        this service's to touch."""
        async with self._bindings.mutation():
            return await self._reconcile()

    async def _reconcile(self) -> tuple[OperationRecord, ...]:
        finished: list[OperationRecord] = []
        for record in await self._bindings.incomplete_operations():
            if record.kind not in _OWNERSHIP_KINDS:
                continue
            try:
                if record.kind == _PUBLISH_KIND:
                    outcome = await self._resume_publish(record)
                else:
                    outcome = await self._resume_unlink(record)
            except ManagedError:
                raise
            except Exception:
                log.exception("ownership replay failed")
                outcome = unavailable("store-failure")
            if outcome.status is OutcomeStatus.COMPLETE:
                current = await self._bindings.operation(record.operation_id)
                if (current is not None
                        and current.state is OperationJournalState.COMPLETE):
                    finished.append(current)
        return tuple(finished)

    async def _resume_publish(self, record: OperationRecord) -> Outcome:
        snapshot = dict(record.snapshot)
        link = await self._links.get(snapshot["request_id"])
        if link is None:
            return unavailable("request-state-unconfirmed")
        if link.state is LinkState.COMMITTED:
            return await self._finish_committed(record)
        if link.state is LinkState.COMMITTING:
            return await self._resume_committed(record, link)
        released = await self._release_holds(record)
        if released.status is not OutcomeStatus.COMPLETE:
            return released
        if link.state is not LinkState.PENDING:
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "denied", f"request-{link.state.value}")
            return denied(f"request-{link.state.value}")
        # PENDING with a journal: the commit attempt died before the
        # transition — rebuild the plan; drift in the recorded effect
        # refuses instead of silently publishing a different one.
        plan = await self._plan(link)
        if isinstance(plan, Outcome):
            if plan.status is OutcomeStatus.DENIED:
                await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                              plan.status.value, plan.detail)
            return plan
        if dict(record.snapshot) != self._snapshot(link, plan):
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "denied", "effect-conflict")
            return denied("effect-conflict")
        return await self._publish(record, link, plan, _utcnow())

    async def _resume_unlink(self, record: OperationRecord) -> Outcome:
        """A journaled unlink replays: binding -> sessions -> binding-absent read-back."""
        snapshot = dict(record.snapshot)
        ident = _ident_from(snapshot["account"])
        person = await self._bindings.canonical_person(
            snapshot["expected_person"]) or snapshot["expected_person"]
        outcome = await self._step(record, "binding", lambda: self._revoke(ident))
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._step(
                record, "sessions", lambda: self._sessions_dropped(person))
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._step(
                record, "readback",
                lambda: self._binding_absent(ident))
        if outcome.status is not OutcomeStatus.COMPLETE:
            await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                      "complete")
        return complete()

    async def _sessions_dropped(self, person_id: str) -> Outcome:
        """Require the registry's positive session-revocation read-back."""
        return await self._registry.drop_sessions(external_id=person_id)

    async def _release_holds(self, record: OperationRecord) -> Outcome:
        """Clear only this operation's holds at a safe preparation or completed-effect boundary."""
        for person in (record.snapshot["survivor"], record.snapshot["loser"]):
            outcome = await self._bindings.release_identity_hold(
                person_id=person, operation_id=record.operation_id)
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        return complete()

    async def _revoke(self, ident: Identifier) -> Outcome:
        bound = await self._bindings.account_for(
            ident.platform, ident.kind, ident.value)
        if bound is None:
            return complete()
        return await self._bindings.revoke_binding(account=ident)

    async def _binding_absent(self, ident: Identifier) -> Outcome:
        bound = await self._bindings.account_for(
            ident.platform, ident.kind, ident.value)
        return complete() if bound is None else unavailable("read-back-mismatch")


def _utcnow() -> datetime:
    return datetime.now(UTC)
