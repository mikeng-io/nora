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
from urllib.parse import quote

import aiohttp
from yarl import URL

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
    state, the descriptive traits and the admin-only metadata."""

    identity_id: str
    external_id: str | None
    state: str
    traits: dict[str, Any]
    metadata_admin: dict[str, Any] | None = None


def _parse_identity(body: Any) -> KratosIdentity | None:
    if not isinstance(body, dict):
        return None
    identity_id = body.get("id")
    state = body.get("state")
    if (not isinstance(identity_id, str) or not identity_id.strip()
            or not isinstance(state, str) or state not in {"active", "inactive"}):
        return None
    traits = body.get("traits")
    external_id = body.get("external_id")
    metadata_admin = body.get("metadata_admin")
    if not isinstance(traits, dict) or (
        external_id is not None and not isinstance(external_id, str)
    ) or (
        metadata_admin is not None and not isinstance(metadata_admin, dict)
    ):
        return None
    return KratosIdentity(
        identity_id=identity_id,
        external_id=external_id if isinstance(external_id, str) else None,
        state=state,
        traits=traits,
        metadata_admin=metadata_admin if isinstance(metadata_admin, dict) else None,
    )


def _path(base: str, *segments: str) -> URL:
    url = URL(base)
    encoded = "/".join(quote(segment, safe="").replace(".", "%2E") for segment in segments)
    return url.with_path(url.raw_path.rstrip("/") + "/" + encoded, encoded=True)


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
        self, *, external_id: str, traits: dict[str, Any],
        metadata_admin: dict[str, Any] | None = None,
    ) -> tuple[Outcome, KratosIdentity | None]:
        """`POST /admin/identities`. A 409 is `denied` ("conflict") — the
        caller then reads the identity back by `external_id` rather than
        trusting the create path's failure shape. `metadata_admin` rides the
        same write when given: admin-only metadata, durable at create time,
        never a holder-editable trait."""
        body: dict[str, Any] = {
            "schema_id": self.schema_id,
            "traits": traits,
            "external_id": external_id,
            "state": "active",
        }
        if metadata_admin is not None:
            body["metadata_admin"] = metadata_admin
        result = await request(
            self.session,
            "POST",
            f"{self.admin_url}/admin/identities",
            json_body=body,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={201: HttpStatus.OK, 409: HttpStatus.CONFLICT},
        )
        if result.status is HttpStatus.OK:
            identity = _parse_identity(result.body)
            if identity is None or identity.external_id != external_id:
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
            _path(self.admin_url, "admin", "identities", identity_id),
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK, 404: HttpStatus.ABSENT},
        )
        if result.status is HttpStatus.OK:
            identity = _parse_identity(result.body)
            if identity is None or identity.identity_id != identity_id:
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
            _path(self.admin_url, "admin", "identities", "by", "external", external_id),
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK, 404: HttpStatus.ABSENT},
        )
        if result.status is HttpStatus.OK:
            identity = _parse_identity(result.body)
            if identity is None or identity.external_id != external_id:
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
            _path(self.admin_url, "admin", "identities", identity_id),
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
            identity = _parse_identity(result.body)
            if (identity is None or identity.identity_id != identity_id
                    or identity.state != ("active" if active else "inactive")):
                return unavailable("state-readback-mismatch")
            return complete()
        if result.status is HttpStatus.ABSENT:
            return denied("absent")
        return unavailable(result.detail)

    async def set_display_name(
        self, *, identity_id: str, display_name: str
    ) -> Outcome:
        """`PATCH /admin/identities/{id}` — JSON-patch only
        `/traits/display_name`, so every other trait, credential and the
        record itself is preserved; the patched body must read the new
        name back before `complete`."""
        result = await request(
            self.session,
            "PATCH",
            _path(self.admin_url, "admin", "identities", identity_id),
            json_body=[
                {
                    # `add` covers a never-set display_name as well as a
                    # rename — RFC 6902 `add` replaces an existing member.
                    "op": "add",
                    "path": "/traits/display_name",
                    "value": display_name,
                }
            ],
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK, 404: HttpStatus.ABSENT},
        )
        if result.status is HttpStatus.OK:
            identity = _parse_identity(result.body)
            if (identity is None or identity.identity_id != identity_id
                    or identity.traits.get("display_name") != display_name):
                return unavailable("display-name-readback-mismatch")
            return complete()
        if result.status is HttpStatus.ABSENT:
            return denied("absent")
        return unavailable(result.detail)

    async def drop_sessions(self, *, identity_id: str) -> Outcome:
        """Delete sessions; a zero-row 404 requires an empty-list read-back."""
        result = await request(
            self.session,
            "DELETE",
            _path(self.admin_url, "admin", "identities", identity_id, "sessions"),
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={204: HttpStatus.OK, 404: HttpStatus.ABSENT},
        )
        if result.status is HttpStatus.OK:
            return complete()
        if result.status is HttpStatus.ABSENT:
            found, identity = await self.get_identity(identity_id)
            if identity is None:
                return found
            sessions = await request(
                self.session, "GET",
                _path(self.admin_url, "admin", "identities", identity_id, "sessions"),
                api_key=self.api_key, timeout_seconds=self.timeout_seconds,
                expected={200: HttpStatus.OK},
            )
            if sessions.status is HttpStatus.OK and sessions.body == []:
                return complete("sessions-already-absent")
            return unavailable("session-revocation-unconfirmed")
        return unavailable(result.detail)
