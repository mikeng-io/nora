"""Bulk onboarding shares registration, source-scoped sync and the mutation gate."""

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from nora.domain.identity import Identifier
from nora.domain.managed import (
    IdentifierConflictError,
    ObservedMember,
    OperationConflictError,
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
    complete,
    denied,
    incomplete,
    unavailable,
)
from nora.domain.managed_ports import BindingStore, IdentityRegistry, TopologySource
from nora.domain.management import (
    AccountObservation,
    Actor,
    ActorKind,
    Operation,
    RosterSync,
    Scope,
    Target,
    TargetKind,
)
from nora.people.app.managed import RegistrationService
from nora.people.app.managed_authority import AuthorityService
from nora.people.app.managed_ops import _finish, _step_done

log = logging.getLogger("nora.people.managed")


@dataclass(frozen=True)
class RosterProvisioning:
    bindings: BindingStore
    registry: IdentityRegistry
    topology: TopologySource
    registration: RegistrationService
    authority: AuthorityService

    async def apply(self, *, operation_id: str, actor: Actor, scope: Scope,
                    collection: str, members: Iterable[ObservedMember],
                    complete_roster: bool, observed_ms: int) -> Outcome:
        roster = tuple(members)
        groups = tuple(frozenset(member.identifiers) for member in roster
                       if member.classification == "person")
        if (actor.kind is not ActorKind.SERVICE or not operation_id.strip()
                or not collection.strip()
                or any(len({i.platform for i in group}) != 1
                       or any(i.platform not in actor.sources for i in group)
                       for group in groups)):
            return denied("invalid-roster-source")
        if complete_roster and any(
                member.classification == "unknown" for member in roster):
            return unavailable("unclassified-member")
        try:
            async with self.bindings.mutation():
                if not await self.topology.scope_exists(scope):
                    return denied("unknown-scope")
                return await self._apply(operation_id, actor, scope, collection,
                                         groups, complete_roster, observed_ms)
        except OperationConflictError:
            return denied("operation-conflict")
        except Exception:
            log.exception("bulk roster provisioning failed")
            return unavailable("store-failure")

    async def _apply(self, key: str, actor: Actor, scope: Scope, collection: str,
                     groups: tuple[frozenset[Identifier], ...],
                     complete_roster: bool, observed_ms: int) -> Outcome:
        ordered = sorted(groups, key=lambda group: min(
            (i.platform, i.kind, i.value) for i in group))
        snapshot = {"service": actor.service_id, "collection": collection,
            "scope": {"kind": scope.kind.value, "id": scope.id},
            "complete": complete_roster, "observed_ms": observed_ms,
            "members": [[
                [i.platform, i.kind, i.value]
                for i in sorted(group, key=lambda i: (i.platform, i.kind, i.value))
            ] for group in ordered]}
        record = await self.bindings.operation(key)
        if record is not None:
            if record.kind != "provision_roster" or record.snapshot != snapshot:
                return denied("operation-conflict")
            if record.state is OperationJournalState.COMPLETE:
                return complete("replayed")
        else:
            record = OperationRecord(key, "provision_roster", OperationJournalState.PENDING,
                MappingProxyType({"accounts": "pending", "membership": "pending"}),
                snapshot=MappingProxyType(snapshot))
            recorded = await self.bindings.record_operation(record)
            if recorded.status is not OutcomeStatus.COMPLETE:
                return recorded
        for group in ordered:
            outcome = await self._account(key, actor, group)
            if outcome.status is not OutcomeStatus.COMPLETE:
                await _finish(self.bindings, record, OperationJournalState.INCOMPLETE,
                              outcome.status.value, outcome.detail)
                return incomplete("account-registration-unconfirmed")
        await _step_done(self.bindings, record, "accounts")
        members = frozenset().union(*ordered) if ordered else frozenset()
        sync = Operation(key + ":membership", "sync_membership", actor,
            Target(TargetKind.SCOPE, scope=scope),
            RosterSync(members, complete_roster, collection, observed_ms),
            {"authority": "current"})
        outcome = await self.authority._apply(sync)
        if outcome.status is OutcomeStatus.INCOMPLETE:
            pending = await self.bindings.operation(sync.operation_id)
            if pending is not None:
                outcome = await self.authority._replay_current(pending)
        if outcome.status is OutcomeStatus.COMPLETE:
            await _step_done(self.bindings, record, "membership")
        state = (OperationJournalState.COMPLETE if outcome.status is OutcomeStatus.COMPLETE
                 else OperationJournalState.INCOMPLETE)
        await _finish(self.bindings, record, state, outcome.status.value, outcome.detail)
        # The inner sync's refusal is its own answer — a pre-effect
        # freshness/ordering refusal is `unavailable`, never dressed as a
        # partial commit and never as completion.
        return outcome

    async def _account(self, key: str, actor: Actor,
                       group: frozenset[Identifier]) -> Outcome:
        """Registers ONE observed account — `group` is every identifier that account presented."""
        raw = json.dumps(
            [[i.platform, i.kind, i.value]
             for i in sorted(group, key=lambda i: (i.platform, i.kind, i.value))],
            separators=(",", ":"))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        account = min(group, key=lambda i: (i.platform, i.kind, i.value))
        current = await self.bindings.record_for(account=account)
        operation = Operation(key + ":account:" + digest, "observe_account", actor,
            Target(TargetKind.ACCOUNT, account=account), AccountObservation(group),
            {"ownership": str(current.revision) if current is not None else "0"})
        try:
            outcome = await self.registration._apply(operation)
        except IdentifierConflictError:
            return denied("identifier-conflict")
        if outcome.status is not OutcomeStatus.COMPLETE:
            return outcome
        # Read back EVERY supplied identifier as the SAME exact account —
        # the group the observation committed, not only the selected
        # target. A group that resolves anywhere else, or nowhere, never
        # reaches membership sync.
        owners: dict[str, str] = {}
        for ident in group:
            record = await self.bindings.record_for(account=ident)
            if record is None:
                return denied("unbound-account")
            owners[record.account_id] = record.person_id
        if len(owners) != 1:
            return denied("observation-accounts-conflict")
        person_id = next(iter(owners.values()))
        if await self.bindings.person_lifecycle(person_id) is not PersonLifecycle.ACTIVE:
            return denied("person-inactive")
        state = await self.registry.identity_state(external_id=person_id)
        if state.verdict is Verdict.ALLOWED:
            return complete()
        return unavailable("identity-unconfirmed")
