"""Request-bound bilateral consent, before any ownership publication.

The service supplies authenticated actors/proofs and current store reads. It
must persist transitions under the same lock that serializes commitment; this
value model neither authenticates callers nor publishes identity ownership.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType

from nora.domain.identity import Identifier
from nora.domain.management import (
    OPERATIONS,
    LinkApproval,
    LinkPair,
    LinkRefusal,
    LinkRequest,
    LinkSide,
    Operation,
    OperationError,
)


class LinkStateError(OperationError):
    """A decision cannot apply to this request and authenticated proof."""


class ConsentSurface(Enum):
    SIGNAL_PRIVATE = "signal-private"
    DISCORD_REAUTH = "discord-reauth"
    TELEGRAM_REAUTH = "telegram-reauth"


_SURFACE_PLATFORM = {
    ConsentSurface.SIGNAL_PRIVATE: "signal",
    ConsentSurface.DISCORD_REAUTH: "discord",
    ConsentSurface.TELEGRAM_REAUTH: "telegram",
}


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LinkStateError("consent times require a timezone")


def _digest(nonce: str) -> str:
    if not isinstance(nonce, str) or not nonce.strip():
        raise LinkStateError("a private request nonce is required")
    return hashlib.sha256(nonce.encode()).hexdigest()


@dataclass(frozen=True)
class ConsentProof:
    """Server-derived proof from the named account's private consent surface.

    OTP redemption and an ordinary portal session are deliberately absent.
    The transport/provider adapter validates provenance before constructing
    this value; accepting its fields from browser/model JSON is forbidden.
    """

    account: Identifier
    issuer: str
    surface: ConsentSurface
    authenticated_at: datetime
    request_id: str
    nonce: str = field(repr=False)

    def __post_init__(self) -> None:
        _aware(self.authenticated_at)
        if _SURFACE_PLATFORM.get(self.surface) != self.account.platform:
            raise LinkStateError("consent surface does not authenticate this platform")
        if not self.issuer.strip() or not self.request_id.strip():
            raise LinkStateError("consent requires issuer and request provenance")
        _digest(self.nonce)


class LinkState(Enum):
    PENDING = "pending"
    REFUSED = "refused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    COMMITTING = "committing"
    COMMITTED = "committed"


@dataclass(frozen=True)
class LinkObservation:
    """The live snapshot a link request binds (`account-linking.md`).

    The service computes it from CURRENT store reads; the request records
    these values so commitment can prove the approved state still holds.
    """

    owners: Mapping[LinkSide, str]
    receiving_person: str
    revisions: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "owners", MappingProxyType(dict(self.owners)))
        object.__setattr__(self, "revisions", MappingProxyType(dict(self.revisions)))
        if set(self.owners) != set(LinkSide):
            raise LinkStateError("an observation names both owners")
        if not self.receiving_person.strip():
            raise LinkStateError("an observation names the receiving person")
        if any(not self.revisions.get(key, "").strip()
               for key in OPERATIONS["request_link"].revisions):
            raise LinkStateError("an observation binds every required revision")


@dataclass(frozen=True)
class ConsentReceipt:
    operation_id: str
    account: Identifier
    issuer: str
    surface: ConsentSurface
    authenticated_at: datetime
    approved_at: datetime
    effect_id: str


@dataclass(frozen=True)
class PendingLink:
    request_id: str
    pair: LinkPair
    surviving_person: str
    owners: Mapping[LinkSide, str]
    revisions: Mapping[str, str]
    nonce_hashes: Mapping[LinkSide, str]
    created_at: datetime
    expires_at: datetime
    approvals: Mapping[LinkSide, ConsentReceipt] = field(default_factory=dict)
    state: LinkState = LinkState.PENDING

    def __post_init__(self) -> None:
        _aware(self.created_at)
        _aware(self.expires_at)
        if not self.request_id.strip() or self.expires_at <= self.created_at:
            raise LinkStateError("a link requires an id and a positive lifetime")
        for name in ("owners", "revisions", "nonce_hashes", "approvals"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        if not self.approved <= frozenset(LinkSide):
            raise LinkStateError("only the two named sides can approve")
        if set(self.owners) != set(LinkSide) or set(self.nonce_hashes) != set(LinkSide):
            raise LinkStateError("a link binds both holders and private challenges")
        if any(not value.strip() for value in self.owners.values()):
            raise LinkStateError("both account holders must be known")
        if self.surviving_person not in self.owners.values():
            raise LinkStateError("the survivor must own one of the approved accounts")
        if any(not self.revisions.get(key, "").strip()
               for key in OPERATIONS["request_link"].revisions):
            raise LinkStateError("a link binds every required revision")
        if any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
               for value in self.nonce_hashes.values()):
            raise LinkStateError("persist only private nonce digests")
        if self.nonce_hashes[LinkSide.INITIATING] == self.nonce_hashes[LinkSide.RECEIVING]:
            raise LinkStateError("the two holders require independent challenges")
        for side, receipt in self.approvals.items():
            _aware(receipt.authenticated_at)
            _aware(receipt.approved_at)
            if (receipt.effect_id != self.effect_id or receipt.account != self.pair.account(side)
                    or not receipt.operation_id.strip() or not receipt.issuer.strip()
                    or _SURFACE_PLATFORM.get(receipt.surface) != receipt.account.platform
                    or not self.created_at <= receipt.authenticated_at
                    <= receipt.approved_at < self.expires_at):
                raise LinkStateError("persisted approval does not prove this request's effect")

    @property
    def effect_id(self) -> str:
        def account(value: Identifier) -> tuple[str, str, str]:
            return value.platform, value.kind, value.value

        snapshot = {
            "request_id": self.request_id,
            "pair": [account(self.pair.initiating), account(self.pair.receiving)],
            "survivor": self.surviving_person,
            "owners": {side.value: value for side, value in self.owners.items()},
            "revisions": dict(self.revisions),
            "nonce_hashes": {side.value: value for side, value in self.nonce_hashes.items()},
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
        }
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()

    @property
    def approved(self) -> frozenset[LinkSide]:
        return frozenset(self.approvals)

    @classmethod
    def request(
        cls, operation: Operation, *, receiving_person: str,
        initiating_nonce: str, receiving_nonce: str,
        created_at: datetime, expires_at: datetime,
    ) -> PendingLink:
        if operation.kind != "request_link" or not isinstance(operation.payload, LinkRequest):
            raise LinkStateError("expected a holder's link request")
        if operation.actor.account is None or operation.target.account is None:
            raise LinkStateError("a link binds exact accounts")
        return cls(
            request_id=operation.operation_id,
            pair=LinkPair(operation.actor.account, operation.target.account),
            surviving_person=operation.payload.surviving_person,
            owners={LinkSide.INITIATING: operation.actor.person_id or "",
                    LinkSide.RECEIVING: receiving_person},
            revisions=operation.observed_revisions,
            nonce_hashes={LinkSide.INITIATING: _digest(initiating_nonce),
                          LinkSide.RECEIVING: _digest(receiving_nonce)},
            created_at=created_at, expires_at=expires_at,
        )

    def expire(self, now: datetime) -> PendingLink:
        _aware(now)
        if self.state is LinkState.PENDING and now >= self.expires_at:
            return replace(self, state=LinkState.EXPIRED)
        return self

    @property
    def ready(self) -> bool:
        return self.state is LinkState.PENDING and self.approved == frozenset(LinkSide)

    def decide(self, operation: Operation, proof: ConsentProof, *, now: datetime) -> PendingLink:
        _aware(now)
        payload = operation.payload
        if (self.expire(now).state is not LinkState.PENDING
                or now < self.created_at
                or operation.kind not in {"approve_link", "refuse_link", "cancel_link"}
                or not isinstance(payload, LinkApproval | LinkRefusal)):
            raise LinkStateError("this request cannot accept a decision")
        if (payload.request_id != self.request_id or payload.pair != self.pair
                or (isinstance(payload, LinkApproval)
                    and payload.surviving_person != self.surviving_person)):
            raise LinkStateError("the decision names another request or effect")
        account = self.pair.account(payload.side)
        if (proof.request_id != self.request_id or proof.account != account
                or operation.actor.account != account
                or operation.actor.person_id != self.owners[payload.side]
                or operation.actor.proof_issuer != proof.issuer
                or not self.created_at <= proof.authenticated_at <= now
                or not hmac.compare_digest(_digest(proof.nonce), self.nonce_hashes[payload.side])):
            raise LinkStateError("the private proof does not authenticate this holder's decision")
        if any(operation.observed_revisions.get(key) != self.revisions[key]
               for key in OPERATIONS[operation.kind].revisions):
            raise LinkStateError("the approved revisions changed")
        if operation.kind == "approve_link":
            if payload.side in self.approved:
                raise LinkStateError("this side's private approval was already consumed")
            receipt = ConsentReceipt(
                operation_id=operation.operation_id, account=account,
                issuer=proof.issuer, surface=proof.surface,
                authenticated_at=proof.authenticated_at, approved_at=now,
                effect_id=self.effect_id,
            )
            return replace(self, approvals={**self.approvals, payload.side: receipt})
        state = LinkState.REFUSED if operation.kind == "refuse_link" else LinkState.CANCELLED
        return replace(self, state=state)

    def begin_commit(
        self, *, now: datetime, owners: Mapping[Identifier, str],
        active_people: frozenset[str], revisions: Mapping[str, str],
    ) -> PendingLink:
        """Revalidate live reads under the service's ownership transaction lock.

        COMMITTING is not completion: reconciliation must publish and read back
        the binding, aliases, permissions and session revocations afterwards.
        """
        if not self.expire(now).ready or now < self.created_at:
            raise LinkStateError("both current approvals are required")
        if any(owners.get(self.pair.account(side)) != self.owners[side]
               or self.owners[side] not in active_people for side in LinkSide):
            raise LinkStateError("a holder's current ownership or lifecycle changed")
        if any(revisions.get(key) != value for key, value in self.revisions.items()):
            raise LinkStateError("current authority no longer matches the approved state")
        return replace(self, state=LinkState.COMMITTING)

    def mark_committed(self) -> PendingLink:
        """Terminal marker — reconciliation publishes and reads back before
        this transition is recorded; nothing un-commits a link."""
        if self.state is LinkState.COMMITTED:
            return self
        if self.state is not LinkState.COMMITTING:
            raise LinkStateError("only a committing request can complete")
        return replace(self, state=LinkState.COMMITTED)
