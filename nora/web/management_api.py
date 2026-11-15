"""Authenticated service provisioning through the shared management boundary.

Credentials and resource scopes come from trusted composition. A bridge bearer
does not authenticate a human holder or give a service operator privileges.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from pydantic import BaseModel, ConfigDict, ValidationError

from nora.domain.identity import Identifier
from nora.domain.identity_resolution import ResolutionStatus
from nora.domain.management import (
    AccountRegistration,
    Actor,
    ActorKind,
    Operation,
    Scope,
    ScopeKind,
)
from nora.people import HumanManagement, IdentityResolver, RosterProvisioning
from nora.web.management_auth import AuthenticationRefusedError, AuthenticationVerifier
from nora.web.management_request import ManagementRequestError, decode_management_request
from nora.web.management_roster_request import RosterRequest


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

    def can_resolve(self, account: Identifier, scope: Scope) -> bool:
        return account.platform in self.actor.sources and scope in self.scopes


class _ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    account: Identifier
    scope: Scope


def _reply(status: str, code: int, *, detail: str | None = None,
           person_id: str | None = None) -> web.Response:
    body = {"status": status}
    if detail is not None:
        body["detail"] = detail
    if person_id is not None:
        body["person_id"] = person_id
    return web.json_response(body, status=code, headers={"Cache-Control": "no-store"})


class _ManagementHTTP:
    def __init__(self, services: Any, credentials: tuple[ProvisioningCredential, ...],
                 authentication: AuthenticationVerifier | None) -> None:
        self._credentials = tuple(credentials)
        if not self._credentials and authentication is None:
            raise ValueError("management routes require provisioning credentials")
        if len({c.token for c in self._credentials}) != len(self._credentials):
            raise ValueError("provisioning credentials cannot share a token")
        if len({c.actor.service_id for c in self._credentials}) != len(self._credentials):
            raise ValueError("provisioning service identities must be unique")
        self._services = services
        self._authentication = authentication
        self._human = (HumanManagement(services.bindings, services.registry,
                                      services.registration, services.authority)
                       if authentication is not None else None)
        self._identity = IdentityResolver(services.bindings, services.registry, services.topology)

    def _credential(self, request: web.Request) -> ProvisioningCredential | None:
        authorization = request.headers.get("Authorization", "").encode()
        return next((
            c for c in self._credentials
            if hmac.compare_digest(authorization, f"Bearer {c.token}".encode())
        ), None)

    async def _scope_refusal(self, scope: Scope) -> web.Response | None:
        try:
            exists = await self._services.topology.scope_exists(scope)
        except Exception:  # noqa: BLE001 — no authority answer means unavailable
            return _reply("unavailable", 503)
        if not isinstance(exists, bool):
            return _reply("unavailable", 503)
        return None if exists else _reply("denied", 403)

    async def execute(self, request: web.Request) -> web.Response:
        credential = self._credential(request)
        if credential is None:
            return await self._execute_human(request)
        try:
            operation = decode_management_request(
                await request.read(), actor=credential.actor,
            )
        except ManagementRequestError:
            return _reply("invalid_request", 400)
        if not credential.admits(operation):
            return _reply("denied", 403)
        scope = (operation.payload.scope if isinstance(operation.payload, AccountRegistration)
                 else operation.target.scope)
        if scope is None:
            return _reply("invalid_request", 400)
        refusal = await self._scope_refusal(scope)
        if refusal is not None:
            return refusal
        if operation.kind == "register_account":
            outcome = await self._services.registration.apply(operation)
        else:
            outcome = await self._services.authority.apply(operation)
        status = outcome.status.value
        return _reply(status, {"complete": 200, "denied": 403,
                               "unavailable": 503, "incomplete": 409}[status],
                      detail=outcome.detail)

    async def _execute_human(self, request: web.Request) -> web.Response:
        if self._authentication is None or self._human is None:
            return _reply("denied", 401)
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Nora-Authentication "):
            return _reply("denied", 401)
        raw = await request.read()
        try:
            authentication = self._authentication.verify(
                authorization.removeprefix("Nora-Authentication "),
                method=request.method, path=request.raw_path, body=raw)
            if authentication.purpose != "session" or authentication.binding is None:
                raise AuthenticationRefusedError("a retained session is required")
        except AuthenticationRefusedError:
            return _reply("denied", 401)
        try:
            operation = decode_management_request(
                raw, operator_person=authentication.binding.person_id)
        except ManagementRequestError:
            return _reply("invalid_request", 400)
        outcome = await self._human.apply(operation, authentication)
        status = outcome.status.value
        return _reply(status, {"complete": 200, "denied": 403,
                               "unavailable": 503, "incomplete": 409}[status],
                      detail=outcome.detail)

    async def provision_roster(self, request: web.Request) -> web.Response:
        credential = self._credential(request)
        if credential is None:
            return _reply("denied", 401)
        try:
            roster = RosterRequest.model_validate_json(await request.read())
        except (ValueError, TypeError):
            return _reply("invalid_request", 400)
        if (roster.scope not in credential.scopes or roster.platform not in credential.actor.sources
                or (roster.audience == "collection" and roster.scope.kind is ScopeKind.ROOM)):
            return _reply("denied", 403)
        if roster.complete and any(m.classification == "unknown" for m in roster.members):
            return _reply("unavailable", 503)
        services = self._services
        outcome = await RosterProvisioning(services.bindings, services.registry, services.topology,
            services.registration, services.authority).apply(
                operation_id=roster.operation_id, actor=credential.actor, scope=roster.scope,
                collection=roster.collection, complete_roster=roster.complete,
                members=(m.account for m in roster.members if m.classification == "person"))
        return _reply(outcome.status.value, {"complete": 200, "denied": 403,
            "unavailable": 503, "incomplete": 409}[outcome.status.value], detail=outcome.detail)

    async def resolve(self, request: web.Request) -> web.Response:
        credential = self._credential(request)
        if credential is None:
            return _reply("denied", 401)
        try:
            query = _ResolveRequest.model_validate_json(
                await request.read(), strict=True, extra="forbid",
            )
        except (ValidationError, ValueError, TypeError):
            return _reply("invalid_request", 400)
        if not credential.can_resolve(query.account, query.scope):
            return _reply("denied", 403)
        result = await self._identity.resolve(query.account, scope=query.scope)
        return _reply(
            result.status.value,
            {ResolutionStatus.OK: 200, ResolutionStatus.ABSENT: 404,
             ResolutionStatus.DENIED: 403, ResolutionStatus.UNAVAILABLE: 503}[result.status],
            person_id=result.person_id,
        )


def register_management_routes(
    app: web.Application, *, services: Any,
    credentials: tuple[ProvisioningCredential, ...],
    authentication: AuthenticationVerifier | None = None,
) -> None:
    """Add the authenticated private boundary over the selected service graph."""
    handlers = _ManagementHTTP(services, credentials, authentication)
    app.router.add_post("/app/v1/management/operations", handlers.execute)
    app.router.add_post("/app/v1/management/accounts/resolve", handlers.resolve)
    app.router.add_post("/app/v1/management/rosters", handlers.provision_roster)
