"""Revalidate an account-exact login and its retained identity/binding revision."""

import hashlib
import json
import logging
from dataclasses import asdict
from types import MappingProxyType

from nora.domain.authentication import (
    AccountAuthentication,
    AuthenticatedPrincipal,
    AuthenticationResult,
    SessionBinding,
)
from nora.domain.managed import (
    OperationJournalState,
    OperationRecord,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
)
from nora.domain.managed_ports import BindingStore, IdentityRegistry

log = logging.getLogger("nora.people.authentication")


class AccountSessions:
    def __init__(self, bindings: BindingStore, registry: IdentityRegistry) -> None:
        self._bindings = bindings
        self._registry = registry

    async def check(self, authentication: AccountAuthentication) -> AuthenticationResult:
        try:
            if authentication.purpose == "sign_in":
                async with self._bindings.mutation():
                    return await self._check(authentication)
            return await self._check(authentication)
        except Exception as error:  # noqa: BLE001 — an unreadable authority never authenticates
            log.error("managed session unavailable: %s", type(error).__name__)
            return AuthenticationResult(Verdict.UNAVAILABLE)

    async def _check(self, authentication: AccountAuthentication) -> AuthenticationResult:
        account = await self._bindings.record_for(account=authentication.account)
        if account is None:
            return AuthenticationResult(Verdict.DENIED)
        canonical = await self._bindings.canonical_person(account.person_id)
        if canonical is None or canonical != account.person_id:
            return AuthenticationResult(Verdict.UNAVAILABLE)
        if await self._bindings.identity_held(canonical) is not False:
            return AuthenticationResult(Verdict.UNAVAILABLE)
        person = await self._bindings.person(canonical)
        if person is None:
            return AuthenticationResult(Verdict.UNAVAILABLE)
        if person.lifecycle is not PersonLifecycle.ACTIVE:
            return AuthenticationResult(Verdict.DENIED)
        identity = await self._registry.identity_state(external_id=canonical)
        if identity.verdict is not Verdict.ALLOWED:
            return AuthenticationResult(Verdict.DENIED if identity.verdict is Verdict.DENIED
                                        else Verdict.UNAVAILABLE)
        binding = SessionBinding(canonical, str(person.revision), account.account_id,
                                 str(account.revision))
        if authentication.binding is not None and authentication.binding != binding:
            return AuthenticationResult(Verdict.DENIED)
        if authentication.purpose == "sign_in":
            verdict = await self._capture(authentication, binding)
            if verdict is not Verdict.ALLOWED:
                return AuthenticationResult(verdict)
        return AuthenticationResult(Verdict.ALLOWED,
            AuthenticatedPrincipal(authentication, binding, person.display_name))

    async def _capture(
        self, authentication: AccountAuthentication, binding: SessionBinding,
    ) -> Verdict:
        identity = json.dumps([authentication.issuer, authentication.session_id])
        operation_id = "authentication:" + hashlib.sha256(identity.encode()).hexdigest()
        evidence = {"issuer": authentication.issuer, "session_id": authentication.session_id,
                    "account": asdict(authentication.account), "method": authentication.method,
                    "proof_issuer": authentication.proof_issuer,
                    "authenticated_at": authentication.authenticated_at.isoformat()}
        snapshot = {"authentication": evidence, "binding": asdict(binding)}
        recorded = await self._bindings.operation(operation_id)
        if recorded is not None:
            valid = (recorded.kind == "authentication-session"
                     and recorded.state is OperationJournalState.COMPLETE
                     and recorded.snapshot == snapshot)
            return Verdict.ALLOWED if valid else Verdict.DENIED
        outcome = await self._bindings.record_operation(OperationRecord(
            operation_id, "authentication-session", OperationJournalState.COMPLETE,
            MappingProxyType({}), snapshot=MappingProxyType(snapshot),
        ))
        return Verdict.ALLOWED if outcome.status is OutcomeStatus.COMPLETE else Verdict.UNAVAILABLE
