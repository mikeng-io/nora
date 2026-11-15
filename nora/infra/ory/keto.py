"""The pinned Keto v26.2.0 surface Nora uses (`authorization.md`).

Read API on the private read listener (`/relation-tuples/check`,
`/relation-tuples`, `/namespaces`); writes on the private write listener
(`PUT`/`DELETE /admin/relation-tuples`).

The check mirror maps the verdict into the status — `allowed: true` is 200,
`allowed: false` is 403 — and this client validates that status and body
AGREE: a 200 whose body does not allow, a body that is not an object, or a
`subject_set` the check does not know is `unavailable`, never an inferred
verdict. Unknown objects and empty tuple sets are ordinary denies, not
errors — the service's scope validation, not Keto, is what notices a scope
that does not exist.
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
    keto_namespace,
    unavailable,
)
from nora.domain.management import Scope
from nora.infra.ory.http import HttpResult, HttpStatus, request


@dataclass(frozen=True)
class RelationTuple:
    """One asserted Keto relationship — `(relation, subject)` on an object.

    `subject_set` carries containment links (`Unit.workspace`,
    `Room.unit`): the relation is empty and the subject names another
    namespace's object whole."""

    namespace: str
    object: str
    relation: str
    subject_id: str | None = None
    subject_set: tuple[str, str] | None = None  # (namespace, object)

    def write_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "namespace": self.namespace,
            "object": self.object,
            "relation": self.relation,
        }
        if self.subject_set is not None:
            body["subject_set"] = {
                "namespace": self.subject_set[0],
                "object": self.subject_set[1],
                "relation": "",
            }
        else:
            body["subject_id"] = self.subject_id
        return body

    def query_params(self) -> dict[str, str]:
        params = {
            "namespace": self.namespace,
            "object": self.object,
            "relation": self.relation,
        }
        if self.subject_set is not None:
            params["subject_set.namespace"] = self.subject_set[0]
            params["subject_set.object"] = self.subject_set[1]
        else:
            params["subject_id"] = self.subject_id or ""
        return params


@dataclass(frozen=True)
class KetoClient:
    """Thin, typed calls over the two private listeners.

    Answers are `CheckResult`/`Outcome` VALUES — a transport failure, a
    redirect or a malformed body is `unavailable` and is never raised, so a
    caller cannot forget to catch its way into an allow.
    """

    session: aiohttp.ClientSession
    read_url: str
    write_url: str
    timeout_seconds: float = 5.0
    api_key: str | None = None

    async def check(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> CheckResult:
        """`GET /relation-tuples/check` — the mirror, not `/check/openapi`
        (which answers 200 either way and cannot be trusted for a verdict)."""
        result = await request(
            self.session,
            "GET",
            f"{self.read_url}/relation-tuples/check",
            params={
                "namespace": keto_namespace(scope.kind),
                "object": scope.id,
                "relation": relation,
                "subject_id": person_id,
            },
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK, 403: HttpStatus.DENIED},
        )
        if result.status is HttpStatus.OK:
            if (
                isinstance(result.body, dict)
                and result.body.get("allowed") is True
            ):
                return CheckResult(Verdict.ALLOWED)
            # status/body disagreement: a 200 that does not affirm allow is
            # not a denial — the answer is untrustworthy.
            return check_unavailable("status-body-disagree")
        if result.status is HttpStatus.DENIED:
            if (
                isinstance(result.body, dict)
                and result.body.get("allowed") is False
            ):
                return check_denied()
            return check_unavailable("status-body-disagree")
        return check_unavailable(result.detail)

    async def put_tuple(self, tuple_: RelationTuple) -> Outcome:
        """`PUT /admin/relation-tuples` — 201 on create. Keto accepts the
        same tuple twice and stores both, so idempotence is the service's
        job: verify with `tuples_on` before/after, don't replay blindly."""
        result = await request(
            self.session,
            "PUT",
            f"{self.write_url}/admin/relation-tuples",
            json_body=tuple_.write_body(),
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={201: HttpStatus.OK},
        )
        if result.status is HttpStatus.OK:
            return complete()
        if result.detail == "status:400":
            # Keto answered the request was malformed — an authoritative
            # refusal of THIS write, not a service failure.
            return denied("malformed-tuple")
        return unavailable(result.detail)

    async def drop_tuple(self, tuple_: RelationTuple) -> Outcome:
        """`DELETE /admin/relation-tuples` — 204 whether the tuple existed
        or not (absent is indistinguishable from deleted at this endpoint;
        `tuples_on` is the read-back that proves absence)."""
        result = await request(
            self.session,
            "DELETE",
            f"{self.write_url}/admin/relation-tuples",
            params=tuple_.query_params(),
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={204: HttpStatus.OK},
        )
        if result.status is HttpStatus.OK:
            return complete()
        return unavailable(result.detail)

    async def tuples_on(
        self, *, namespace: str, object_id: str
    ) -> CheckResult | frozenset[RelationTuple]:
        """`GET /relation-tuples?namespace&object` — the read-back list a
        mutation verifies itself against. `unavailable` when the read
        cannot answer."""
        result = await request(
            self.session,
            "GET",
            f"{self.read_url}/relation-tuples",
            params={"namespace": namespace, "object": object_id},
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK},
        )
        if result.status is not HttpStatus.OK:
            return check_unavailable(result.detail)
        rows = result.body.get("relation_tuples") if isinstance(result.body, dict) else None
        if not isinstance(rows, list):
            return check_unavailable("malformed-body")
        out: set[RelationTuple] = set()
        for row in rows:
            if not isinstance(row, dict):
                return check_unavailable("malformed-body")
            subject_set = row.get("subject_set")
            if isinstance(subject_set, dict):
                out.add(
                    RelationTuple(
                        namespace=row.get("namespace", ""),
                        object=row.get("object", ""),
                        relation=row.get("relation", ""),
                        subject_set=(
                            subject_set.get("namespace", ""),
                            subject_set.get("object", ""),
                        ),
                    )
                )
            else:
                out.add(
                    RelationTuple(
                        namespace=row.get("namespace", ""),
                        object=row.get("object", ""),
                        relation=row.get("relation", ""),
                        subject_id=row.get("subject_id"),
                    )
                )
        return frozenset(out)

    async def namespaces(self) -> HttpResult:
        """`GET /namespaces` — what the service actually loaded."""
        return await request(
            self.session,
            "GET",
            f"{self.read_url}/namespaces",
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            expected={200: HttpStatus.OK},
        )
