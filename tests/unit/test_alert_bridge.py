"""`deploy/alert-bridge/bridge.py` — the last hop, and the one that decides
whether any of the monitoring stack is real.

This file's subject is not "does it format nicely" but "does a failure anywhere on
this path stay visible instead of turning into silence".

Imported by PATH rather than as a package: the bridge is a deployment artifact
with no dependencies on `nora`, deliberately (see its docstring), and importing
it as one would create the coupling it exists to avoid.

No test here reaches Discord. `urlopen` is replaced and the REQUEST is
inspected — the properties worth guarding (which channel, which credential
shape, what happens on a refusal) are all decided before the socket opens, and
a test that needed the network would be one nobody runs.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_BRIDGE = Path(__file__).resolve().parents[2] / "deploy" / "alert-bridge" / "bridge.py"


@pytest.fixture
def bridge():
    spec = importlib.util.spec_from_file_location("alert_bridge", _BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(*alerts: dict, status: str = "firing") -> dict:
    return {"status": status, "alerts": list(alerts)}


def _alert(name: str, **annotations: str) -> dict:
    return {
        "labels": {"alertname": name, "severity": "critical", "job": "nora"},
        "annotations": annotations,
    }


def test_the_message_leads_with_firing_or_resolved(bridge):
    """On a phone screen the only thing that matters first is which of the two
    this is. A message that buries it is one the operator has to read twice at
    3am."""
    text = bridge.render(_payload(_alert("TargetDown", summary="nora is down")))
    assert text.startswith("[FIRING]")
    resolved = bridge.render(
        _payload(_alert("TargetDown", summary="nora is down"), status="resolved"),
    )
    assert resolved.startswith("[RESOLVED]")


def test_it_carries_the_alert_name_and_summary(bridge):
    text = bridge.render(_payload(_alert("HeartbeatStalled", summary="no tick for 6m")))
    assert "HeartbeatStalled" in text
    assert "no tick for 6m" in text


def test_a_description_identical_to_the_summary_is_not_repeated(bridge):
    """Half the rules set both and some set them the same. Printing it twice
    makes every message look padded, which is how people stop reading them."""
    text = bridge.render(
        _payload(_alert("X", summary="the same words", description="the same words")),
    )
    assert text.count("the same words") == 1


def test_a_long_burst_is_truncated_and_says_so(bridge):
    """A silently clipped list is a message that lies about how bad it is."""
    alerts = [_alert(f"Alert{i}", summary=f"s{i}") for i in range(25)]
    text = bridge.render(_payload(*alerts))
    assert "… and 15 more" in text
    assert "Alert0" in text and "Alert24" not in text


def test_a_missing_channel_or_token_is_reported_as_a_failure_not_a_success(
    bridge, monkeypatch, caplog,
):
    """An unset destination must not read as delivered. This is the difference
    between "you have alerting" and "you believe you have alerting", which is
    the state this whole stack exists to end.

    Both halves are checked because they fail for different reasons an operator
    fixes in different places: a channel id is compose configuration, a bot
    token is a credential.
    """
    monkeypatch.setattr(bridge, "TOKEN", "bot-token")
    monkeypatch.setattr(bridge, "CHANNEL", "")
    with caplog.at_level("ERROR"):
        assert bridge.send("anything") is False
    assert any("ALERT_DISCORD_CHANNEL" in r.message for r in caplog.records)

    caplog.clear()
    monkeypatch.setattr(bridge, "CHANNEL", "123")
    monkeypatch.setattr(bridge, "TOKEN", "")
    with caplog.at_level("ERROR"):
        assert bridge.send("anything") is False
    assert any("DISCORD_BOT_TOKEN" in r.message for r in caplog.records)


def _wired(bridge, monkeypatch) -> list[object]:
    """Configure the bridge and capture the request it builds."""
    monkeypatch.setattr(bridge, "CHANNEL", "100000000000000207")
    monkeypatch.setattr(bridge, "TOKEN", "bot-token")
    seen: list[object] = []

    class _Response:
        def read(self) -> bytes: return b"{}"
        def __enter__(self): return self
        def __exit__(self, *_a): return False

    def fake_urlopen(request: object, **_k: object) -> object:
        seen.append(request)
        return _Response()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_a_clean_post_is_a_delivery_and_says_so(bridge, monkeypatch, caplog):
    """The POSITIVE CONTROL. Every assertion in this file is satisfied by a
    `send` that always returns False; this is the one that proves it can
    succeed.

    It also asserts the success LINE: if only failures logged, an empty log would mean both
    "delivered" and "the webhook never arrived".
    """
    _wired(bridge, monkeypatch)
    with caplog.at_level("INFO"):
        assert bridge.send("anything") is True
    assert any("delivered" in r.getMessage() for r in caplog.records), (
        "a successful delivery is silent, so the log cannot distinguish it "
        "from an alert that never arrived"
    )


def test_it_posts_to_the_configured_channel_as_the_bot(bridge, monkeypatch):
    """The token rides an `Authorization: Bot` header and the channel id is in
    the path. Asserted because both are silent when wrong: a bad channel gives
    a 404 that looks like an outage, and a missing `Bot ` prefix gives a 401
    that looks like a bad token."""
    seen = _wired(bridge, monkeypatch)
    assert bridge.send("hello") is True
    request = seen[0]
    assert request.full_url.endswith("/channels/100000000000000207/messages")
    assert request.get_header("Authorization") == "Bot bot-token"
    # Discord blocks urllib's default agent outright.
    assert request.get_header("User-agent")
    assert json.loads(request.data)["content"] == "hello"


def test_an_over_long_message_is_truncated_and_says_so(bridge, monkeypatch):
    """Discord rejects a body over 2000 characters OUTRIGHT. A burst of alerts
    can exceed it, and a 400 would turn "many things broke" into "you were told
    nothing" — at the worst possible moment to lose the message."""
    seen = _wired(bridge, monkeypatch)
    assert bridge.send("x" * 5000) is True
    content = json.loads(seen[0].data)["content"]
    assert len(content) < 2000
    assert content.endswith("… truncated")


def test_a_refusal_from_discord_is_logged_with_discord_s_own_reason(
    bridge, monkeypatch, caplog,
):
    """Discord answers 403 for a bot without permission in that channel, 404
    for a wrong id, 429 when rate-limited — and the BODY carries which.

    **Asserted on the log, not on the return value.** `HTTPError` is a subclass of `URLError`, so
    deleting the dedicated branch entirely still returns `False` through the
    next handler: a `is False` assertion here passes either way and guards
    nothing. What the branch buys is the reason, and without it every one of
    403/404/429 looks identical to an operator — "discord unreachable" for a
    bot that simply lacks permission in the channel.
    """
    import io

    monkeypatch.setattr(bridge, "CHANNEL", "123")
    monkeypatch.setattr(bridge, "TOKEN", "bot-token")

    def refuse(*_a: object, **_k: object) -> None:
        raise bridge.urllib.error.HTTPError(
            "https://discord.com", 403, "Forbidden", {},
            io.BytesIO(b'{"message":"Missing Permissions","code":50013}'),
        )

    monkeypatch.setattr(bridge.urllib.request, "urlopen", refuse)
    with caplog.at_level("ERROR"):
        assert bridge.send("anything") is False
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "403" in messages and "Missing Permissions" in messages, (
        f"the refusal was logged without Discord's reason: {messages!r}"
    )


def test_an_unreachable_discord_returns_false_and_never_raises(bridge, monkeypatch):
    """A bridge that dies on a bad response stops delivering every LATER alert
    too — the failure would spread from one notification to all of them."""
    monkeypatch.setattr(bridge, "CHANNEL", "123")
    monkeypatch.setattr(bridge, "TOKEN", "bot-token")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(bridge.urllib.request, "urlopen", boom)
    assert bridge.send("anything") is False
