"""The wired consent seam: private decision envelopes reach the link service
BEFORE the room parser can drop them, and every other shape falls through
untouched (`account-linking.md`)."""

from __future__ import annotations

from dataclasses import dataclass, field

from nora.domain.identity import Identifier
from nora.domain.managed import Outcome, complete, denied
from nora.domain.models import ConversationTarget
from nora.obs.metrics import Metrics
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.consent import SignalLinkConsent, SignalLinkDelivery

SERVICE = "synthetic-service"
SENDER_UUID = "synthetic-holder-uuid"
SENDER_PHONE = "synthetic-holder-phone"


def _decision_event(body="!!!link approve synthetic-request synthetic-nonce",
                    **envelope):
    env = {
        "sourceUuid": SENDER_UUID, "sourceNumber": SENDER_PHONE,
        "timestamp": 1_700_000_000_000,
        "dataMessage": {"message": body},
    }
    env.update(envelope)
    return {"envelope": env, "account": SERVICE}


def _room_event():
    return {
        "envelope": {
            "sourceUuid": SENDER_UUID, "timestamp": 1_700_000_000_000,
            "dataMessage": {
                "message": "!!!link approve synthetic-request synthetic-nonce",
                "groupInfo": {"groupId": "synthetic-room"},
            },
        },
        "account": SERVICE,
    }


@dataclass
class _Decisions:
    calls: list[dict] = field(default_factory=list)
    outcome: Outcome = field(default_factory=lambda: complete("recorded"))

    async def __call__(self, **kwargs) -> Outcome:
        self.calls.append(kwargs)
        return self.outcome


@dataclass
class _Sends:
    sent: list[tuple[ConversationTarget, str]] = field(default_factory=list)
    ok: bool = True

    async def send(self, target, text, **_):
        self.sent.append((target, text))
        from nora.domain.ports import SendResult
        return SendResult(ok=self.ok)


def _consent(decide, sends):
    return SignalLinkConsent(
        own_account=SERVICE, decide=decide,
        delivery=SignalLinkDelivery("http://daemon", sender=_Sends() if sends is None else sends))


async def test_a_private_decision_reaches_the_service_with_exact_provenance():
    decide = _Decisions()
    sends = _Sends()
    consent = _consent(decide, sends)

    assert await consent.dispatch(_decision_event()) is True
    (call,) = decide.calls
    assert call["sender"] == frozenset({
        Identifier("signal", "uuid", SENDER_UUID),
        Identifier("signal", "phone", SENDER_PHONE),
    })
    assert call["issuer"] == SERVICE
    assert call["request_id"] == "synthetic-request"
    assert call["nonce"] == "synthetic-nonce"
    assert call["approve"] is True
    assert call["timestamp_ms"] == 1_700_000_000_000

    # The private answer goes back to the sender's ACI — never a room.
    (target, text), = sends.sent
    assert target.conversation_type == "dm"
    assert target.conversation_id == SENDER_UUID
    assert "synthetic-request" in text and "synthetic-nonce" not in text


async def test_a_refusal_still_consumes_the_event_and_reports_it_privately():
    decide = _Decisions(outcome=denied("stale"))
    sends = _Sends()
    consent = _consent(decide, sends)

    event = _decision_event(body="!!!link refuse synthetic-request synthetic-nonce")
    assert await consent.dispatch(event) is True
    assert decide.calls[0]["approve"] is False
    (_, text), = sends.sent
    assert "refused" in text


async def test_non_consent_envelopes_are_not_consumed():
    decide = _Decisions()
    sends = _Sends()
    consent = _consent(decide, sends)

    for event in (
        _room_event(),                                                # group
        _decision_event(dataMessage={"message": "just chatting"}),    # not a decision
        {"envelope": {"syncMessage": {}}, "account": SERVICE},        # sync
        _decision_event(dataMessage={
            "message": "!!!link approve synthetic-request synthetic-nonce",
            "quote": {}}),                                            # quoted
    ):
        assert await consent.dispatch(event) is False
    assert decide.calls == [] and sends.sent == []


async def test_a_decider_exception_is_unavailable_never_a_crash_or_allow():
    async def boom(**_):
        raise RuntimeError("store down")

    sends = _Sends()
    consent = _consent(boom, sends)
    assert await consent.dispatch(_decision_event()) is True
    (_, text), = sends.sent
    assert "unavailable" in text


async def test_the_adapter_offers_consent_before_the_room_parser():
    async def always_consent(_event):
        return True

    consumed = SignalAdapter(
        "http://x", SERVICE, metrics=Metrics(), on_consent=always_consent)
    delivered = []

    async def collect(message):
        delivered.append(message)

    await consumed._handle_event(_decision_event(), collect)
    assert delivered == []

    # A consent-shaped GROUP message is refused by the consent parser, so it
    # falls through to the room parser — consent can never hide in a room,
    # and the room keeps working.
    passthrough = SignalAdapter(
        "http://x", SERVICE, metrics=Metrics(),
        on_consent=SignalLinkConsent(
            own_account=SERVICE, decide=_Decisions(),
            delivery=SignalLinkDelivery("http://daemon", sender=_Sends())
        ).dispatch)
    messages = []

    async def collect_room(message):
        messages.append(message)

    await passthrough._handle_event(_room_event(), collect_room)
    assert len(messages) == 1  # reached on_message as an ordinary room message


async def test_a_degraded_signal_sends_no_link_challenge():
    """The managed link challenge has its own `SignalSender`, so it is gated
    on the registry's Signal liveness (platform-liveness.md): degraded sends
    nothing and answers False, which routes the challenge to the
    authenticated view; once live, it sends."""
    from nora.domain.identity import Identifier

    live = {"signal": False}
    sends = _Sends()
    delivery = SignalLinkDelivery("http://daemon", sender=sends,
                                  live=lambda: live["signal"])
    account = Identifier(platform="signal", kind="phone", value="+85290000001")

    assert await delivery.deliver(account=account, text="code") is False
    assert sends.sent == []
    live["signal"] = True
    assert await delivery.deliver(account=account, text="code") is True
    assert len(sends.sent) == 1
