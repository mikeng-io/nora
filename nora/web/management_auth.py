"""Request-bound assertions from a configured authentication gateway.

The gateway validates provider/code authentication; a provisioning bearer has
no signing authority. Assertions carry no role or permission claims, and their
captured binding is revalidated by the managed session use case on every call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

import jwt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nora.domain.authentication import (
    AccountAuthentication,
    AuthenticationMethod,
    SessionBinding,
)
from nora.domain.identity import Identifier

_PLATFORMS = {"discord_oauth": "discord", "telegram_oidc": "telegram",
              "signal_code": "signal", "whatsapp_code": "whatsapp"}
ASSERTION_AUDIENCE = "nora-managed-api"
ASSERTION_TYPE = "nora.authn+jwt"


class AuthenticationRefusedError(ValueError):
    """An assertion cannot authenticate this request; never quote its private content."""


@dataclass(frozen=True)
class AuthenticationIssuer:
    name: str
    key: bytes = field(repr=False)
    methods: Mapping[AuthenticationMethod, str]

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.key) < 32 or not self.methods:
            raise ValueError("authentication issuers require a name, strong key and methods")
        if any(method not in _PLATFORMS or not issuer.strip()
               for method, issuer in self.methods.items()):
            raise ValueError("authentication methods require known provider provenance")
        object.__setattr__(self, "methods", MappingProxyType(dict(self.methods)))


class _Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    person_id: str = Field(min_length=1, pattern=r"\S")
    person_revision: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    account_id: str = Field(min_length=1, pattern=r"\S")
    account_revision: str = Field(pattern=r"^(0|[1-9][0-9]*)$")


class _Claims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    iss: str
    aud: Literal["nora-managed-api"]
    sub: str = Field(min_length=1, pattern=r"\S")
    jti: str = Field(min_length=1, pattern=r"\S")
    iat: int
    exp: int
    account: Identifier
    authentication_method: AuthenticationMethod
    proof_issuer: str = Field(min_length=1, pattern=r"\S")
    authenticated_at: int
    purpose: Literal["sign_in", "session"]
    binding: _Binding | None = None
    method: str
    path: str
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _coherent(self) -> _Claims:
        if not 0 < self.exp - self.iat <= 60:
            raise ValueError("authentication assertions expire within one minute")
        age = self.iat - self.authenticated_at
        if not 0 <= age <= (60 if self.purpose == "sign_in" else 30 * 86400):
            raise ValueError("authentication is not current for this purpose")
        if (self.purpose == "session") != (self.binding is not None):
            raise ValueError("a session retains its original verified binding")
        if self.account.platform != _PLATFORMS[self.authentication_method]:
            raise ValueError("authentication method does not prove this account platform")
        return self


class AuthenticationVerifier:
    def __init__(self, issuers: Sequence[AuthenticationIssuer]) -> None:
        self._issuers = {issuer.name: issuer for issuer in issuers}
        if (not issuers or len(self._issuers) != len(issuers)
                or len({issuer.key for issuer in issuers}) != len(issuers)):
            raise ValueError("authentication issuers and keys must be distinct")

    def verify(self, token: str, *, method: str, path: str, body: bytes) -> AccountAuthentication:
        try:
            header = jwt.get_unverified_header(token)
            if (set(header) != {"alg", "typ", "kid"} or header.get("alg") != "HS256"
                    or header.get("typ") != ASSERTION_TYPE):
                raise ValueError("unexpected assertion header")
            issuer = self._issuers[header["kid"]]
            decoded = jwt.decode(token, issuer.key, algorithms=["HS256"],
                issuer=issuer.name, audience=ASSERTION_AUDIENCE, leeway=2,
                options={"require": ["iss", "aud", "sub", "jti", "iat", "exp"], "strict_aud": True})
            claims = _Claims.model_validate_json(json.dumps(decoded), strict=True)
            if (claims.method != method or claims.path != path
                    or claims.body_sha256 != hashlib.sha256(body).hexdigest()
                    or issuer.methods.get(claims.authentication_method) != claims.proof_issuer):
                raise ValueError("assertion does not authenticate this request")
            binding = (None if claims.binding is None
                       else SessionBinding(**claims.binding.model_dump()))
            return AccountAuthentication(claims.iss, claims.sub, claims.account,
                claims.authentication_method, claims.proof_issuer,
                datetime.fromtimestamp(claims.authenticated_at, UTC), claims.purpose, binding)
        except (jwt.PyJWTError, KeyError, ValueError, TypeError, OverflowError) as error:
            raise AuthenticationRefusedError("authentication assertion refused") from error
