"""The holder-facing link lifecycle (`docs/design/people/linking.md`).

Every HTTP-facing method revalidates the caller's exact account through
the managed session check — a request body never names an actor, an
account or a proof. Consent itself never rides a session: a decision
needs the side's private surface — a fresh provider re-authentication
for Discord/Telegram, the trusted signal-cli envelope for Signal — so a
retained session can create, view and unlink but can never approve.

Challenges are single-side everywhere: the store persists each side's
private nonce next to its request (one transaction, so a restart can
never strand an undeliverable request), and a view returns only the
caller's own side — the initiator can never read the receiver's nonce.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from nora.domain.account_linking import (
    ConsentProof,
    ConsentSurface,
    LinkState,
    LinkStateError,
    PendingLink,
)
from nora.domain.authentication import AccountAuthentication, AuthenticatedPrincipal
from nora.domain.identity import Identifier
from nora.domain.managed import (
    CheckResult,
    ManagedAccount,
    Outcome,
    Verdict,
    complete,
    denied,
    unavailable,
)
from nora.domain.managed_ports import (
    BindingStore,
    IdentityRegistry,
    LinkChallengeDelivery,
    LinkRequestStore,
)
from nora.domain.management import (
    Actor,
    ActorKind,
    LinkApproval,
    LinkPair,
    LinkRefusal,
    LinkRequest,
    LinkSide,
    Operation,
    OperationError,
    Target,
    TargetKind,
    Unlink,
)
from nora.people.app.authentication import AccountSessions
from nora.people.app.link_publication import LinkPublicationService
from nora.people.app.managed import RegistrationService

log = logging.getLogger(__name__)

LIFETIME = timedelta(minutes=15)

#: Only trusted provider re-authentication can supply HTTP consent.
#: Every code-login method remains authentication without consent authority.
_REAUTH_SURFACE: Mapping[str, ConsentSurface] = {
    "discord_oauth": ConsentSurface.DISCORD_REAUTH,
    "telegram_oidc": ConsentSurface.TELEGRAM_REAUTH,
}

#: These platforms cannot approve through HTTP; code-login proof is insufficient.
#: Supported private transports own their approval surface (account-linking.md).
_TRANSPORT_ONLY: frozenset[str] = frozenset({"signal", "whatsapp"})

#: Platforms a link request may name at all — each side needs a consent
#: surface (a private transport decision or a provider re-auth), so an
#: account on any other platform could never approve.
_CONSENTABLE: frozenset[str] = frozenset({"signal", "discord", "telegram"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class HolderLinkView:
    """The per-side slice ONE authenticated holder may see — the request,
    their own approval state and their own challenge, never the other
    holder's (`nonce` stays out of `repr` so a logged view cannot leak it)."""

    request_id: str
    side: LinkSide
    pair: LinkPair
    surviving_person: str
    state: LinkState
    created_at: datetime
    expires_at: datetime
    approved: frozenset[LinkSide]
    nonce: str = field(repr=False)


@dataclass(frozen=True)
class LinkActionResult:
    """A decision attempt: the outcome plus the request state afterwards,
    so a refusal can still show the holder where the request landed."""

    outcome: Outcome
    state: LinkState | None = None


class HolderLinkService:
    """`request_link`/`view`/decision/`unlink` for authenticated holders.

    Owns no state of its own: the durable store owns the request and its
    challenges, `LinkPublicationService` owns every ownership effect, and
    `RegistrationService` owns unlink — this service only revalidates the
    holder and hands the operation to the right executor.
    """

    def __init__(
        self,
        bindings: BindingStore,
        registry: IdentityRegistry,
        links: LinkRequestStore,
        publication: LinkPublicationService,
        registration: RegistrationService,
        *,
        signal_delivery: LinkChallengeDelivery | None = None,
        lifetime: timedelta = LIFETIME,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._bindings = bindings
        self._sessions = AccountSessions(bindings, registry)
        self._links = links
        self._publication = publication
        self._registration = registration
        self._signal_delivery = signal_delivery
        self._lifetime = lifetime
        self._clock = clock

    # -- authenticated entry points ---------------------------------------

    async def request_link(
        self,
        authentication: AccountAuthentication,
        *,
        request_id: str,
        receiving: Identifier,
        surviving_person: str,
    ) -> HolderLinkView | Outcome:
        """Observe live state and persist the request plus both private
        challenges in one durable write, then push each side's challenge
        where a private transport exists."""
        if not request_id.strip():
            return denied("request-id-required")
        checked = await self._principal(authentication)
        if isinstance(checked, Outcome):
            return checked
        initiating = authentication.account
        if initiating == receiving:
            return denied("same-account")
        if not {initiating.platform, receiving.platform} <= _CONSENTABLE:
            return denied("platform-not-consentable")
        observation = await self._publication.observe(
            initiating=initiating, receiving=receiving,
            surviving_person=surviving_person)
        if isinstance(observation, CheckResult):
            return _check_outcome(observation)
        try:
            operation = Operation(
                operation_id=request_id, kind="request_link",
                actor=Actor(
                    ActorKind.HOLDER, person_id=checked.binding.person_id,
                    account=initiating,
                    proof_issuer=authentication.proof_issuer),
                target=Target(TargetKind.ACCOUNT, account=receiving),
                payload=LinkRequest(surviving_person),
                observed_revisions=dict(observation.revisions),
            )
            nonces = {LinkSide.INITIATING: secrets.token_urlsafe(24),
                      LinkSide.RECEIVING: secrets.token_urlsafe(24)}
            link = PendingLink.request(
                operation, receiving_person=observation.receiving_person,
                initiating_nonce=nonces[LinkSide.INITIATING],
                receiving_nonce=nonces[LinkSide.RECEIVING],
                created_at=(now := self._clock()),
                expires_at=now + self._lifetime)
            link = await self._links.create(link, nonces=nonces)
        except (OperationError, LinkStateError) as error:
            return denied(str(error) or "link-request-refused")
        except Exception:  # noqa: BLE001 — an unreadable store is unavailable
            log.error("link request store unavailable")
            return unavailable("request-unavailable")
        await self._deliver_signal_challenges(link)
        view = await self.view_link(authentication, request_id=link.request_id)
        return view

    async def view_link(
        self,
        authentication: AccountAuthentication,
        *,
        request_id: str,
    ) -> HolderLinkView | Outcome:
        """The caller's own side of one request — including their private
        challenge — or a refusal when they are not a named holder."""
        checked = await self._principal(authentication)
        if isinstance(checked, Outcome):
            return checked
        link = await self._read(request_id)
        if isinstance(link, Outcome):
            return link
        side = self._side_of(link, authentication.account)
        if side is None:
            return denied("not-a-party")
        return await self._view(link, side)

    async def decide(
        self,
        authentication: AccountAuthentication,
        *,
        operation_id: str,
        request_id: str,
        action: str,
        nonce: str,
    ) -> LinkActionResult:
        """A private HTTP decision — a `sign_in` assertion whose method
        maps to the account's consent surface, because a retained session
        is not consent (`management.md`)."""
        if authentication.purpose != "sign_in":
            return LinkActionResult(denied("fresh-authentication-required"))
        surface = _REAUTH_SURFACE.get(authentication.method)
        if surface is None or authentication.account.platform in _TRANSPORT_ONLY:
            return LinkActionResult(denied("consent-surface-required"))
        checked = await self._principal(authentication)
        if isinstance(checked, Outcome):
            return LinkActionResult(checked)
        link = await self._read(request_id)
        if isinstance(link, Outcome):
            return LinkActionResult(link)
        side = self._side_of(link, authentication.account)
        if side is None:
            return LinkActionResult(denied("not-a-party"), state=link.state)
        kind = _decision_kind(action, side)
        if kind is None:
            return LinkActionResult(denied("unknown-decision"), state=link.state)
        try:
            proof = ConsentProof(
                account=authentication.account,
                issuer=authentication.proof_issuer, surface=surface,
                authenticated_at=authentication.authenticated_at,
                request_id=request_id, nonce=nonce)
        except LinkStateError as error:
            return LinkActionResult(denied(str(error)), state=link.state)
        return await self._decide(
            link, side, kind, operation_id=operation_id,
            actor_account=authentication.account,
            issuer=authentication.proof_issuer,
            person_id=checked.binding.person_id, proof=proof)

    async def decide_from_transport(
        self,
        *,
        sender: frozenset[Identifier],
        issuer: str,
        timestamp_ms: int,
        request_id: str,
        nonce: str,
        approve: bool,
    ) -> Outcome:
        """A private consent message from the trusted transport — account
        control is the transport's own provenance (`sourceNumber`/
        `sourceUuid`), never a session and never message-claimed text."""
        link = await self._read(request_id)
        if isinstance(link, Outcome):
            return link
        sender_ids = frozenset(i for i in sender if i.platform == "signal")
        record = await self._sender_account(sender_ids)
        if isinstance(record, Outcome):
            return record
        side = None
        for candidate in LinkSide:
            bound = await self._bindings.record_for(
                account=link.pair.account(candidate))
            if bound is not None and bound.account_id == record.account_id:
                side = candidate
        if side is None:
            return denied("not-a-party")
        authenticated_at = datetime.fromtimestamp(timestamp_ms / 1000, UTC)
        kind = ("approve_link" if approve
                else "cancel_link" if side is LinkSide.INITIATING else "refuse_link")
        try:
            proof = ConsentProof(
                account=link.pair.account(side), issuer=issuer,
                surface=ConsentSurface.SIGNAL_PRIVATE,
                authenticated_at=authenticated_at,
                request_id=request_id, nonce=nonce)
        except LinkStateError as error:
            return denied(str(error))
        result = await self._decide(
            link, side, kind,
            operation_id=f"signal:{request_id}:{side.value}:{timestamp_ms}",
            actor_account=link.pair.account(side), issuer=issuer,
            person_id=record.person_id, proof=proof)
        return result.outcome

    async def unlink(
        self,
        authentication: AccountAuthentication,
        *,
        operation_id: str,
        expected_person: str,
    ) -> Outcome:
        """Holder-exact unlink through `RegistrationService` — the
        operation binds the account's live identity/ownership/authority
        revisions, and the central service revokes the binding and its
        sessions."""
        checked = await self._principal(authentication)
        if isinstance(checked, Outcome):
            return checked
        binding = checked.binding
        try:
            authority = await self._publication.authority_revision(binding.person_id)
        except Exception:  # noqa: BLE001 — an unreadable authority is unavailable
            authority = None
        if authority is None:
            return unavailable("authority-unavailable")
        try:
            operation = Operation(
                operation_id=operation_id, kind="unlink_account",
                actor=Actor(
                    ActorKind.HOLDER, person_id=binding.person_id,
                    account=authentication.account,
                    proof_issuer=authentication.proof_issuer),
                target=Target(TargetKind.ACCOUNT, account=authentication.account),
                payload=Unlink(expected_person),
                observed_revisions={
                    "identity": binding.person_revision,
                    "ownership": binding.account_revision,
                    "authority": authority,
                })
        except OperationError as error:
            return denied(str(error))
        return await self._registration.apply(operation)

    # -- internals ----------------------------------------------------------

    async def _principal(
        self, authentication: AccountAuthentication
    ) -> AuthenticatedPrincipal | Outcome:
        """Revalidate the retained exact-account binding; session proof is never consent."""
        result = await self._sessions.check(authentication)
        if result.verdict is Verdict.UNAVAILABLE:
            return unavailable("authentication-unavailable")
        if result.principal is None:
            return denied("authentication-refused")
        return result.principal

    async def _read(self, request_id: str) -> PendingLink | Outcome:
        try:
            link = await self._links.get(request_id)
        except Exception:  # noqa: BLE001 — an unreadable store is unavailable
            log.error("link request read unavailable")
            return unavailable("request-unavailable")
        if link is None:
            return denied("unknown-request")
        return link

    @staticmethod
    def _side_of(link: PendingLink, account: Identifier) -> LinkSide | None:
        return next(
            (side for side in LinkSide if link.pair.account(side) == account),
            None)

    async def _view(self, link: PendingLink, side: LinkSide) -> HolderLinkView | Outcome:
        try:
            nonce = await self._links.challenge(link.request_id, side=side)
        except Exception:  # noqa: BLE001 — an unreadable store is unavailable
            return unavailable("challenge-unavailable")
        if nonce is None:
            return unavailable("challenge-unavailable")
        return HolderLinkView(
            request_id=link.request_id, side=side, pair=link.pair,
            surviving_person=link.surviving_person, state=link.state,
            created_at=link.created_at, expires_at=link.expires_at,
            approved=link.approved, nonce=nonce)

    async def _decide(
        self,
        link: PendingLink,
        side: LinkSide,
        kind: str,
        *,
        operation_id: str,
        actor_account: Identifier,
        issuer: str,
        person_id: str,
        proof: ConsentProof,
    ) -> LinkActionResult:
        """Persist one side's decision and centrally publish a fully approved request."""
        payload: object = (
            LinkApproval(request_id=link.request_id, side=side, pair=link.pair,
                         surviving_person=link.surviving_person)
            if kind == "approve_link"
            else LinkRefusal(request_id=link.request_id, side=side, pair=link.pair))
        try:
            operation = Operation(
                operation_id=operation_id, kind=kind,
                actor=Actor(ActorKind.HOLDER, person_id=person_id,
                            account=actor_account, proof_issuer=issuer),
                target=Target(TargetKind.ACCOUNT, account=link.pair.receiving),
                payload=payload, observed_revisions=dict(link.revisions))
            changed = await self._links.decide(operation, proof, now=self._clock())
        except (OperationError, LinkStateError) as error:
            return LinkActionResult(
                denied(str(error) or "decision-refused"), state=link.state)
        except Exception:  # noqa: BLE001 — an unreadable store is unavailable
            log.error("link decision store unavailable")
            return LinkActionResult(unavailable("decision-unavailable"),
                                    state=link.state)
        if not changed.ready:
            return LinkActionResult(complete("recorded"), state=changed.state)
        outcome = await self._publication.commit(link.request_id, now=self._clock())
        try:
            final = await self._links.get(link.request_id)
        except Exception:  # noqa: BLE001 — a state read failure still reports the commit outcome
            final = None
        return LinkActionResult(
            outcome, state=final.state if final is not None else changed.state)

    async def _sender_account(
        self, sender: frozenset[Identifier]
    ) -> ManagedAccount | Outcome:
        """Resolve all transport sender aliases to one exact account; conflicts refuse."""
        records = []
        try:
            for ident in sender:
                bound = await self._bindings.record_for(account=ident)
                if bound is not None:
                    records.append(bound)
        except Exception:  # noqa: BLE001 — an unreadable store is unavailable
            return unavailable("sender-unavailable")
        if not records or len({r.account_id for r in records}) != 1:
            return denied("sender-conflict" if records else "sender-unbound")
        return records[0]

    async def _deliver_signal_challenges(self, link: PendingLink) -> None:
        """Deliver exact-account challenges; failed pushes leave private-view retrieval."""
        if self._signal_delivery is None:
            return
        for side in LinkSide:
            account = link.pair.account(side)
            if account.platform != "signal":
                continue
            nonce = await self._links.challenge(link.request_id, side=side)
            if nonce is None:
                continue
            try:
                await self._signal_delivery.deliver(
                    account=account, text=_challenge_text(link, side, nonce))
            except Exception:  # noqa: BLE001 — a failed push never blocks the pull view
                log.warning(
                    "link challenge delivery failed for request %s side %s",
                    link.request_id, side.value)


def _challenge_text(link: PendingLink, side: LinkSide, nonce: str) -> str:
    """Render the exact pair, survivor, request id and private reply syntax."""
    account = link.pair.account(side)
    other = link.pair.account(
        LinkSide.RECEIVING if side is LinkSide.INITIATING else LinkSide.INITIATING)
    return (
        "Nora account-link request "
        f"{link.request_id}: link {_ident_text(account)} with "
        f"{_ident_text(other)}; the surviving person is "
        f"{link.surviving_person}.\n"
        f"Approve: !!!link approve {link.request_id} {nonce}\n"
        f"Refuse: !!!link refuse {link.request_id} {nonce}\n"
        f"Expires: {link.expires_at.astimezone(UTC).isoformat()}")


def _ident_text(account: Identifier) -> str:
    return f"{account.platform}:{account.kind}:{account.value}"


def _decision_kind(action: str, side: LinkSide) -> str | None:
    """Map a side's verb; only the initiating side can cancel."""
    if action == "approve":
        return "approve_link"
    if action == "cancel" and side is LinkSide.INITIATING:
        return "cancel_link"
    if action == "refuse":
        return "refuse_link" if side is LinkSide.RECEIVING else None
    return None


def _check_outcome(result: CheckResult) -> Outcome:
    if result.verdict is Verdict.DENIED:
        return denied(result.detail or "link-refused")
    return unavailable(result.detail or "link-unavailable")
