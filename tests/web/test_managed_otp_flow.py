"""A real delivered-code API receipt reaches the account-exact session exchange."""

import hashlib
import re
import time
from dataclasses import replace

import jwt
import pytest
from aiohttp import web

from nora.people import AccountSessions
from nora.web.management_auth import AuthenticationIssuer, AuthenticationVerifier
from nora.web.management_sessions import register_session_routes
from nora.web.otp import register_otp_routes
from tests.infra.test_managed_otp import PHONE
from tests.infra.test_managed_otp import world as world

KEY = b"generated-independent-authentication-gateway-key"
TOKEN = "generated-private-code-service-key"  # noqa: S105 — generated local fixture
PATH = "/app/v1/management/auth/exchange"


@pytest.mark.parametrize("change,expected", [(False, 200), (True, 403)])
async def test_code_receipt_cannot_change_identity_between_redemption_and_exchange(
    world, aiohttp_client, change, expected,
):
    service, _, _, adapter, bindings, registry = world
    app = web.Application()
    register_otp_routes(app, otp=service, api_token=TOKEN)
    verifier = AuthenticationVerifier([AuthenticationIssuer(
        "generated-gateway", KEY, {"signal_code": "nora-otp"})])
    register_session_routes(app, sessions=AccountSessions(bindings, registry), verifier=verifier)
    client = await aiohttp_client(app)
    headers = {"Authorization": "Bearer generated-private-code-service-key"}
    requested = await client.post("/app/v1/otp/request", headers=headers,
        json={"identifier": PHONE.value, "platform": "signal", "purpose": "sign_in"})
    assert requested.status == 202
    challenge = (await requested.json())["challengeId"]
    code = re.search(r"\b(\d{6})\b", adapter.sent[-1][1]).group(1)
    verified = await client.post("/app/v1/otp/verify", headers=headers,
        json={"challengeId": challenge, "code": code, "purpose": "sign_in"})
    assert verified.status == 200
    receipt = (await verified.json())["managedIdentity"]
    assert receipt["authentication"]["sessionId"] == "otp:" + challenge
    assert receipt["authentication"]["account"]["value"] == PHONE.value
    if change:
        account = bindings.accounts["generated-account"]
        bindings.accounts[account.account_id] = replace(account, revision=account.revision + 1)
    authentication = receipt["authentication"]
    now = int(time.time())
    body = b"{}"
    token = jwt.encode({"iss": "generated-gateway", "aud": "nora-managed-api",
        "sub": authentication["sessionId"], "jti": "generated-code-exchange",
        "iat": now, "exp": now + 30, "account": authentication["account"],
        "authentication_method": authentication["method"],
        "proof_issuer": authentication["proofIssuer"],
        "authenticated_at": authentication["authenticatedAt"],
        "purpose": "sign_in", "binding": receipt["binding"],
        "method": "POST", "path": PATH, "body_sha256": hashlib.sha256(body).hexdigest()},
        KEY, algorithm="HS256", headers={"kid": "generated-gateway", "typ": "nora.authn+jwt"})
    response = await client.post(PATH, data=body,
        headers={"Authorization": "Nora-Authentication " + token})
    assert response.status == expected
    if not change:
        assert (await response.json())["binding"] == receipt["binding"]
