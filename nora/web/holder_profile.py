"""The holder's own profile surface — `POST /app/v1/holder/profile`
(`docs/design/managed-identity-access/management.md`).

One route, one effect: the authenticated holder renames their own
canonical person. The body carries ONLY the intended display name and the
caller's operation id — actor, target and account all derive from the
verified session binding, and the strict body model refuses any field that
would smuggle one. Scoped nicknames have no holder surface.
"""

from __future__ import annotations

from typing import TypeVar

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nora.domain.managed import Outcome, OutcomeStatus
from nora.people import ProfileService
from nora.web.management_auth import AuthenticationVerifier, verified_authentication

_HEADERS = {"Cache-Control": "no-store"}

_STATUS_CODE = {
    OutcomeStatus.COMPLETE: 200,
    OutcomeStatus.DENIED: 403,
    OutcomeStatus.UNAVAILABLE: 503,
    OutcomeStatus.INCOMPLETE: 409,
}


class _ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str = Field(min_length=1, pattern=r"\S")
    display_name: str = Field(min_length=1, pattern=r"\S")


_Body = TypeVar("_Body", bound=BaseModel)


def _parse(raw: bytes, model: type[_Body]) -> _Body | web.Response:
    try:
        return model.model_validate_json(raw)
    except ValidationError:
        return web.json_response({"status": "denied"}, status=400,
                                 headers=_HEADERS)


def _reply(outcome: Outcome) -> web.Response:
    name = "ok" if outcome.status is OutcomeStatus.COMPLETE else outcome.status.value
    return web.json_response(
        {"status": name, **({"detail": outcome.detail} if outcome.detail else {})},
        status=_STATUS_CODE[outcome.status], headers=_HEADERS)


def register_profile_routes(
    app: web.Application, *, profile: ProfileService,
    verifier: AuthenticationVerifier,
) -> None:
    """Mount `POST /app/v1/holder/profile` — the holder's global name."""

    async def update_profile(request: web.Request) -> web.Response:
        checked = await verified_authentication(
            request, verifier=verifier,
            purposes=frozenset({"session", "sign_in"}), headers=_HEADERS)
        if isinstance(checked, web.Response):
            return checked
        authentication, raw = checked
        body = _parse(raw, _ProfileBody)
        if isinstance(body, web.Response):
            return body
        return _reply(await profile.update_display_name(
            authentication, operation_id=body.operation_id,
            display_name=body.display_name))

    app.router.add_post("/app/v1/holder/profile", update_profile)
