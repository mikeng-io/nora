"""The pinned Kratos v26.2.0 admin surface Nora uses (`identity.md`).

Kratos owns the managed identity record and its lifecycle — `state` gates
real session checks — while Nora's binding store owns which platform
accounts a person holds. Traits carry only descriptive fields
(`deploy/ory/kratos/person.schema.json`): nothing here is ownership evidence.

`external_id` is the correlation key: the service creates with the canonical
`person_id`, and a 409 answers by read-back (`GET /admin/identities/by/
external/{id}`) — the existing identity is evidence of the person, never of
an account binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from nora.domain.managed import (
    CheckResult,
    Outcome,
    Verdict,
    check_denied,
    check_unavailable,
    complete,
    denied,
    unavailable,
    unknown_scope,
)
from nora.infra.ory.http import HttpStatus, request


@dataclass(frozen=True)
class KratosIdentity:
    """The parts of a Kratos identity Nora reads — id, correlation key,
    state and the descriptive traits."""

    identity_id: str
    external_id: str | None
    state: str
    traits: dict[str, Any]


def _parse_identity(body: Any) -> KratosIdentity | None:
    if not isinstance(body, dict):
        return None
    identity_id = body.get("id")
    state = body.get("state")
    if not isinstance(identity_id, str) or not isinstance(state, str):
        return None
    traits = body.get("traits")
    external_id = body.get("external_id")
    return KratosIdentity(
        identity_id=identity_id,
        external_id=external_id if isinstance(external_id, str) else None,
        state=state,
        traits=traits if isinstance(traits, dict) else {},
    )


@dataclass(frozen=True)
class KratosClient:
    """Admin API calls on the private admin listener — creation keyed by
    `external_id`, state, session teardown. Every transport failure is an
    `Outcome`/`CheckResult` value; nothing here raises for a remote answer.
    """

    session: aiohttp.ClientSession
    admin_url: str
    timeout_seconds: float = 5.0
    api_key: str | None = None
    schema_id: str = "nora_person"

    async def create_identity(
        self, *, external_id: str, traits: dict[str, Any]
    ) -> tuple[Outcome, KratosIdentity | None]:
        """`POST /admin/identities`. A 409 is `denied` ("conflict") — the
        caller then reads the identity back by `external_id` rather than
        trusting the create path's failure shape."""
        result = await request(
            self.session,
            "POST",
            f"{self.admin_url}/admin/identities",
            json_body={
                "schema_id": self.schema_id,
                "traits": traits,
                "external_id": external_id,
                "state": "active",
            },
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={201: HttpStatus.OK, 409: HttpStatus.CONFLICT},
        )
        if result.status is HttpStatus.OK:
            identity = _parse_identity(result.body)
            if identity is None:
                return unavailable("malformed-body"), None
            return complete(), identity
        if result.status is HttpStatus.CONFLICT:
            return denied("external-id-conflict"), None
        return unavailable(result.detail), None

    async def get_identity(self, identity_id: str) -> tuple[Outcome, KratosIdentity | None]:
        """`GET /admin/identities/{id}` — `denied("absent")` on 404."""
        result = await request(
            self.session,
            "GET",
            f"{self.admin_url}/admin/identities/{identity_id}",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK, 404: HttpStatus.ABSENT},
        )
        if result.status is HttpStatus.OK:
            identity = _parse_identity(result.body)
            if identity is None:
                return unavailable("malformed-body"), None
            return complete(), identity
        if result.status is HttpStatus.ABSENT:
            return denied("absent"), None
        return unavailable(result.detail), None

    async def get_by_external_id(
        self, external_id: str
    ) -> tuple[Outcome, KratosIdentity | None]:
        """`GET /admin/identities/by/external/{externalID}` — the read-back
        a 409 resolves through."""
        result = await request(
            self.session,
            "GET",
            f"{self.admin_url}/admin/identities/by/external/{external_id}",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK, 404: HttpStatus.ABSENT},
        )
        if result.status is HttpStatus.OK:
            identity = _parse_identity(result.body)
            if identity is None:
                return unavailable("malformed-body"), None
            return complete(), identity
        if result.status is HttpStatus.ABSENT:
            return denied("absent"), None
        return unavailable(result.detail), None

    async def state_of(self, external_id: str) -> CheckResult:
        """Whether the identity is ACTIVE — `denied` for the inactive state
        (a real gate in v26.2.0) and `unknown` for absence, so a suspended
        person is never confused with a missing one."""
        outcome, identity = await self.get_by_external_id(external_id)
        if identity is None:
            if outcome.detail == "absent":
                return unknown_scope("identity-absent")
            return check_unavailable(outcome.detail)
        if identity.state == "active":
            return CheckResult(Verdict.ALLOWED)
        return check_denied(f"state:{identity.state}")

    async def set_state(self, *, identity_id: str, active: bool) -> Outcome:
        """`PATCH /admin/identities/{id}` — JSON-patch `state`."""
        result = await request(
            self.session,
            "PATCH",
            f"{self.admin_url}/admin/identities/{identity_id}",
            json_body=[
                {
                    "op": "replace",
                    "path": "/state",
                    "value": "active" if active else "inactive",
                }
            ],
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK, 404: HttpStatus.ABSENT},
        )
        if result.status is HttpStatus.OK:
            return complete()
        if result.status is HttpStatus.ABSENT:
            return denied("absent")
        return unavailable(result.detail)

    async def drop_sessions(self, *, identity_id: str) -> Outcome:
        """`DELETE /admin/identities/{id}/sessions`. A 404 on an identity
        that has no sessions is a completed effect — the end state (no
        sessions) already holds."""
        result = await request(
            self.session,
            "DELETE",
            f"{self.admin_url}/admin/identities/{identity_id}/sessions",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={204: HttpStatus.OK, 404: HttpStatus.ABSENT},
        )
        if result.status is HttpStatus.OK:
            return complete()
        if result.status is HttpStatus.ABSENT:
            return denied("absent")
        return unavailable(result.detail)
