"""Generated Signal envelopes cannot turn room or quoted text into consent."""

from copy import deepcopy

import pytest

from nora.domain.identity import Identifier
from nora.platform.signal.consent import parse_link_decision

SERVICE = "synthetic-service"
SENDER = "synthetic-holder"


def envelope() -> dict:
    return {
        "sourceUuid": SENDER,
        "timestamp": 123,
        "dataMessage": {"message": "!!!link approve synthetic-request synthetic-nonce"},
    }


@pytest.mark.parametrize("framing", ["sse", "rpc", "subscription"])
def test_exact_transport_sender_survives_both_daemon_framings(framing: str) -> None:
    env = envelope()
    if framing == "sse":
        event = {"envelope": env, "account": SERVICE}
    elif framing == "rpc":
        event = {"method": "receive", "params": {"envelope": env}}
    else:
        event = {"method": "receive", "params": {"result": {"envelope": env}}}
    result = parse_link_decision(event, own_account=SERVICE)
    assert result is not None
    assert result.sender == frozenset({Identifier("signal", "uuid", SENDER)})
    assert (result.request_id, result.nonce, result.approve, result.timestamp) == (
        "synthetic-request", "synthetic-nonce", True, 123,
    )


def test_forwarded_text_keeps_actual_initiator_not_claimed_receiving_holder() -> None:
    env = envelope()
    env["sourceUuid"] = "synthetic-initiator"
    env["sourceName"] = SENDER
    result = parse_link_decision({"envelope": env}, own_account=SERVICE)
    assert result is not None
    assert result.sender == frozenset({Identifier("signal", "uuid", "synthetic-initiator")})


def test_private_refusal_retains_every_transport_alias() -> None:
    env = envelope()
    env["sourceNumber"] = "synthetic-phone"
    env["dataMessage"]["message"] = "!!!link refuse synthetic-request synthetic-nonce"
    result = parse_link_decision({"envelope": env}, own_account=SERVICE)
    assert result is not None and not result.approve
    assert result.sender == frozenset({
        Identifier("signal", "uuid", SENDER),
        Identifier("signal", "phone", "synthetic-phone"),
    })


@pytest.mark.parametrize("key", [
    "groupInfo", "quote", "reaction", "remoteDelete", "editMessage",
])
def test_non_private_or_indirect_messages_cannot_supply_consent(key: str) -> None:
    env = envelope()
    env["dataMessage"][key] = {}
    assert parse_link_decision({"envelope": env}, own_account=SERVICE) is None


@pytest.mark.parametrize("key", ["syncMessage", "editMessage"])
def test_sync_and_edited_envelopes_cannot_supply_consent(key: str) -> None:
    env = envelope()
    env[key] = {}
    assert parse_link_decision({"envelope": env}, own_account=SERVICE) is None


@pytest.mark.parametrize("body", [
    "!!!link approve synthetic-request", "!!!link approve request nonce extra",
    "!!!link approve request nonce\n", "> !!!link approve request nonce",
    "!!!link approve request\nnonce", "!!!link APPROVE request nonce", None,
])
def test_decision_is_an_explicit_whole_message(body: object) -> None:
    env = envelope()
    env["dataMessage"]["message"] = body
    assert parse_link_decision({"envelope": env}, own_account=SERVICE) is None


@pytest.mark.parametrize("timestamp", [None, "123", True, 0, -1])
def test_missing_or_malformed_timestamp_is_not_a_fresh_message(timestamp: object) -> None:
    env = envelope()
    env["timestamp"] = timestamp
    assert parse_link_decision({"envelope": env}, own_account=SERVICE) is None


def test_absent_typed_sender_never_uses_a_name_or_ambiguous_legacy_source() -> None:
    env = envelope()
    del env["sourceUuid"]
    env.update(source=SENDER, sourceName=SENDER)
    assert parse_link_decision({"envelope": env}, own_account=SERVICE) is None


def test_self_send_and_wrong_service_account_are_refused() -> None:
    env = envelope()
    env["sourceUuid"] = SERVICE
    assert parse_link_decision({"envelope": env}, own_account=SERVICE) is None
    env = envelope()
    original = deepcopy(env)
    assert parse_link_decision({"envelope": env, "account": "another-service"},
                               own_account=SERVICE) is None
    assert env == original
    assert parse_link_decision({"envelope": env}, own_account="") is None


@pytest.mark.parametrize("event", [
    {}, {"envelope": []}, {"envelope": {"dataMessage": []}},
    {"method": "receive", "params": []},
    {"method": "typing", "params": {"envelope": envelope()}},
])
def test_malformed_or_other_notifications_are_not_decisions(event: dict) -> None:
    assert parse_link_decision(event, own_account=SERVICE) is None
