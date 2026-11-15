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
                    collection: str, members: Iterable[Identifier],
                    complete_roster: bool) -> Outcome:
        accounts = frozenset(members)
        if (actor.kind is not ActorKind.SERVICE or not operation_id.strip()
                or not collection.strip()
                or any(a.platform not in actor.sources for a in accounts)):
            return denied("invalid-roster-source")
        try:
            async with self.bindings.mutation():
                if not await self.topology.scope_exists(scope):
                    return denied("unknown-scope")
                return await self._apply(operation_id, actor, scope, collection,
                                         accounts, complete_roster)
        except OperationConflictError:
            return denied("operation-conflict")
        except Exception:
            log.exception("bulk roster provisioning failed")
            return unavailable("store-failure")

    async def _apply(self, key: str, actor: Actor, scope: Scope, collection: str,
                     members: frozenset[Identifier], complete_roster: bool) -> Outcome:
        ordered = sorted(members, key=lambda a: (a.platform, a.kind, a.value))
        snapshot = {"service": actor.service_id, "collection": collection,
            "scope": {"kind": scope.kind.value, "id": scope.id}, "complete": complete_roster,
            "members": [[a.platform, a.kind, a.value] for a in ordered]}
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
        for account in ordered:
            outcome = await self._account(key, actor, account)
            if outcome.status is not OutcomeStatus.COMPLETE:
                await _finish(self.bindings, record, OperationJournalState.INCOMPLETE,
                              outcome.status.value, outcome.detail)
                return incomplete("account-registration-unconfirmed")
        await _step_done(self.bindings, record, "accounts")
        sync = Operation(key + ":membership", "sync_membership", actor,
            Target(TargetKind.SCOPE, scope=scope),
            RosterSync(members, complete_roster, collection), {"authority": "current"})
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
        return outcome if state is OperationJournalState.COMPLETE else incomplete(outcome.detail)

    async def _account(self, key: str, actor: Actor, account: Identifier) -> Outcome:
        raw = json.dumps([account.platform, account.kind, account.value], separators=(",", ":"))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        current = await self.bindings.record_for(account=account)
        operation = Operation(key + ":account:" + digest, "observe_account", actor,
            Target(TargetKind.ACCOUNT, account=account), AccountObservation(frozenset({account})),
            {"ownership": str(current.revision) if current is not None else "0"})
        try:
            outcome = await self.registration._apply(operation)
        except IdentifierConflictError:
            return denied("identifier-conflict")
        if outcome.status is not OutcomeStatus.COMPLETE:
            return outcome
        bound = await self.bindings.account_for(account.platform, account.kind, account.value)
        if bound is None:
            return denied("unbound-account")
        if await self.bindings.person_lifecycle(bound.person_id) is not PersonLifecycle.ACTIVE:
            return denied("person-inactive")
        state = await self.registry.identity_state(external_id=bound.person_id)
        if state.verdict is Verdict.ALLOWED:
            return complete()
        return unavailable("identity-unconfirmed")
