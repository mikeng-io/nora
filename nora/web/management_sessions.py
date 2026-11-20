"""The same managed authentication check serves sign-in and every retained session."""

from dataclasses import asdict

from aiohttp import web

from nora.domain.managed import Verdict
from nora.people import AccountSessions
from nora.web.management_auth import AuthenticationRefusedError, AuthenticationVerifier


def register_session_routes(
    app: web.Application, *, sessions: AccountSessions, verifier: AuthenticationVerifier,
) -> None:
    async def check(request: web.Request) -> web.Response:
        headers = {"Cache-Control": "no-store"}
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Nora-Authentication "):
            return web.json_response({"status": "denied"}, status=401, headers=headers)
        try:
            authentication = verifier.verify(authorization.removeprefix("Nora-Authentication "),
                method=request.method, path=request.raw_path, body=await request.read())
            purpose = "sign_in" if request.path.endswith("/exchange") else "session"
            if authentication.purpose != purpose:
                raise AuthenticationRefusedError("unexpected authentication purpose")
        except AuthenticationRefusedError:
            return web.json_response({"status": "denied"}, status=401, headers=headers)
        result = await sessions.check(authentication)
        principal = result.principal
        if result.verdict is Verdict.ALLOWED and principal is not None:
            return web.json_response({"status": "ok", "binding": asdict(principal.binding),
                "display_name": principal.display_name}, headers=headers)
        code = 403 if result.verdict is Verdict.DENIED else 503
        return web.json_response({"status": "denied" if code == 403 else "unavailable"},
                                 status=code, headers=headers)

    app.router.add_post("/app/v1/management/auth/exchange", check)
    app.router.add_post("/app/v1/management/auth/check", check)
