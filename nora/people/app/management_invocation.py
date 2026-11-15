"""Commit a human management request under its current exact-account session."""

import logging

from nora.domain.authentication import AccountAuthentication
from nora.domain.managed import (
    IdentifierConflictError,
    OperationConflictError,
    Outcome,
    Verdict,
    denied,
    unavailable,
)
from nora.domain.managed_ports import BindingStore, IdentityRegistry
from nora.domain.management import AccountRegistration, ActorKind, Operation
from nora.people.app.authentication import AccountSessions
from nora.people.app.managed import RegistrationService
from nora.people.app.managed_authority import _REQUIRED_PERMIT, AuthorityService

log = logging.getLogger("nora.people.management_invocation")
SCOPED_HUMAN_OPERATIONS = frozenset({
    "register_account", "set_membership", "grant_access", "revoke_access",
    "delegate", "revoke_delegation",
})


class HumanManagement:
    def __init__(self, bindings: BindingStore, registry: IdentityRegistry,
                 registration: RegistrationService, authority: AuthorityService) -> None:
        self._bindings = bindings
        self._sessions = AccountSessions(bindings, registry)
        self._registration = registration
        self._authority = authority

    async def apply(self, operation: Operation, authentication: AccountAuthentication) -> Outcome:
        if (authentication.purpose != "session" or authentication.binding is None
                or operation.actor.kind is not ActorKind.OPERATOR
                or operation.kind not in SCOPED_HUMAN_OPERATIONS):
            return denied("unsupported-human-invocation")
        try:
            async with self._bindings.mutation():
                verified = await self._sessions.check(authentication)
                if verified.verdict is Verdict.UNAVAILABLE:
                    return unavailable("authentication-unavailable")
                principal = verified.principal
                if (principal is None
                        or operation.actor.person_id != principal.binding.person_id):
                    return denied("authentication-changed")
                scope = (operation.payload.scope
                         if isinstance(operation.payload, AccountRegistration)
                         else operation.target.scope)
                if scope is None:
                    return denied("unscoped-operation")
                permission = await self._authority.check(scope=scope,
                    person_id=principal.binding.person_id, permit=_REQUIRED_PERMIT[operation.kind])
                if permission.verdict is Verdict.UNAVAILABLE:
                    return unavailable("authority-unavailable")
                if not permission.allowed:
                    return denied("operator-not-authorized")
                # One area-owned invocation holds the gate through authentication and effects.
                service = (self._registration if operation.kind == "register_account"
                           else self._authority)
                return await service._apply(operation)
        except (IdentifierConflictError, OperationConflictError):
            return denied("operation-conflict")
        except Exception as error:  # noqa: BLE001 — an unreadable authority never authorizes
            log.error("human management unavailable: %s", type(error).__name__)
            return unavailable("store-failure")
