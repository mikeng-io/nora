"""Commit a human management request under its current exact-account session."""

import logging
from dataclasses import dataclass

from nora.domain.authentication import (
    AccountAuthentication,
    AuthenticationResult,
    RoomAuthentication,
)
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
from nora.people.app.managed_ops import _journal_after_failure

log = logging.getLogger("nora.people.management_invocation")
SCOPED_HUMAN_OPERATIONS = frozenset({
    "register_account", "set_membership", "grant_access", "revoke_access",
    "delegate", "revoke_delegation",
})


@dataclass(frozen=True)
class ResolvedTarget:
    """The account references a resolved membership target rode in on —
    `(platform, value)` pairs as the caller named them. A membership
    target cannot carry an account (`Target` forbids it), so the
    `ownership` observed-revision cannot see a binding that moved between
    resolution and commitment; `apply` re-resolves each reference inside
    the mutation gate and refuses when one now names a different or no
    canonical person, instead of mutating the stale person."""

    refs: frozenset[tuple[str, str]] = frozenset()


class HumanManagement:
    def __init__(self, bindings: BindingStore, registry: IdentityRegistry,
                 registration: RegistrationService, authority: AuthorityService) -> None:
        self._bindings = bindings
        self._sessions = AccountSessions(bindings, registry)
        self._registration = registration
        self._authority = authority

    async def authenticate(
        self, authentication: AccountAuthentication | RoomAuthentication
    ) -> AuthenticationResult:
        """The same `AccountSessions` check `apply` re-runs inside the
        gate — invoked ahead of target reads so a sender whose exact
        binding, lifecycle, hold or registry identity cannot currently
        authenticate never reaches them."""
        return await self._sessions.check(authentication)

    async def apply(
        self, operation: Operation,
        authentication: AccountAuthentication | RoomAuthentication,
        target: ResolvedTarget | None = None,
    ) -> Outcome:
        # Retained sessions carry a binding claim; transport messages resolve it under the gate.
        authenticated = (
            (authentication.purpose == "session" and authentication.binding is not None)
            or (authentication.purpose == "message" and authentication.binding is None)
        )
        if (not authenticated
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
                if target is not None:
                    refused = await self._target_current(operation, target)
                    if refused is not None:
                        return refused
                # One area-owned invocation holds the gate through authentication and effects.
                service = (self._registration if operation.kind == "register_account"
                           else self._authority)
                return await service._apply(operation)
        except (IdentifierConflictError, OperationConflictError):
            return denied("operation-conflict")
        except Exception as error:  # noqa: BLE001 — an unreadable authority never authorizes
            log.error("human management unavailable: %s", type(error).__name__)
            # The journal, not the exception, says whether part of the
            # recorded effect already committed — a bare outage would call
            # a partial write "nothing applied".
            admitted = await _journal_after_failure(self._bindings, operation)
            return admitted if admitted is not None else unavailable("store-failure")

    async def _target_current(
        self, operation: Operation, target: ResolvedTarget
    ) -> Outcome | None:
        """`None` while every resolved reference still names the operation's canonical person."""
        for platform, value in sorted(target.refs):
            bound = await self._bindings.resolve(platform, value)
            canonical = (
                await self._bindings.canonical_person(bound.person_id)
                if bound is not None
                else None
            )
            if canonical != operation.target.person_id:
                return denied("target-binding-changed")
        return None
