"""`[identity]` configuration — backend selection for person binding and
scoped grants, and the private Ory service endpoints `managed` wires
against (`docs/design/managed-identity-access/`).

`file` (the default) is `identity.toml` + `policy.toml` exactly as today —
unchanged, hot-reloaded, and requiring no Ory service. `managed` selects
Kratos + Keto + PostgreSQL under the same ports; a backend change is a
boot-time decision — it builds a different object graph, so it is a
restart, never a hot reload.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nora.domain.authentication import AuthenticationMethod
from nora.domain.identity import id_kinds


class ManagedOryConfig(BaseModel):
    """`[identity.managed]` — the private Ory service endpoints a managed
    deployment wires against (`docs/design/managed-identity-access/`).

    Kratos's public listener is recorded for later session-facing flows; the
    managed backend speaks admin API only. Every URL names the service's
    PRIVATE listener — write and admin sides are never reachable from a
    browser — and credentials never appear inside a URL: a gated deployment
    names the env var holding the key (`*_env`, `api_key_env`-style), and
    `Config.credentials` resolves it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kratos_admin_url: str = ""
    kratos_public_url: str = ""
    keto_read_url: str = ""
    keto_write_url: str = ""
    # Env-var names for optional bearer keys on the Ory listeners — never
    # values. Empty means the listeners authenticate by network position
    # alone, which is the dev setup's shape.
    kratos_admin_key_env: str = ""
    keto_key_env: str = ""
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)


class ProvisioningScopeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["workspace", "unit", "room"]
    id: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _named_scope(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a provisioning scope requires an id")
        return value


class ProvisioningServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    sources: frozenset[str] = Field(min_length=1)
    scopes: tuple[ProvisioningScopeConfig, ...] = Field(min_length=1)

    @field_validator("sources")
    @classmethod
    def _known_sources(cls, values: frozenset[str]) -> frozenset[str]:
        if any(not id_kinds(value) for value in values):
            raise ValueError("provisioning sources must name known identity platforms")
        return values


class AuthenticationIssuerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    methods: dict[AuthenticationMethod, str] = Field(min_length=1)

    @field_validator("methods")
    @classmethod
    def _provider_provenance(cls, values: dict[AuthenticationMethod, str]
                             ) -> dict[AuthenticationMethod, str]:
        if any(not value.strip() for value in values.values()):
            raise ValueError("authentication methods require provider provenance")
        return values


class OnboardingCollectionConfig(BaseModel):
    """One operator-approved onboarding rule (`identity.md` «Platform
    discovery and membership»): `collection`'s member set flows into the
    named scope under the named provisioning service's authenticated
    identity. Nothing onboards implicitly — every rule is declared and its
    admission is the same service+scope check the roster endpoint applies.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    service: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    collection: str = Field(min_length=1)
    scope: ProvisioningScopeConfig
    audience: Literal["collection", "room"] = "collection"

    @field_validator("platform")
    @classmethod
    def _known_platform(cls, value: str) -> str:
        if not id_kinds(value):
            raise ValueError(
                f"unknown platform {value!r}: no id-kind vocabulary declared"
            )
        return value


class OnboardingConfig(BaseModel):
    """Managed-identity onboarding: which collection member sets feed which
    scopes through the provisioning boundary, and the reconciliation
    cadence. An absent section — or empty `collections` — keeps current
    behavior: no collection ever onboards implicitly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collections: tuple[OnboardingCollectionConfig, ...] = ()
    interval_seconds: float = Field(default=300.0, ge=15.0, le=86400.0)
    retry_seconds: float = Field(default=60.0, ge=5.0, le=86400.0)

    @field_validator("collections")
    @classmethod
    def _distinct_rules(
        cls, value: tuple[OnboardingCollectionConfig, ...]
    ) -> tuple[OnboardingCollectionConfig, ...]:
        names = [c.name for c in value]
        if len(names) != len(set(names)):
            raise ValueError("onboarding collection names must be distinct")
        keys = [(c.service, c.platform, c.collection) for c in value]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "duplicate onboarding collection: one service+platform+"
                "collection may carry only one rule"
            )
        return value


class IdentityBackendConfig(BaseModel):
    """`[identity]` — which backend owns person binding and scoped grants.

    `file` (the default) is `identity.toml` + `policy.toml` exactly as
    today — unchanged, hot-reloaded every 3s, and requiring no Ory service.
    `managed` selects Kratos + Keto + PostgreSQL under the same ports; it
    needs every managed URL before a deployment can be built. A backend
    change is a boot-time decision — it builds a different object graph, so
    it is a restart, never a hot reload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Literal["file", "managed"] = "file"
    managed: ManagedOryConfig = ManagedOryConfig()
    provisioning: dict[str, ProvisioningServiceConfig] = Field(default_factory=dict)
    authentication: dict[str, AuthenticationIssuerConfig] = Field(default_factory=dict)
    onboarding: OnboardingConfig = Field(default_factory=OnboardingConfig)

    @model_validator(mode="after")
    def _managed_needs_its_services(self) -> IdentityBackendConfig:
        if any(not name.strip() for name in {*self.provisioning, *self.authentication}):
            raise ValueError("management services require a nonblank identity")
        if self.backend != "managed" and self.onboarding.collections:
            raise ValueError(
                "[identity.onboarding] collections require backend = \"managed\""
            )
        for rule in self.onboarding.collections:
            service = self.provisioning.get(rule.service)
            if service is None:
                raise ValueError(
                    f"onboarding collection {rule.name!r} names a service not "
                    "declared in [identity.provisioning]"
                )
            if rule.platform not in service.sources:
                raise ValueError(
                    f"onboarding collection {rule.name!r}: service "
                    f"{rule.service!r} is not approved for platform "
                    f"{rule.platform!r}"
                )
            if rule.scope not in service.scopes:
                raise ValueError(
                    f"onboarding collection {rule.name!r}: service "
                    f"{rule.service!r} is not approved for scope "
                    f"{rule.scope.kind}={rule.scope.id!r}"
                )
            if (rule.audience == "collection") == (rule.scope.kind == "room"):
                raise ValueError(
                    f"onboarding collection {rule.name!r}: audience "
                    f"{rule.audience!r} requires scope.kind "
                    f"{'room' if rule.audience == 'room' else 'non-room'} — a "
                    "collection-wide roster can never grant room membership"
                )
        if self.backend != "managed":
            return self
        required = {
            "kratos_admin_url": self.managed.kratos_admin_url,
            "keto_read_url": self.managed.keto_read_url,
            "keto_write_url": self.managed.keto_write_url,
        }
        missing = [name for name, url in required.items() if not url.strip()]
        if missing:
            raise ValueError(
                f"[identity.managed] {missing} must name the private Ory "
                "listeners when backend = \"managed\""
            )
        for name, url in required.items():
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError(
                    f"[identity.managed] {name} must be an http(s) URL to the "
                    "service's private listener"
                )
            if parsed.username or parsed.password:
                raise ValueError(
                    f"[identity.managed] {name} carries credentials — name the "
                    "env var instead"
                )
        return self
