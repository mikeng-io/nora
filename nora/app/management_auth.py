"""Compose authentication issuers without sharing service-provisioning credentials."""

from typing import Any

from aiohttp import web

from nora.app.startup import StartupError
from nora.config import Config
from nora.people import AccountSessions
from nora.web.management_auth import AuthenticationIssuer, AuthenticationVerifier
from nora.web.management_sessions import register_session_routes


def mount_authentication(
    app: web.Application, *, cfg: Config, services: Any,
) -> AuthenticationVerifier | None:
    if not cfg.identity.authentication:
        return None
    credentials = cfg.credentials
    service_keys = {setting.token_env for setting in cfg.identity.provisioning.values()}
    service_keys.update({cfg.identity.managed.kratos_admin_key_env,
                         cfg.identity.managed.keto_key_env, cfg.otp.api_token_env})
    service_values = {credentials.get(name, "").encode() for name in service_keys if name}
    issuers = []
    try:
        for name, setting in cfg.identity.authentication.items():
            key = credentials.get(setting.key_env, "").encode()
            if key in service_values:
                raise ValueError("authentication and service credentials must be separate")
            issuers.append(AuthenticationIssuer(name, key, setting.methods))
        verifier = AuthenticationVerifier(issuers)
    except ValueError:
        raise StartupError(
            "managed authentication issuer configuration is unavailable or unsafe"
        ) from None
    register_session_routes(app, sessions=AccountSessions(services.bindings, services.registry),
                             verifier=verifier)
    return verifier
