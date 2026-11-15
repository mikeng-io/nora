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

    def _tuple_row(
        self, row: Any, *, namespace: str, object_id: str
    ) -> RelationTuple | None:
        """Strict shape+identity validation — an unplaceable row fails closed."""
        if (
            not isinstance(row, dict)
            or row.get("namespace") != namespace
            or row.get("object") != object_id
            or not isinstance(row.get("relation"), str)
            or not row["relation"]
        ):
            return None
        subject_set = row.get("subject_set")
        if subject_set is not None:
            if (
                not isinstance(subject_set, dict)
                or not isinstance(subject_set.get("namespace"), str)
                or not subject_set["namespace"]
                or not isinstance(subject_set.get("object"), str)
                or not subject_set["object"]
            ):
                return None
            return RelationTuple(
                namespace=namespace, object=object_id,
                relation=row["relation"],
                subject_set=(subject_set["namespace"], subject_set["object"]),
            )
        subject_id = row.get("subject_id")
        if not isinstance(subject_id, str) or not subject_id:
            return None
        return RelationTuple(
            namespace=namespace, object=object_id,
            relation=row["relation"], subject_id=subject_id,
        )

    async def tuples_on(
        self, *, namespace: str, object_id: str
    ) -> CheckResult | frozenset[RelationTuple]:
        """`GET /relation-tuples?namespace&object` — the read-back list a
        mutation verifies itself against, FOLLOWED ACROSS PAGES: v26.2.0
        pages the answer behind `next_page_token`, so a first-page read is
        incomplete evidence. `unavailable` when the read cannot answer or
        a row falls outside the requested object."""
        out: set[RelationTuple] = set()
        page_token: str | None = None
        for _ in range(64):  # a page cap that bounds a looping server
            params: dict[str, Any] = {"namespace": namespace, "object": object_id}
            if page_token:
                params["page_token"] = page_token
            result = await request(
                self.session,
                "GET",
                f"{self.read_url}/relation-tuples",
                params=params,
                api_key=self.api_key,
                timeout_seconds=self.timeout_seconds,
                expected={200: HttpStatus.OK},
            )
            if result.status is not HttpStatus.OK:
                return check_unavailable(result.detail)
            body = result.body if isinstance(result.body, dict) else {}
            rows = body.get("relation_tuples")
            if not isinstance(rows, list):
                return check_unavailable("malformed-body")
            for row in rows:
                parsed = self._tuple_row(row, namespace=namespace, object_id=object_id)
                if parsed is None:
                    return check_unavailable("malformed-row")
                out.add(parsed)
            next_token = body.get("next_page_token")
            if next_token in (None, ""):
                return frozenset(out)
            if not isinstance(next_token, str):
                return check_unavailable("malformed-body")
            page_token = next_token
        return check_unavailable("pagination-exceeded")

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
