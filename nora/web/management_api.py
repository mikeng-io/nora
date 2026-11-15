"""Authenticated service provisioning through the shared management boundary.

Credentials and resource scopes come from trusted composition. A bridge bearer
does not authenticate a human holder or give a service operator privileges.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from nora.domain.management import (
    AccountRegistration,
    Actor,
    ActorKind,
    Operation,
    Scope,
)
from nora.web.management_request import ManagementRequestError, decode_management_request


@dataclass(frozen=True)
class ProvisioningCredential:
    actor: Actor
    token: str = field(repr=False)
    scopes: frozenset[Scope] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.actor.kind is not ActorKind.SERVICE:
            raise ValueError("a provisioning credential authenticates a service only")
        if not self.token or self.token != self.token.strip():
            raise ValueError("a provisioning credential requires a nonblank token")
        if not self.scopes:
            raise ValueError("a provisioning credential requires approved scopes")
        object.__setattr__(self, "scopes", frozenset(self.scopes))

    def admits(self, operation: Operation) -> bool:
        if operation.actor != self.actor:
            return False
        if operation.kind == "register_account":
            payload = operation.payload
            return isinstance(payload, AccountRegistration) and payload.scope in self.scopes
        return (operation.kind == "sync_membership"
                and operation.target.scope in self.scopes)


def register_management_routes(
    app: web.Application, *, services: Any,
    credentials: tuple[ProvisioningCredential, ...],
) -> None:
    """Add service-only provisioning; human endpoints require separate proofs.

    `services` is the people area's ManagedServices, supplied by composition;
    web does not import the area's protected application modules.
    Exact scope allowlists are deliberate: topology containment and current
    permission checks remain the actual services' responsibility. A shared
    transport token or a claimed platform role never selects these credentials.
    """
    credentials = tuple(credentials)
    if not credentials:
        raise ValueError("management routes require provisioning credentials")
    if len({c.token for c in credentials}) != len(credentials):
        raise ValueError("provisioning credentials cannot share a token")
    if len({c.actor.service_id for c in credentials}) != len(credentials):
        raise ValueError("provisioning service identities must be unique")

    async def execute(request: web.Request) -> web.Response:
        authorization = request.headers.get("Authorization", "").encode()
        credential = next((
            c for c in credentials
            if hmac.compare_digest(authorization, f"Bearer {c.token}".encode())
        ), None)
        headers = {"Cache-Control": "no-store"}
        if credential is None:
            return web.json_response({"status": "denied"}, status=401, headers=headers)
        try:
            operation = decode_management_request(
                await request.read(), actor=credential.actor,
            )
        except ManagementRequestError:
            return web.json_response({"status": "invalid_request"}, status=400,
                                     headers=headers)
        if not credential.admits(operation):
            return web.json_response({"status": "denied"}, status=403, headers=headers)
        if operation.kind == "register_account":
            outcome = await services.registration.apply(operation)
        else:
            outcome = await services.authority.apply(operation)
        status = outcome.status.value
        return web.json_response(
            {"status": status, "detail": outcome.detail},
            status={"complete": 200, "denied": 403,
                    "unavailable": 503, "incomplete": 409}[status],
            headers=headers,
        )

    app.router.add_post("/app/v1/management/operations", execute)
