"""Authenticate every portal data route against the current exact account binding."""

import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

from nora.domain.managed import Verdict
from nora.domain.managed_ports import BindingStore, IdentityRegistry
from nora.people import AccountSessions, PortalScopes
from nora.web.managed_scope import scoped_context
from nora.web.management_auth import AuthenticationRefusedError, AuthenticationVerifier


def register_managed_viewer(app: web.Application, *, bindings: BindingStore,
                            registry: IdentityRegistry, scopes: PortalScopes,
                            verifier: AuthenticationVerifier | None) -> None:
    owned = frozenset(route for route in app.router.routes() if route.handler.__module__ in {
        "nora.web.api", "nora.web.document_history", "nora.web.artifacts",
    })
    sessions = AccountSessions(bindings, registry)

    def refusal(code: int) -> web.Response:
        return web.json_response({"status": "unavailable" if code == 503 else "denied"},
                                 status=code, headers={"Cache-Control": "no-store"})

    async def verified(
        request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if verifier is None:
            return refusal(503)
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Nora-Authentication "):
            return refusal(401)
        try:
            authentication = verifier.verify(
                authorization.removeprefix("Nora-Authentication "), method=request.method,
                path=request.raw_path, body=await request.read())
            if authentication.purpose != "session" or authentication.binding is None:
                raise AuthenticationRefusedError("a retained session is required")
        except AuthenticationRefusedError:
            return refusal(401)
        result = await sessions.check(authentication)
        if result.verdict is Verdict.UNAVAILABLE:
            return refusal(503)
        principal = result.principal
        if principal is None:
            return refusal(403)
        viewers = request.query.getall("personId", [])
        if viewers and viewers != [principal.binding.person_id]:
            return refusal(403)
        context = await scoped_context(request, scopes=scopes, bindings=bindings,
                                       viewer=principal.binding.person_id)
        if context is None:
            return refusal(503)
        request["web_context"] = context
        request["web_principal"] = principal
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @web.middleware
    async def authenticate(
        request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if request.match_info.route not in owned:
            return await handler(request)
        if request.method in {"GET", "HEAD"}:
            return await verified(request, handler)
        try:
            async with bindings.mutation():
                return await verified(request, handler)
        except Exception as error:  # noqa: BLE001 — no unchecked writes during an authority outage
            logging.getLogger(__name__).error("managed write unavailable: %s", type(error).__name__)
            return refusal(503)

    app.middlewares.append(authenticate)
