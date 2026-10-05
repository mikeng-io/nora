"""`register_otp_routes`: a bearer token, one answer for every refusal."""

from __future__ import annotations

import pytest
from aiohttp import web

from nora.web.otp import register_otp_routes

TOKEN = "t0ken"  # noqa: S105 — a test fixture, not a secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _Otp:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str]] = []

    async def request(self, *, identifier: str, platform: str, purpose: str) -> str:
        if platform not in ("signal", "whatsapp"):
            raise ValueError(platform)
        self.requests.append((identifier, platform, purpose))
        return "c-1"

    async def verify(self, *, challenge_id: str, code: str) -> str | None:
        return "miles" if (challenge_id, code) == ("c-1", "123456") else None


@pytest.fixture
async def client(aiohttp_client):
    app = web.Application()
    otp = _Otp()
    register_otp_routes(app, otp=otp, api_token=TOKEN)
    client = await aiohttp_client(app)
    client.otp = otp
    return client


async def test_without_the_token_nothing_is_requested_or_checked(client):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        r = await client.post("/app/v1/otp/request", headers=headers,
                              json={"identifier": "miles", "platform": "signal"})
        assert r.status == 401
        r = await client.post("/app/v1/otp/verify", headers=headers,
                              json={"challengeId": "c-1", "code": "123456"})
        assert r.status == 401 and "personId" not in await r.json()
    assert client.otp.requests == []


async def test_a_request_answers_a_challenge_and_defaults_to_sign_in(client):
    r = await client.post("/app/v1/otp/request", headers=AUTH,
                          json={"identifier": "miles", "platform": "signal"})
    assert r.status == 202
    assert await r.json() == {"challengeId": "c-1"}
    assert client.otp.requests == [("miles", "signal", "sign_in")]


async def test_a_malformed_request_is_a_400(client):
    for body in ({"identifier": "miles"}, {"identifier": "miles", "platform": "telegram"}):
        r = await client.post("/app/v1/otp/request", headers=AUTH, json=body)
        assert r.status == 400


async def test_verify_answers_the_person_or_one_fixed_refusal(client):
    r = await client.post("/app/v1/otp/verify", headers=AUTH,
                          json={"challengeId": "c-1", "code": "123456"})
    assert (r.status, await r.json()) == (200, {"personId": "miles"})
    r = await client.post("/app/v1/otp/verify", headers=AUTH,
                          json={"challengeId": "c-1", "code": "000000"})
    assert (r.status, await r.json()) == (401, {"error": "refused"})


def test_the_routes_refuse_to_mount_without_a_token():
    with pytest.raises(ValueError):
        register_otp_routes(web.Application(), otp=_Otp(), api_token="")
