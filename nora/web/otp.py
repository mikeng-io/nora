"""`register_otp_routes` — the one-time code API (`nora.app.otp`).

    POST /app/v1/otp/request  {"identifier", "platform", "purpose"} -> 202 {"challengeId"}
    POST /app/v1/otp/verify   {"challengeId", "code"}               -> 200 {"personId"} | 401

**A bearer token, unlike every other `/app/v1` route.** Those trust the private
docker network and the portal proxy in front of them, which is enough for reads
the portal already scoped to its viewer. These two hand out a `personId` for a
code, which is a sign-in, so a caller must hold the token (`[otp]
api_token_env`), compared in constant time.

`request` answers 202 with a challenge id whether or not anything was sent (see
`nora.app.otp` for why). `verify` answers 401 with one fixed body for every
refusal, so a wrong code, an expired one and an unknown id cannot be told apart.
A malformed body is a 400: that is the caller's bug, not a guess.

Joins the health app like `register_web_routes` and `register_voice`, so it
needs no second port.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from aiohttp import web

log = logging.getLogger("nora.web.otp")

_REFUSED = {"error": "refused"}


def register_otp_routes(app: web.Application, *, otp: Any, api_token: str) -> None:
    """`otp` is a `nora.app.otp.OtpService` (duck-typed: `nora.web` does not
    import the application layer). `api_token` must be non-empty; composition
    refuses to start without it."""
    if not api_token:
        raise ValueError("register_otp_routes needs a non-empty api_token")
    expected = f"Bearer {api_token}".encode()

    def _authorized(request: web.Request) -> bool:
        given = request.headers.get("Authorization", "").encode()
        return hmac.compare_digest(given, expected)

    async def _body(request: web.Request) -> dict[str, Any] | None:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return None
        return body if isinstance(body, dict) else None

    async def request_code(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await _body(request) or {}
        identifier = body.get("identifier")
        platform = body.get("platform")
        purpose = body.get("purpose", "sign_in")
        if not all(isinstance(f, str) and f for f in (identifier, platform, purpose)):
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            challenge_id = await otp.request(
                identifier=identifier, platform=platform, purpose=purpose,
            )
        except ValueError:
            return web.json_response({"error": "bad_request"}, status=400)
        return web.json_response({"challengeId": challenge_id}, status=202)

    async def verify_code(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await _body(request) or {}
        challenge_id, code = body.get("challengeId"), body.get("code")
        if not isinstance(challenge_id, str) or not isinstance(code, str):
            return web.json_response({"error": "bad_request"}, status=400)
        person_id = await otp.verify(challenge_id=challenge_id, code=code)
        if person_id is None:
            return web.json_response(_REFUSED, status=401)
        return web.json_response({"personId": person_id})

    app.router.add_post("/app/v1/otp/request", request_code)
    app.router.add_post("/app/v1/otp/verify", verify_code)
