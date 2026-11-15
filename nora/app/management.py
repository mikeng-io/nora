"""Mount managed provisioning with validated scopes and resolved private keys."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from nora.app.management_auth import mount_authentication
from nora.app.startup import StartupError
from nora.config import Config
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.people import PortalScopes
from nora.web.managed_viewer import register_managed_viewer
from nora.web.management_api import ProvisioningCredential, register_management_routes


async def mount_management(app: web.Application, *, cfg: Config, services: Any) -> None:
    if cfg.identity.backend != "managed":
        return
    authentication = mount_authentication(app, cfg=cfg, services=services)
    register_managed_viewer(app, bindings=services.bindings, registry=services.registry,
        scopes=PortalScopes(services.bindings, services.topology, services.authority),
        verifier=authentication)
    if not cfg.identity.provisioning and authentication is None:
        return
    credentials = []
    private_values = cfg.credentials
    for name, setting in cfg.identity.provisioning.items():
        token = private_values.get(setting.token_env, "")
        if not token or token != token.strip():
            raise StartupError("managed provisioning credential is unavailable")
        scopes = frozenset(Scope(ScopeKind(scope.kind), scope.id) for scope in setting.scopes)
        for scope in scopes:
            if not await services.topology.scope_exists(scope):
                raise StartupError("managed provisioning scope is not in Nora's topology")
        credentials.append(ProvisioningCredential(
            actor=Actor(ActorKind.SERVICE, service_id=name, sources=setting.sources),
            token=token, scopes=scopes,
        ))
    try:
        register_management_routes(app, services=services, credentials=tuple(credentials),
                                   authentication=authentication)
    except ValueError:
        raise StartupError("managed provisioning credentials are invalid or ambiguous") from None
