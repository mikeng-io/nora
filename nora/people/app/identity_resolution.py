"""Resolve an active identity under current Nora and Kratos state."""

import logging

from nora.domain.identity import Identifier
from nora.domain.identity_resolution import IdentityResolution, ResolutionStatus
from nora.domain.managed import PersonLifecycle, Verdict
from nora.domain.managed_ports import IdentityRegistry, PersonDirectory, TopologySource
from nora.domain.management import Scope

log = logging.getLogger("nora.people.identity_resolution")


class IdentityResolver:
    def __init__(
        self, bindings: PersonDirectory, registry: IdentityRegistry, topology: TopologySource,
    ) -> None:
        self._bindings = bindings
        self._registry = registry
        self._topology = topology

    async def resolve(self, account: Identifier, *, scope: Scope) -> IdentityResolution:
        """The caller authenticates and authorizes its source/resource first.

        Only the canonical id is returned, never another account or its aliases.
        """
        try:
            exists = await self._topology.scope_exists(scope)
            if not isinstance(exists, bool):
                return IdentityResolution(ResolutionStatus.UNAVAILABLE)
            if not exists:
                return IdentityResolution(ResolutionStatus.DENIED)
            bound = await self._bindings.account_for(account.platform, account.kind, account.value)
            if bound is None:
                return IdentityResolution(ResolutionStatus.ABSENT)
            canonical = await self._bindings.canonical_person(bound.person_id)
            if canonical is None:
                return IdentityResolution(ResolutionStatus.UNAVAILABLE)
            if await self._bindings.identity_held(canonical) is not False:
                return IdentityResolution(ResolutionStatus.UNAVAILABLE)
            lifecycle = await self._bindings.person_lifecycle(canonical)
            if lifecycle is PersonLifecycle.SUSPENDED:
                return IdentityResolution(ResolutionStatus.DENIED)
            if lifecycle is not PersonLifecycle.ACTIVE:
                return IdentityResolution(ResolutionStatus.UNAVAILABLE)
            identity = await self._registry.identity_state(external_id=canonical)
            if identity.verdict is Verdict.DENIED:
                return IdentityResolution(ResolutionStatus.DENIED)
            if identity.verdict is not Verdict.ALLOWED:
                return IdentityResolution(ResolutionStatus.UNAVAILABLE)
            return IdentityResolution(ResolutionStatus.OK, canonical)
        except Exception as error:  # noqa: BLE001 — private boundary preserves unavailable
            log.error("identity resolution unavailable: %s", type(error).__name__)
            return IdentityResolution(ResolutionStatus.UNAVAILABLE)
