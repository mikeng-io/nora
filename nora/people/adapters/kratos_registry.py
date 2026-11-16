"""`IdentityRegistry` over the pinned Kratos admin client — the managed
identity record (`design/managed-identity-access/identity.md`).

`ensure_identity` is the concurrency-critical verb: a create that hits the
`external_id` unique constraint is resolved by READ-BACK of the identity
that already holds it — the collision is proof a Kratos identity exists for
the person, never proof two persons are one. Everything else is a direct
client call; the client has already classified absent/denied/unavailable.
"""

from __future__ import annotations

from typing import Any

from nora.domain.managed import CheckResult, Outcome, complete
from nora.domain.managed_ports import IdentityRegistry
from nora.infra.ory import KratosClient


class KratosIdentityRegistry(IdentityRegistry):
    def __init__(self, client: KratosClient) -> None:
        self._client = client

    async def ensure_identity(
        self, *, external_id: str, traits: dict[str, Any]
    ) -> tuple[Outcome, str | None]:
        outcome, identity = await self._client.create_identity(
            external_id=external_id, traits=traits
        )
        if identity is not None:
            return outcome, identity.identity_id
        if outcome.detail != "external-id-conflict":
            return outcome, None
        # 409: an identity already holds this external_id. Read it back —
        # re-running a half-completed registration lands here and must
        # recover the SAME identity the first attempt created.
        outcome2, existing = await self._client.get_by_external_id(external_id)
        if existing is not None:
            return complete("read-back"), existing.identity_id
        return outcome2, None

    async def identity_state(self, *, external_id: str) -> CheckResult:
        return await self._client.state_of(external_id)

    async def identity_traits(
        self, *, external_id: str
    ) -> tuple[Outcome, dict[str, Any] | None]:
        outcome, identity = await self._client.get_by_external_id(external_id)
        if identity is None:
            return outcome, None
        return complete(), identity.traits

    async def set_state(self, *, external_id: str, active: bool) -> Outcome:
        outcome, identity = await self._client.get_by_external_id(external_id)
        if identity is None:
            return outcome
        return await self._client.set_state(
            identity_id=identity.identity_id, active=active
        )

    async def drop_sessions(self, *, external_id: str) -> Outcome:
        outcome, identity = await self._client.get_by_external_id(external_id)
        if identity is None:
            return outcome
        return await self._client.drop_sessions(identity_id=identity.identity_id)

    async def aclose(self) -> None:
        """The client shares the caller's session — nothing to close."""
