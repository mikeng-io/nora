"""Only the configured authentication gateway can bind an account to this request."""

import hashlib
import time
from dataclasses import asdict
from uuid import uuid4

import jwt
import pytest

from nora.web.management_auth import (
    ASSERTION_AUDIENCE,
    ASSERTION_TYPE,
    AuthenticationIssuer,
    AuthenticationRefusedError,
    AuthenticationVerifier,
)

KEY = b"generated-authentication-key-for-local-contracts"
ISSUER = AuthenticationIssuer("generated-portal", KEY, {"discord_oauth": "generated-oauth-client"})
BODY = b"{}"
PATH = "/app/v1/management/auth/exchange"


def claims(**changes):
    now = int(time.time())
    value = {"iss": ISSUER.name, "aud": ASSERTION_AUDIENCE, "sub": str(uuid4()),
        "jti": str(uuid4()), "iat": now, "exp": now + 30,
        "account": {"platform": "discord", "kind": "user_id", "value": "generated-account"},
        "authentication_method": "discord_oauth", "proof_issuer": "generated-oauth-client",
        "authenticated_at": now, "purpose": "sign_in", "method": "POST", "path": PATH,
        "body_sha256": hashlib.sha256(BODY).hexdigest()}
    value.update(changes)
    return value


def signed(value=None, *, key=KEY, header=None):
    return jwt.encode(claims() if value is None else value, key, algorithm="HS256",
        headers={"kid": ISSUER.name, "typ": ASSERTION_TYPE, **(header or {})})


def verify(token, *, body=BODY, path=PATH, method="POST"):
    return AuthenticationVerifier([ISSUER]).verify(token, method=method, path=path, body=body)


def test_authentication_retains_exact_account_and_provider_provenance():
    authentication = verify(signed())
    assert authentication.issuer == ISSUER.name
    assert authentication.account.value == "generated-account"
    assert authentication.proof_issuer == "generated-oauth-client"
    assert authentication.binding is None
    assert authentication.authenticated_at.tzinfo is not None


@pytest.mark.parametrize("change", [
    {"aud": "another-api"}, {"iss": "untrusted"}, {"role": "admin"},
    {"actor": {"kind": "installation"}}, {"exp": 1}, {"iat": 1},
    {"iat": True}, {"proof_issuer": "another-oauth-client"},
    {"authenticated_at": 1}, {"purpose": "consent"},
    {"account": {"platform": "telegram", "kind": "user_id", "value": "generated-account"}},
    {"binding": {"person_id": "generated-person", "person_revision": "1",
                 "account_id": "generated-binding", "account_revision": "1"}},
])
def test_signed_but_invalid_or_privileged_claims_do_not_authenticate(change):
    with pytest.raises(AuthenticationRefusedError):
        verify(signed(claims(**change)))


@pytest.mark.parametrize("change", [{"method": "GET"}, {"path": PATH + "/other"},
                                    {"body": b'{"actor":"admin"}'}])
def test_an_authentication_assertion_cannot_move_to_another_request(change):
    with pytest.raises(AuthenticationRefusedError):
        verify(signed(), **change)


@pytest.mark.parametrize("header", [{"kid": "unknown"}, {"typ": "JWT"}, {"crit": ["b64"]}])
def test_unexpected_signing_context_is_refused(header):
    with pytest.raises(AuthenticationRefusedError):
        verify(signed(header=header))


def test_service_credential_or_wrong_key_cannot_sign_a_human_assertion():
    with pytest.raises(AuthenticationRefusedError):
        verify(signed(key=b"generated-different-service-credential"))
    with pytest.raises(AuthenticationRefusedError):
        verify("generated-provisioning-bearer")


def test_a_retained_session_requires_its_original_binding():
    with pytest.raises(AuthenticationRefusedError):
        verify(signed(claims(purpose="session")))
    bound = {"person_id": "generated-person", "person_revision": "1",
             "account_id": "generated-binding", "account_revision": "2"}
    authentication = verify(signed(claims(purpose="session", binding=bound)))
    assert asdict(authentication.binding) == bound
