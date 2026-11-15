"""The managed backend's upstream-store fakes — `FakeIdentityRegistry` for
the Kratos identity records and `FakeLinkRequests` for durable private link
consent, with the same identity/effect rules the real stores enforce.
Re-exported through `nora.testing.managed` alongside the binding-store and
authority fakes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from nora.domain.account_linking import (
    ConsentProof,
    LinkState,
    LinkStateError,
    PendingLink,
)
from nora.domain.identity import Identifier
from nora.domain.managed import (
    CheckResult,
    Outcome,
    allowed,
    check_denied,
    check_unavailable,
    complete,
    denied,
    unavailable,
    unknown_scope,
)
from nora.domain.managed_ports import IdentityRegistry, LinkRequestStore
from nora.domain.management import (
    LinkApproval,
    LinkRefusal,
    LinkSide,
    Operation,
)


def unavailable_outcome() -> Outcome:
    return unavailable("down")


class FakeIdentityRegistry(IdentityRegistry):
    """In-memory Kratos records keyed by `external_id` — same rules: one
    identity per external id, `active` gates state, `down` is the outage."""

    def __init__(self) -> None:
        self.identities: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, int] = {}
        self.down = False
        self._counter = 0

    async def ensure_identity(
        self, *, external_id: str, traits: dict[str, Any]
    ) -> tuple[Outcome, str | None]:
        if self.down:
            return unavailable_outcome(), None
        existing = self.identities.get(external_id)
        if existing is not None:
            return complete("read-back"), existing["id"]
        self._counter += 1
        identity_id = f"kratos-{self._counter}"
        self.identities[external_id] = {
            "id": identity_id, "state": "active", "traits": dict(traits),
        }
        return complete(), identity_id

    async def identity_state(self, *, external_id: str) -> CheckResult:
        if self.down:
            return check_unavailable("down")
        ident = self.identities.get(external_id)
        if ident is None:
            return unknown_scope("absent")
        return allowed() if ident["state"] == "active" else check_denied("inactive")

    async def set_state(self, *, external_id: str, active: bool) -> Outcome:
        if self.down:
            return unavailable_outcome()
        ident = self.identities.get(external_id)
        if ident is None:
            return denied("absent")
        ident["state"] = "active" if active else "inactive"
        return complete()

    async def drop_sessions(self, *, external_id: str) -> Outcome:
        if self.down:
            return unavailable_outcome()
        if external_id not in self.identities:
            return denied("absent")
        self.sessions.pop(external_id, None)
        return complete()

    async def aclose(self) -> None:
        ...


class FakeLinkRequests(LinkRequestStore):
    """In-memory durable link consent — the same identity/effect rules as
    `SqlLinkRequests`, so unit services exercise the real decision path."""

    def __init__(self) -> None:
        self.requests: dict[str, PendingLink] = {}
        # operation_id -> (request_id, effect fingerprint) — a decision id
        # can never be consumed by a different request or effect.
        self.decisions: dict[str, tuple[str, str]] = {}
        # (request_id, side) -> the private nonce that side was sent.
        self.challenges: dict[tuple[str, LinkSide], str] = {}

    @staticmethod
    def _effect(operation: Operation, proof: ConsentProof) -> str:
        fingerprint = {
            "operation_id": operation.operation_id,
            "kind": operation.kind,
            "request_id": getattr(operation.payload, "request_id", ""),
            "actor": operation.actor.person_id,
            "issuer": proof.issuer,
            "nonce": hashlib.sha256(proof.nonce.encode()).hexdigest(),
        }
        return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()

    async def create(
        self, link: PendingLink, *, nonces: Mapping[LinkSide, str]
    ) -> PendingLink:
        if link.state is not LinkState.PENDING or link.approved:
            raise LinkStateError("create requires a new undecided request")
        if (set(nonces) != set(LinkSide) or any(
                hashlib.sha256(nonces[side].encode()).hexdigest()
                != link.nonce_hashes[side] for side in LinkSide)):
            raise LinkStateError("challenges disagree with the request's recorded digests")
        if link.request_id in self.decisions:
            raise LinkStateError("operation identity already names a decision")
        recorded = self.requests.get(link.request_id)
        if recorded is not None:
            if recorded.effect_id != link.effect_id:
                raise LinkStateError("request identity already names a different effect")
            return recorded
        self.requests[link.request_id] = link
        self.challenges.update(
            {(link.request_id, side): nonces[side] for side in LinkSide})
        return link

    async def get(self, request_id: str) -> PendingLink | None:
        return self.requests.get(request_id)

    async def challenge(self, request_id: str, *, side: LinkSide) -> str | None:
        return self.challenges.get((request_id, side))

    async def decide(
        self, operation: Operation, proof: ConsentProof, *, now: datetime
    ) -> PendingLink:
        payload = operation.payload
        if not isinstance(payload, LinkApproval | LinkRefusal):
            raise LinkStateError("expected a private holder decision")
        if operation.operation_id in self.requests:
            raise LinkStateError("operation identity already names a request")
        effect = self._effect(operation, proof)
        previous = self.decisions.get(operation.operation_id)
        if previous is not None and previous != (payload.request_id, effect):
            raise LinkStateError("decision identity already names a different effect")
        link = self.requests.get(payload.request_id)
        if link is None:
            raise LinkStateError("unknown private link request")
        if previous is not None:
            return link
        changed = link.decide(operation, proof, now=now)
        self.decisions[operation.operation_id] = (payload.request_id, effect)
        self.requests[payload.request_id] = changed
        return changed

    async def begin_commit(
        self, request_id: str, *, owners: Mapping[Identifier, str],
        active_people: frozenset[str], revisions: Mapping[str, str],
        now: datetime,
    ) -> PendingLink:
        link = self.requests.get(request_id)
        if link is None:
            raise LinkStateError("unknown private link request")
        changed = link.begin_commit(
            now=now, owners=owners, active_people=active_people,
            revisions=revisions)
        self.requests[request_id] = changed
        return changed

    async def mark_committed(self, request_id: str) -> PendingLink:
        link = self.requests.get(request_id)
        if link is None:
            raise LinkStateError("unknown private link request")
        changed = link.mark_committed()
        self.requests[request_id] = changed
        return changed

    async def aclose(self) -> None:
        ...
