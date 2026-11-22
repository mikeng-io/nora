"""Private link decisions from signal-cli transport envelopes, never room text.

The link service still verifies the nonce, current request, exact account,
revisions and expiry. Parsing a decision grants no authority by itself.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from nora.domain.identity import Identifier
from nora.domain.managed import Outcome, OutcomeStatus
from nora.domain.models import ConversationTarget
from nora.domain.ports import SendResult
from nora.memory import ActAuthority, ActEnvelope, act_event, declare_act
from nora.memory import Outcome as ActOutcome
from nora.platform.bridge.envelope import canonical_digest, canonical_source_key

from .parser import extract_envelope
from .send import SignalSender

log = logging.getLogger(__name__)

_DECISION = re.compile(r"!!!link (approve|refuse) (\S+) (\S+)", re.ASCII)


@dataclass(frozen=True)
class SignalLinkDecision:
    sender: frozenset[Identifier]
    timestamp: int
    request_id: str
    nonce: str = field(repr=False)
    approve: bool


def _matches_service(event: dict, own_account: str) -> bool:
    containers = [event]
    if "method" in event:
        params = event.get("params")
        if not isinstance(params, dict):
            return False
        containers.append(params)
        if "result" in params:
            result = params["result"]
            if not isinstance(result, dict):
                return False
            containers.append(result)
    return all(container.get("account", own_account) == own_account
               for container in containers)


def parse_link_decision(
    event: dict, *, own_account: str,
) -> SignalLinkDecision | None:
    """Read only fresh private data messages on the trusted daemon connection.

    The caller must supply the configured service account, not a client claim.
    Sender aliases are retained together for binding-store conflict checking;
    neither names nor body content can supply a sender identifier.
    """
    if not own_account or not _matches_service(event, own_account):
        return None
    try:
        env = extract_envelope(event)
    except (AttributeError, TypeError):
        return None
    if not isinstance(env, dict) or any(
        key in env for key in ("syncMessage", "editMessage")
    ):
        return None
    data = env.get("dataMessage")
    if not isinstance(data, dict) or any(
        key in data for key in (
            "groupInfo", "quote", "reaction", "remoteDelete", "editMessage",
        )
    ):
        return None
    body = data.get("message")
    if not isinstance(body, str) or (match := _DECISION.fullmatch(body)) is None:
        return None
    timestamp = env.get("timestamp")
    if type(timestamp) is not int or timestamp <= 0:
        return None
    identifiers = frozenset(
        Identifier("signal", kind, value)
        for field, kind in (
            ("sourceNumber", "phone"), ("sourceUuid", "uuid"),
        )
        if isinstance(value := env.get(field), str) and value.strip()
    )
    if not identifiers or any(i.value == own_account for i in identifiers):
        return None
    return SignalLinkDecision(
        sender=identifiers, timestamp=timestamp,
        request_id=match[2], nonce=match[3], approve=match[1] == "approve",
    )


#: Supply daemon-proven aliases, proof issuer and envelope time to the service.
#: Its typed outcome preserves refusal versus transport failure through the reply.
ConsentDecider = Callable[..., Awaitable[Outcome]]


class SignalLinkDelivery:
    """Private challenge delivery over signal-cli (`account-linking.md`).

    The recipient is always an account identifier the binding store already
    owns — the request's bound pair — never `identity.toml`'s direct-message
    allowlist, which exists to stop room-chat DMs, not this narrow consent
    transport. `dm_recipients=None` lifts that other check for exactly this
    reason: the link service supplies a store-bound address, and a
    transport failure answers `False` so the caller can report the
    challenge through the authenticated view instead.
    """

    def __init__(
        self, base_url: str, *, sender: SignalSender | None = None,
        envelope: ActEnvelope | None = None,
    ) -> None:
        self._sender = sender if sender is not None else SignalSender(
            base_url, dm_recipients=None)
        self._envelope = envelope

    async def deliver(self, *, account: Identifier, text: str) -> bool:
        if account.platform != "signal" or not account.value.strip():
            return False
        target = ConversationTarget(
            platform="signal", conversation_id=account.value,
            conversation_type="dm")
        async def send() -> SendResult:
            return await self._sender.send(target, text)

        if self._envelope is None:
            return (await send()).ok
        act = declare_act(
            "signal.private_link", "signal:private-link", event="", authority=ActAuthority(None),
        )
        result = await self._envelope.perform(act, send, lambda sent: ActOutcome(
            "done" if sent.ok else "uncertain" if sent.uncertain else "failed",
        ))
        return result is not None and result.ok


def _reply_address(sender: frozenset[Identifier]) -> Identifier | None:
    """Use the daemon-proven ACI as the private reply address, then the number."""
    for kind in ("uuid", "phone"):
        for ident in sender:
            if ident.kind == kind:
                return ident
    return next(iter(sender), None)


class SignalLinkConsent:
    """Dispatch parsed private decisions to the holder-link service and
    answer privately — the same bound account address, never a room.

    `dispatch` returns True when the event WAS a consent message (whether
    the decision was recorded or refused), so the caller can consume it
    before the ordinary room parser would drop it as a non-room DM.
    """

    def __init__(
        self, *, own_account: str, decide: ConsentDecider,
        delivery: SignalLinkDelivery,
    ) -> None:
        self._own_account = own_account
        self._decide = decide
        self._delivery = delivery

    async def dispatch(self, event: dict) -> bool:
        decision = parse_link_decision(event, own_account=self._own_account)
        if decision is None:
            return False
        try:
            outcome = await self._decide(
                sender=decision.sender, issuer=self._own_account,
                timestamp_ms=decision.timestamp,
                request_id=decision.request_id, nonce=decision.nonce,
                approve=decision.approve)
        except Exception:
            log.exception("link consent dispatch failed")
            outcome = Outcome(OutcomeStatus.UNAVAILABLE, "decision-unavailable")
        if (address := _reply_address(decision.sender)) is not None:
            try:
                event_id = canonical_digest(canonical_source_key(
                    "signal", self._own_account, address.value, "message", str(decision.timestamp),
                ))
                with act_event(event_id, ActAuthority(None)):
                    await self._delivery.deliver(
                        account=address, text=_reply_text(decision, outcome))
            except Exception:  # noqa: BLE001 — a lost reply never re-opens the decision
                log.warning("link consent reply delivery failed")
        return True


def _reply_text(decision: SignalLinkDecision, outcome: Outcome) -> str:
    """Reply with this decision's request id and state, never a nonce or foreign state."""
    verb = "Approval" if decision.approve else "Decision"
    if outcome.status is OutcomeStatus.COMPLETE:
        return f"{verb} recorded for link request {decision.request_id}."
    if outcome.status is OutcomeStatus.DENIED:
        return (f"Link request {decision.request_id} refused: "
                f"{outcome.detail or 'decision-refused'}.")
    return (f"Link request {decision.request_id} is unavailable — "
            "the decision was not recorded; try again later.")
