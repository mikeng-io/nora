"""Holder-facing link routes — the private portal API for `request_link`,
view, decisions and `unlink` (`docs/design/people/README.md`).

Every handler authenticates the request-bound assertion through the shared
verifier; the body carries the operation's OWN fields only — never an actor,
an account or a proof, and the strict body models refuse any field that
would smuggle one. A `session` assertion reaches create/view/unlink; a
decision requires `sign_in` (a fresh provider re-authentication — a
retained session is not consent).
"""

from __future__ import annotations

from datetime import UTC
from typing import Literal, TypeVar

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nora.domain.authentication import AccountAuthentication
from nora.domain.identity import Identifier
from nora.domain.managed import Outcome, OutcomeStatus
from nora.people import HolderLinkService, HolderLinkView
from nora.web.management_auth import AuthenticationVerifier, verified_authentication

_HEADERS = {"Cache-Control": "no-store"}

_STATUS_CODE = {
    OutcomeStatus.COMPLETE: 200,
    OutcomeStatus.DENIED: 403,
    OutcomeStatus.UNAVAILABLE: 503,
    OutcomeStatus.INCOMPLETE: 409,
}


class _LinkRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(min_length=1, pattern=r"\S")
    receiving: Identifier
    surviving_person: str = Field(min_length=1, pattern=r"\S")


class _DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str = Field(min_length=1, pattern=r"\S")
    action: Literal["approve", "refuse", "cancel"]
    nonce: str = Field(min_length=1, repr=False)


class _UnlinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str = Field(min_length=1, pattern=r"\S")
    expected_person: str = Field(min_length=1, pattern=r"\S")


def _ident_json(account: Identifier) -> dict[str, str]:
    return {"platform": account.platform, "kind": account.kind,
            "value": account.value}


def _view_json(view: HolderLinkView) -> dict[str, object]:
    """Serialize only the caller's side and its private consent challenge."""
    return {
        "request_id": view.request_id,
        "side": view.side.value,
        "state": view.state.value,
        "pair": {
            "initiating": _ident_json(view.pair.initiating),
            "receiving": _ident_json(view.pair.receiving),
        },
        "surviving_person": view.surviving_person,
        "created_at": view.created_at.astimezone(UTC).isoformat(),
        "expires_at": view.expires_at.astimezone(UTC).isoformat(),
        "approved": sorted(side.value for side in view.approved),
        "challenge": {
            "nonce": view.nonce,
            "approve": f"!!!link approve {view.request_id} {view.nonce}",
            "refuse": f"!!!link refuse {view.request_id} {view.nonce}",
        },
    }


def _reply(body: dict[str, object], outcome: Outcome | None = None) -> web.Response:
    if outcome is None:
        return web.json_response({"status": "ok", **body}, headers=_HEADERS)
    name = "ok" if outcome.status is OutcomeStatus.COMPLETE else outcome.status.value
    return web.json_response(
        {"status": name, **({"detail": outcome.detail} if outcome.detail else {}),
         **body},
        status=_STATUS_CODE[outcome.status], headers=_HEADERS)


_Body = TypeVar("_Body", bound=BaseModel)


def register_holder_routes(
    app: web.Application, *, holder: HolderLinkService,
    verifier: AuthenticationVerifier,
) -> None:
    """Mount `/app/v1/holder/…` — the holder's private link surface."""

    async def authenticate(
        request: web.Request, *, purposes: frozenset[str]
    ) -> tuple[AccountAuthentication, bytes] | web.Response:
        return await verified_authentication(
            request, verifier=verifier, purposes=purposes, headers=_HEADERS)

    def _parse(raw: bytes, model: type[_Body]) -> _Body | web.Response:
        try:
            return model.model_validate_json(raw)
        except ValidationError:
            return web.json_response({"status": "denied"}, status=400,
                                     headers=_HEADERS)

    async def request_link(request: web.Request) -> web.Response:
        checked = await authenticate(
            request, purposes=frozenset({"session", "sign_in"}))
        if isinstance(checked, web.Response):
            return checked
        authentication, raw = checked
        body = _parse(raw, _LinkRequestBody)
        if isinstance(body, web.Response):
            return body
        result = await holder.request_link(
            authentication, request_id=body.request_id,
            receiving=body.receiving, surviving_person=body.surviving_person)
        if isinstance(result, Outcome):
            return _reply({}, result)
        return _reply({"request": _view_json(result)})

    async def view_link(request: web.Request) -> web.Response:
        checked = await authenticate(
            request, purposes=frozenset({"session", "sign_in"}))
        if isinstance(checked, web.Response):
            return checked
        authentication, _raw = checked
        result = await holder.view_link(
            authentication, request_id=request.match_info["request_id"])
        if isinstance(result, Outcome):
            return _reply({}, result)
        return _reply({"request": _view_json(result)})

    async def decide(request: web.Request) -> web.Response:
        checked = await authenticate(request, purposes=frozenset({"sign_in"}))
        if isinstance(checked, web.Response):
            return checked
        authentication, raw = checked
        body = _parse(raw, _DecisionBody)
        if isinstance(body, web.Response):
            return body
        result = await holder.decide(
            authentication, operation_id=body.operation_id,
            request_id=request.match_info["request_id"],
            action=body.action, nonce=body.nonce)
        body_out: dict[str, object] = {}
        if result.state is not None:
            body_out["state"] = result.state.value
        return _reply(body_out, result.outcome)

    async def unlink(request: web.Request) -> web.Response:
        checked = await authenticate(
            request, purposes=frozenset({"session", "sign_in"}))
        if isinstance(checked, web.Response):
            return checked
        authentication, raw = checked
        body = _parse(raw, _UnlinkBody)
        if isinstance(body, web.Response):
            return body
        return _reply({}, await holder.unlink(
            authentication, operation_id=body.operation_id,
            expected_person=body.expected_person))

    app.router.add_post("/app/v1/holder/link-requests", request_link)
    app.router.add_get("/app/v1/holder/link-requests/{request_id}", view_link)
    app.router.add_post(
        "/app/v1/holder/link-requests/{request_id}/decisions", decide)
    app.router.add_post("/app/v1/holder/unlink", unlink)
